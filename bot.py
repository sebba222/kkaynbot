import os
import json
import logging
import re
import time
from datetime import datetime
import pytz
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
UYU_TZ = pytz.timezone("America/Montevideo")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS_VALIDAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]
conversation_history = {}
_cache = {"ts": 0.0, "data": None}  # ts siempre float

# ── Paleta ──────────────────────────────────────────────────────────────────
def C(r,g,b): return {"red":r/255,"green":g/255,"blue":b/255}
AZUL_OSC=C(26,42,78); AZUL_MED=C(52,90,150); AZUL_CLA=C(220,232,247)
TURQUESA=C(0,137,123); VERDE_OSC=C(30,100,50); VERDE_CLA=C(220,245,220)
ROJO_OSC=C(180,28,28); ROJO_CLA=C(255,232,232); GRIS_OSC=C(60,72,80)
GRIS_CLA=C(245,247,248); BLANCO=C(255,255,255); TEXTO_BLA=C(255,255,255)
TEXTO_OSC=C(25,35,50); MORADO=C(74,20,140); MORADO_MED=C(106,27,154)

# ── Normalización ────────────────────────────────────────────────────────────
CUENTA_MAP = {
    "bbva uyu":"BBVA UYU","bbva usd":"BBVA USD",
    "itau uyu":"Itaú UYU","itaú uyu":"Itaú UYU","itaù uyu":"Itaú UYU","itàu uyu":"Itaú UYU",
    "itau usd":"Itaú USD","itaú usd":"Itaú USD","itaù usd":"Itaú USD","itàu usd":"Itaú USD",
    "efectivo uyu":"Efectivo UYU","efectivo usd":"Efectivo USD",
}
def normalize_cuenta(n):
    if not n: return n
    return CUENTA_MAP.get(n.lower().strip(), n)

# ── Google Sheets helpers ────────────────────────────────────────────────────
def get_sheets_client():
    creds = Credentials.from_service_account_info(
        json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON")), scopes=SCOPES)
    return gspread.authorize(creds)

def get_spreadsheet():
    return get_sheets_client().open_by_key(SPREADSHEET_ID)

def get_usd_rate():
    try:
        return requests.get("https://api.exchangerate-api.com/v4/latest/USD",timeout=5).json()["rates"].get("UYU",40.0)
    except: return 40.0

def safe_float(v):
    try: return float(str(v).replace(",",".")) if v else 0.0
    except: return 0.0

def get_balance_from_data(all_data, cuenta):
    """Calcula saldo de una cuenta a partir de datos ya leídos"""
    bal = 0.0
    for row in all_data[3:]:
        if len(row) >= 7 and row[3] == cuenta:
            bal += safe_float(row[5]) - safe_float(row[6])
    return bal

def get_sheets_context(force=False):
    """Lee el Sheets y cachea por 20s. force=True para saltear cache."""
    global _cache
    now_ts = time.time()
    if not force and _cache["data"] and (now_ts - _cache["ts"]) < 20:
        return _cache["data"]
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet("Cuentas")
        all_data = ws.get_all_values()
        saldos = {c: get_balance_from_data(all_data, c) for c in CUENTAS_VALIDAS}
        ultimos = []
        for i, row in enumerate(all_data[3:], start=4):
            if len(row) >= 7 and (row[5] or row[6]):
                ultimos.append({"fila":i,"fecha":row[0],"descripcion":row[1],"categoria":row[2],
                                 "cuenta":row[3],"moneda":row[4],"ingreso":row[5],"egreso":row[6],
                                 "saldo":row[7] if len(row)>7 else ""})
        ultimos = ultimos[-10:]
        ws_inv = ss.worksheet("Inversiones")
        inversiones = [{"activo":r[1],"monto":r[2],"moneda":r[3],"fecha":r[0]}
                       for r in ws_inv.get_all_values()[3:] if len(r)>=4 and r[1]]
        usd_rate = get_usd_rate()
        now_dt = datetime.now(UYU_TZ)
        ing_uyu=eg_uyu=ing_usd=eg_usd=0.0
        for row in all_data[3:]:
            if len(row) >= 7:
                try:
                    f = datetime.strptime(row[0].split(" ")[0], "%d/%m/%Y")
                    if f.month == now_dt.month and f.year == now_dt.year:
                        moneda = row[4] if len(row)>4 else "UYU"
                        if "USD" in moneda: ing_usd+=safe_float(row[5]); eg_usd+=safe_float(row[6])
                        else:               ing_uyu+=safe_float(row[5]); eg_uyu+=safe_float(row[6])
                except: pass
        movs = [r for r in all_data[3:] if len(r)>=7 and (r[5] or r[6])]
        result = {"saldos":saldos,"ultimos":ultimos,"inversiones":inversiones,"usd_rate":usd_rate,
                  "ing_uyu":ing_uyu,"eg_uyu":eg_uyu,"ing_usd":ing_usd,"eg_usd":eg_usd,
                  "all_movs":movs,"all_data":all_data}
        _cache["data"] = result
        _cache["ts"]   = time.time()  # siempre float
        return result
    except Exception as e:
        logger.error(f"Error contexto: {e}")
        return {}

def invalidate_cache():
    _cache["ts"] = 0.0
    _cache["data"] = None  # also clear data to avoid stale datetime objects

# ── Formato Sheets ───────────────────────────────────────────────────────────
def fmt_req(ws_id,r1,c1,r2,c2,bold=False,bg=None,fg=None,size=None,align=None):
    fmt={}; tf={}
    if bold: tf["bold"]=True
    if fg:   tf["foregroundColor"]=fg
    if size: tf["fontSize"]=size
    if tf:   fmt["textFormat"]=tf
    if bg:   fmt["backgroundColor"]=bg
    if align:fmt["horizontalAlignment"]=align
    fmt["verticalAlignment"]="MIDDLE"
    return {"repeatCell":{"range":{"sheetId":ws_id,"startRowIndex":r1-1,"endRowIndex":r2,
            "startColumnIndex":c1-1,"endColumnIndex":c2},"cell":{"userEnteredFormat":fmt},"fields":"userEnteredFormat"}}

def merge_req(ws_id,r1,c1,r2,c2):
    return {"mergeCells":{"range":{"sheetId":ws_id,"startRowIndex":r1-1,"endRowIndex":r2,
            "startColumnIndex":c1-1,"endColumnIndex":c2},"mergeType":"MERGE_ALL"}}

def col_w(ws_id,col,px):
    return {"updateDimensionProperties":{"range":{"sheetId":ws_id,"dimension":"COLUMNS",
            "startIndex":col-1,"endIndex":col},"properties":{"pixelSize":px},"fields":"pixelSize"}}

def row_h(ws_id,row,px):
    return {"updateDimensionProperties":{"range":{"sheetId":ws_id,"dimension":"ROWS",
            "startIndex":row-1,"endIndex":row},"properties":{"pixelSize":px},"fields":"pixelSize"}}

# ── Setup ────────────────────────────────────────────────────────────────────
def setup_sheets():
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]
    # Recrear hojas para evitar conflictos de merge
    # Crear hoja temporal para que nunca quede el spreadsheet vacío
    temp = ss.add_worksheet("_temp_", rows=1, cols=1)
    for title in ["Global","Cuentas","Inversiones"]:
        if title in existing:
            ss.del_worksheet(ss.worksheet(title))
    # Global
    wg = ss.add_worksheet("Global", rows=1000, cols=10)
    wg_id = wg._properties['sheetId']
    wg.batch_update([
        {"range":"A1","values":[["💰  GESTIÓN FINANCIERA — SEBA RODRÍGUEZ"]]},
        {"range":"A2","values":[["Actualizado:",""]]},
        {"range":"A3","values":[["SALDOS TOTALES"]]},
        {"range":"A4","values":[["Total UYU","Total USD","Todo en UYU","Todo en USD","Cotización USD/UYU"]]},
        {"range":"A5","values":[["","","","",""]]},
        {"range":"A7","values":[["RESUMEN DEL MES"]]},
        {"range":"A8","values":[["","PESOS (UYU)","","DÓLARES (USD)",""]]},
        {"range":"A9","values":[["Ingresos","","","",""]]},
        {"range":"A10","values":[["Egresos","","","",""]]},
        {"range":"A11","values":[["Balance","","","",""]]},
        {"range":"A13","values":[["TODOS LOS MOVIMIENTOS"]]},
        {"range":"A14","values":[["FECHA","DESCRIPCIÓN","CATEGORÍA","CUENTA","MONEDA","INGRESO","EGRESO","SALDO"]]},
    ])
    reqs=[
        fmt_req(wg_id,1,1,1,8,bold=True,bg=AZUL_OSC,fg=TEXTO_BLA,size=14,align="CENTER"),merge_req(wg_id,1,1,1,8),row_h(wg_id,1,48),
        fmt_req(wg_id,2,1,2,8,bold=True,bg=AZUL_MED,fg=TEXTO_BLA,size=10,align="LEFT"),row_h(wg_id,2,22),
        fmt_req(wg_id,3,1,3,8,bold=True,bg=TURQUESA,fg=TEXTO_BLA,size=11,align="CENTER"),merge_req(wg_id,3,1,3,8),row_h(wg_id,3,32),
        fmt_req(wg_id,4,1,4,5,bold=True,bg=AZUL_MED,fg=TEXTO_BLA,align="CENTER"),row_h(wg_id,4,26),
        fmt_req(wg_id,5,1,5,5,bold=True,bg=AZUL_CLA,fg=AZUL_OSC,size=11,align="CENTER"),row_h(wg_id,5,30),
        fmt_req(wg_id,6,1,6,8,bg=BLANCO),row_h(wg_id,6,10),
        fmt_req(wg_id,7,1,7,8,bold=True,bg=TURQUESA,fg=TEXTO_BLA,size=11,align="CENTER"),merge_req(wg_id,7,1,7,8),row_h(wg_id,7,32),
        fmt_req(wg_id,8,1,8,5,bold=True,bg=AZUL_MED,fg=TEXTO_BLA,align="CENTER"),
        merge_req(wg_id,8,2,8,3),merge_req(wg_id,8,4,8,5),row_h(wg_id,8,26),
    ]
    for r in [9,10,11]:
        reqs+=[fmt_req(wg_id,r,1,r,1,bold=True,bg=AZUL_CLA,fg=AZUL_OSC,align="LEFT"),
               fmt_req(wg_id,r,2,r,3,bg=GRIS_CLA,fg=TEXTO_OSC,align="CENTER"),
               fmt_req(wg_id,r,4,r,5,bg=GRIS_CLA,fg=TEXTO_OSC,align="CENTER"),
               merge_req(wg_id,r,2,r,3),merge_req(wg_id,r,4,r,5),row_h(wg_id,r,26)]
    reqs+=[
        fmt_req(wg_id,12,1,12,8,bg=BLANCO),row_h(wg_id,12,10),
        fmt_req(wg_id,13,1,13,8,bold=True,bg=TURQUESA,fg=TEXTO_BLA,size=11,align="CENTER"),merge_req(wg_id,13,1,13,8),row_h(wg_id,13,32),
        fmt_req(wg_id,14,1,14,8,bold=True,bg=GRIS_OSC,fg=TEXTO_BLA,align="CENTER"),row_h(wg_id,14,26),
    ]
    for i,w in enumerate([135,220,120,120,75,105,105,110]): reqs.append(col_w(wg_id,i+1,w))
    reqs.append({"updateSheetProperties":{"properties":{"sheetId":wg_id,"gridProperties":{"frozenRowCount":14}},"fields":"gridProperties.frozenRowCount"}})
    ss.batch_update({"requests":reqs})
    # Cuentas
    wc = ss.add_worksheet("Cuentas", rows=1000, cols=8)
    wc_id = wc._properties['sheetId']
    wc.batch_update([
        {"range":"A1","values":[["📋  REGISTRO DE MOVIMIENTOS — TODAS LAS CUENTAS"]]},
        {"range":"A3","values":[["FECHA","DESCRIPCIÓN","CATEGORÍA","CUENTA","MONEDA","INGRESO","EGRESO","SALDO"]]},
    ])
    reqs2=[fmt_req(wc_id,1,1,1,8,bold=True,bg=AZUL_OSC,fg=TEXTO_BLA,size=13,align="CENTER"),
           merge_req(wc_id,1,1,1,8),row_h(wc_id,1,45),
           fmt_req(wc_id,2,1,2,8,bg=BLANCO),row_h(wc_id,2,10),
           fmt_req(wc_id,3,1,3,8,bold=True,bg=GRIS_OSC,fg=TEXTO_BLA,align="CENTER"),row_h(wc_id,3,26),
           {"updateSheetProperties":{"properties":{"sheetId":wc_id,"gridProperties":{"frozenRowCount":3}},"fields":"gridProperties.frozenRowCount"}},]
    for i,w in enumerate([135,220,120,120,75,105,105,110]): reqs2.append(col_w(wc_id,i+1,w))
    ss.batch_update({"requests":reqs2})
    # Inversiones
    wi = ss.add_worksheet("Inversiones", rows=500, cols=7)
    wi_id = wi._properties['sheetId']
    wi.batch_update([
        {"range":"A1","values":[["📈  REGISTRO DE INVERSIONES"]]},
        {"range":"A3","values":[["FECHA","ACTIVO","MONTO","MONEDA","CUENTA ORIGEN","COTIZACIÓN","NOTAS"]]},
    ])
    reqs3=[fmt_req(wi_id,1,1,1,7,bold=True,bg=MORADO,fg=TEXTO_BLA,size=13,align="CENTER"),
           merge_req(wi_id,1,1,1,7),row_h(wi_id,1,45),
           fmt_req(wi_id,2,1,2,7,bg=BLANCO),row_h(wi_id,2,10),
           fmt_req(wi_id,3,1,3,7,bold=True,bg=MORADO_MED,fg=TEXTO_BLA,align="CENTER"),row_h(wi_id,3,26),
           {"updateSheetProperties":{"properties":{"sheetId":wi_id,"gridProperties":{"frozenRowCount":3}},"fields":"gridProperties.frozenRowCount"}},]
    ss.batch_update({"requests":reqs3})
    # Por Cuenta
    # Layout: 3 bancos (BBVA, Itaú, Efectivo) en filas
    # Cada banco tiene 3 secciones: UYU (cols A-F) | SEP | USD (cols H-M) | SEP | TOTAL (cols O-P)
    # Fila 1: título | Fila 2: sub-headers UYU/USD/TOTAL | Fila 3: headers columnas
    # Fila 4+: datos BBVA | Fila N+2: separador | Fila N+3+: datos Itaú | etc.
    if "Por Cuenta" in existing:
        ss.del_worksheet(ss.worksheet("Por Cuenta"))
    wp = ss.add_worksheet("Por Cuenta", rows=500, cols=18)
    wp_id = wp._properties['sheetId']

    reqs_p = []
    # Fila 1: título principal
    reqs_p += [fmt_req(wp_id,1,1,1,18,bold=True,bg=AZUL_OSC,fg=TEXTO_BLA,size=14,align="CENTER"),
               merge_req(wp_id,1,1,1,18), row_h(wp_id,1,48)]
    # Fila 2: sub-headers de secciones (UYU | USD | TOTAL)
    reqs_p += [
        fmt_req(wp_id,2,1,2,6,bold=True,bg=AZUL_MED,fg=TEXTO_BLA,size=11,align="CENTER"), merge_req(wp_id,2,1,2,6),
        fmt_req(wp_id,2,7,2,7,bg=BLANCO), col_w(wp_id,7,12),
        fmt_req(wp_id,2,8,2,13,bold=True,bg=AZUL_MED,fg=TEXTO_BLA,size=11,align="CENTER"), merge_req(wp_id,2,8,2,13),
        fmt_req(wp_id,2,14,2,14,bg=BLANCO), col_w(wp_id,14,12),
        fmt_req(wp_id,2,15,2,18,bold=True,bg=TURQUESA,fg=TEXTO_BLA,size=11,align="CENTER"), merge_req(wp_id,2,15,2,18),
        row_h(wp_id,2,30),
    ]
    # Fila 3: headers de columnas
    H_MOV = ["FECHA","DESCRIPCIÓN","CATEGORÍA","INGRESO","EGRESO","SALDO"]
    H_TOT = ["SAL UYU","SAL USD","TODO UYU","TODO USD"]
    reqs_p += [fmt_req(wp_id,3,1,3,6,bold=True,bg=GRIS_OSC,fg=TEXTO_BLA,align="CENTER"),
               fmt_req(wp_id,3,8,3,13,bold=True,bg=GRIS_OSC,fg=TEXTO_BLA,align="CENTER"),
               fmt_req(wp_id,3,15,3,18,bold=True,bg=GRIS_OSC,fg=TEXTO_BLA,align="CENTER"),
               fmt_req(wp_id,3,7,3,7,bg=BLANCO), fmt_req(wp_id,3,14,3,14,bg=BLANCO),
               row_h(wp_id,3,26)]
    # Anchos columnas UYU (1-6) y USD (8-13)
    for col_offset in [0, 7]:
        for j, w in enumerate([120,180,100,85,85,90]):
            reqs_p.append(col_w(wp_id, 1+col_offset+j, w))
    # Anchos TOTAL (15-18)
    for j, w in enumerate([90,90,90,90]):
        reqs_p.append(col_w(wp_id, 15+j, w))
    # Freeze fila 3
    reqs_p.append({"updateSheetProperties":{"properties":{"sheetId":wp_id,"gridProperties":{"frozenRowCount":3}},"fields":"gridProperties.frozenRowCount"}})
    ss.batch_update({"requests":reqs_p})
    # Escribir contenido fijo
    wp.batch_update([
        {"range":"A1","values":[["📊  MOVIMIENTOS POR CUENTA — BBVA  |  ITAÚ  |  EFECTIVO"]]},
        {"range":"A2","values":[["PESOS (UYU)"]]},
        {"range":"H2","values":[["DÓLARES (USD)"]]},
        {"range":"O2","values":[["TOTALES"]]},
        {"range":"A3","values":[H_MOV]},
        {"range":"H3","values":[H_MOV]},
        {"range":"O3","values":[H_TOT]},
    ])

    for h in ["Sheet1","Hoja 1","Hoja1","_temp_"]:
        try: ss.del_worksheet(ss.worksheet(h))
        except: pass
    invalidate_cache()
    return "✅ Diseño aplicado y todo reseteado. ¡Listo para empezar!"

# ── Update Global ────────────────────────────────────────────────────────────
def update_global_summary():
    """Actualiza la hoja Global con datos frescos del Sheets."""
    try:
        invalidate_cache()
        ctx = get_sheets_context(force=True)
        if not ctx: return
        ss = get_spreadsheet()
        wg = ss.worksheet("Global")
        wc = ss.worksheet("Cuentas")
        wg_id = wg._properties['sheetId']
        wc_id = wc._properties['sheetId']
        saldos   = ctx["saldos"]
        usd_rate = ctx["usd_rate"]
        t_uyu    = sum(v for k,v in saldos.items() if "UYU" in k)
        t_usd    = sum(v for k,v in saldos.items() if "USD" in k)
        now_str  = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
        # Batch update de valores
        wg.batch_update([
            {"range":"B2","values":[[now_str]]},
            {"range":"A5","values":[[f"$ {t_uyu:,.0f}",f"U$S {t_usd:,.2f}",
                                      f"$ {t_uyu+t_usd*usd_rate:,.0f}",
                                      f"U$S {t_uyu/usd_rate+t_usd:,.2f}" if usd_rate else "U$S 0",
                                      f"$ {usd_rate:.2f}"]]},
            {"range":"A9", "values":[["Ingresos",f"$ {ctx['ing_uyu']:,.0f}","",f"U$S {ctx['ing_usd']:,.2f}",""]]},
            {"range":"A10","values":[["Egresos", f"$ {ctx['eg_uyu']:,.0f}","",f"U$S {ctx['eg_usd']:,.2f}",""]]},
            {"range":"A11","values":[["Balance", f"$ {ctx['ing_uyu']-ctx['eg_uyu']:,.0f}","",f"U$S {ctx['ing_usd']-ctx['eg_usd']:,.2f}",""]]},
        ])
        movs     = ctx["all_movs"]
        movs_inv = list(reversed(movs))
        reqs = []
        # Colorear fila 11 (balance) según positivo/negativo
        bg_uyu = VERDE_CLA if ctx['ing_uyu']>=ctx['eg_uyu'] else ROJO_CLA
        fg_uyu = VERDE_OSC if ctx['ing_uyu']>=ctx['eg_uyu'] else ROJO_OSC
        bg_usd = VERDE_CLA if ctx['ing_usd']>=ctx['eg_usd'] else ROJO_CLA
        fg_usd = VERDE_OSC if ctx['ing_usd']>=ctx['eg_usd'] else ROJO_OSC
        reqs += [fmt_req(wg_id,11,2,11,3,bold=True,bg=bg_uyu,fg=fg_uyu,align="CENTER"),
                 fmt_req(wg_id,11,4,11,5,bold=True,bg=bg_usd,fg=fg_usd,align="CENTER")]
        if movs_inv:
            # Limpiar filas anteriores de movimientos en Global
            wg.batch_clear([f"A15:H{14+len(movs_inv)+5}"])
            wg.update(values=movs_inv, range_name=f"A15")
            for i, row in enumerate(movs_inv):
                es_ing = bool(row[5]) if len(row)>5 else False
                es_eg  = bool(row[6]) if len(row)>6 else False
                fi = 15 + i
                if es_ing and not es_eg:   bg,fg = VERDE_CLA,VERDE_OSC
                elif es_eg and not es_ing: bg,fg = ROJO_CLA,ROJO_OSC
                else:                      bg,fg = GRIS_CLA,TEXTO_OSC
                reqs.append(fmt_req(wg_id,fi,1,fi,8,bg=bg,fg=fg,align="CENTER"))
        # Colorear filas en Cuentas (desde fila 4)
        for i, row in enumerate(movs):
            es_ing = bool(row[5]) if len(row)>5 else False
            es_eg  = bool(row[6]) if len(row)>6 else False
            fi = 4 + i
            if es_ing and not es_eg:   bg,fg = VERDE_CLA,VERDE_OSC
            elif es_eg and not es_ing: bg,fg = ROJO_CLA,ROJO_OSC
            else:                      bg,fg = GRIS_CLA,TEXTO_OSC
            reqs.append(fmt_req(wc_id,fi,1,fi,8,bg=bg,fg=fg,align="CENTER"))
        if reqs:
            ss.batch_update({"requests":reqs})
        invalidate_cache()
        # También actualizar pestaña Por Cuenta
        try:
            update_por_cuenta()
        except Exception as e:
            logger.warning(f"update_por_cuenta: {e}")
    except Exception as e:
        logger.error(f"Error update_global: {e}")

def update_por_cuenta():
    """Actualiza la pestaña Por Cuenta con movimientos separados por cuenta."""
    try:
        ctx = get_sheets_context()
        if not ctx: return
        ss = get_spreadsheet()
        wp = ss.worksheet("Por Cuenta")
        wp_id = wp._properties['sheetId']
        all_movs = ctx["all_movs"]
        CUENTAS = ["BBVA UYU","BBVA USD","Itaú UYU","Itaú USD","Efectivo UYU","Efectivo USD"]
        col_starts = [1, 8, 15]
        # Limpiar datos previos (desde fila 4 para bloque 1, fila 7 para bloque 2)
        wp.batch_clear(["A4:F200","H4:M200","O4:T200","A7:F200","H7:M200","O7:T200"])
        reqs = []
        for bloque in range(2):
            cuentas_bloque = CUENTAS[bloque*3:(bloque+1)*3]
            data_start_row = 4 if bloque == 0 else 7
            for ci, (cuenta, col) in enumerate(zip(cuentas_bloque, col_starts)):
                movs_cuenta = [[r[0],r[1],r[2],r[5],r[6],r[7]] 
                               for r in all_movs if r[3]==cuenta]
                if movs_cuenta:
                    col_letter = chr(64+col)
                    end_col_letter = chr(64+col+5)
                    wp.update(values=movs_cuenta, range_name=f"{col_letter}{data_start_row}")
                    for ri, row in enumerate(movs_cuenta):
                        es_ing = bool(row[3])
                        es_eg  = bool(row[4])
                        fi = data_start_row + ri
                        if es_ing and not es_eg:   bg,fg = VERDE_CLA,VERDE_OSC
                        elif es_eg and not es_ing: bg,fg = ROJO_CLA,ROJO_OSC
                        else:                      bg,fg = GRIS_CLA,TEXTO_OSC
                        reqs.append(fmt_req(wp_id,fi,col,fi,col+5,bg=bg,fg=fg,align="CENTER"))
        if reqs:
            ss.batch_update({"requests":reqs})
    except Exception as e:
        logger.error(f"Error update_por_cuenta: {e}")

# ── Groq ─────────────────────────────────────────────────────────────────────
def call_groq(messages):
    resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
        json={"model":"llama-3.3-70b-versatile","messages":messages,"temperature":0.1,"max_tokens":1000},
        timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Groq error {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"].strip()

# ── Execute Action ────────────────────────────────────────────────────────────
def execute_action(action):
    tipo = action.get("tipo")
    ss   = get_spreadsheet()
    ws   = ss.worksheet("Cuentas")
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    # Leer datos frescos una sola vez
    all_data = ws.get_all_values()

    if tipo == "gasto":
        cuenta = normalize_cuenta(action["cuenta"])
        monto  = float(action["monto"])
        moneda = action.get("moneda","UYU")
        saldo  = get_balance_from_data(all_data, cuenta) - monto
        ws.append_row([fecha, action["descripcion"], action.get("categoria","Otro"), cuenta, moneda, "", monto, round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Gasto registrado*\n📝 {action['descripcion']}\n💸 {sym} {monto:,.2f} | {action.get('categoria','Otro')}\n🏦 {cuenta}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "ingreso":
        cuenta = normalize_cuenta(action["cuenta"])
        monto  = float(action["monto"])
        moneda = action.get("moneda","UYU")
        saldo  = get_balance_from_data(all_data, cuenta) + monto
        ws.append_row([fecha, action["descripcion"], action.get("categoria","Sueldo"), cuenta, moneda, monto, "", round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Ingreso registrado*\n📝 {action['descripcion']}\n💚 {sym} {monto:,.2f} | {action.get('categoria','Ingreso')}\n🏦 {cuenta}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "transferencia":
        origen  = normalize_cuenta(action["cuenta_origen"])
        destino = normalize_cuenta(action["cuenta_destino"])
        monto   = float(action["monto"])
        moneda  = action.get("moneda","UYU")
        s_orig  = get_balance_from_data(all_data, origen)  - monto
        s_dest  = get_balance_from_data(all_data, destino) + monto
        ws.append_row([fecha, f"Transferencia a {destino}",    "Transferencia", origen,  moneda, "",    monto, round(s_orig,2)])
        ws.append_row([fecha, f"Transferencia desde {origen}", "Transferencia", destino, moneda, monto, "",    round(s_dest,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Transferencia*\n📤 {origen}: {sym} {s_orig:,.2f}\n📥 {destino}: {sym} {s_dest:,.2f}\n💱 {sym} {monto:,.2f}"

    elif tipo == "inversion":
        activo     = action["activo"]
        monto      = float(action["monto"])
        moneda     = action.get("moneda","USD")
        cuenta_orig = normalize_cuenta(action["cuenta"])
        usd_rate   = get_usd_rate()
        ws_inv = ss.worksheet("Inversiones")
        ws_inv.append_row([fecha, activo, monto, moneda, cuenta_orig, usd_rate, action.get("descripcion","")])
        saldo = get_balance_from_data(all_data, cuenta_orig) - monto
        ws.append_row([fecha, f"Inversión en {activo}", "Inversión", cuenta_orig, moneda, "", monto, round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Inversión*\n📈 {activo}\n💸 {sym} {monto:,.2f}\n🏦 {cuenta_orig}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "eliminar":
        fila = action.get("fila")
        if fila:
            fila_int = int(fila)
            if fila_int <= len(all_data):
                desc = all_data[fila_int-1][1] if len(all_data[fila_int-1])>1 else "movimiento"
                ws.delete_rows(fila_int)
                update_global_summary()
                return f"✅ *Eliminado*: {desc}"
        return "❌ No pude identificar qué eliminar."

    elif tipo == "editar":
        fila = action.get("fila")
        if fila:
            fila_int = int(fila)
            if fila_int <= len(all_data):
                row = all_data[fila_int - 1]
                desc_orig = row[1] if len(row)>1 else "movimiento"
                es_ingreso = bool(row[5]) if len(row)>5 else False
                updates = []
                if "monto" in action:
                    nuevo_monto = float(action["monto"])
                    if es_ingreso:
                        updates.append({"range": f"F{fila_int}", "values": [[nuevo_monto]]})
                        updates.append({"range": f"G{fila_int}", "values": [[""]]})
                    else:
                        updates.append({"range": f"F{fila_int}", "values": [[""]]})
                        updates.append({"range": f"G{fila_int}", "values": [[nuevo_monto]]})
                if "descripcion" in action:
                    updates.append({"range": f"B{fila_int}", "values": [[action["descripcion"]]]})
                if "categoria" in action:
                    updates.append({"range": f"C{fila_int}", "values": [[action["categoria"]]]})
                if "cuenta" in action:
                    updates.append({"range": f"D{fila_int}", "values": [[normalize_cuenta(action["cuenta"])]]})
                if updates:
                    ws.batch_update(updates)
                # Recalcular saldos de todas las cuentas afectadas
                time.sleep(1)
                fresh = ws.get_all_values()
                saldo_por_cuenta = {}
                cell_updates = []
                for idx in range(3, len(fresh)):
                    r = fresh[idx]
                    if len(r) >= 7 and r[3]:
                        c = r[3]
                        if c not in saldo_por_cuenta:
                            saldo_por_cuenta[c] = 0.0
                        ing = safe_float(r[5]); eg = safe_float(r[6])
                        saldo_por_cuenta[c] += ing - eg
                        cell_updates.append({"range": f"H{idx+1}", "values": [[round(saldo_por_cuenta[c], 2)]]})
                if cell_updates:
                    # Batch en grupos de 50 para no superar límites
                    for i in range(0, len(cell_updates), 50):
                        ws.batch_update(cell_updates[i:i+50])
                        if i + 50 < len(cell_updates): time.sleep(1)
                update_global_summary()
                return f"✅ *Editado*: {action.get('descripcion', desc_orig)}"
        return "❌ No pude identificar qué editar."

    elif tipo == "actualizar_saldo":
        cuenta = normalize_cuenta(action["cuenta"])
        nuevo  = float(action["saldo"])
        actual = get_balance_from_data(all_data, cuenta)
        diff   = nuevo - actual
        moneda = "USD" if "USD" in cuenta else "UYU"
        if diff > 0:   ws.append_row([fecha,"Ajuste de saldo","Ajuste",cuenta,moneda,diff,"",nuevo])
        elif diff < 0: ws.append_row([fecha,"Ajuste de saldo","Ajuste",cuenta,moneda,"",abs(diff),nuevo])
        update_global_summary()
        sym = "$" if "UYU" in cuenta else "U$S"
        return f"✅ *Saldo actualizado*\n🏦 {cuenta}: {sym} {nuevo:,.2f}"

    elif tipo == "resumen":
        ctx = get_sheets_context()
        saldos   = ctx.get("saldos",{})
        usd_rate = ctx.get("usd_rate",40)
        now      = datetime.now(UYU_TZ)
        t_uyu    = sum(v for k,v in saldos.items() if "UYU" in k)
        t_usd    = sum(v for k,v in saldos.items() if "USD" in k)
        lines = ["📊 *RESUMEN GLOBAL*", f"📅 {now.strftime('%d/%m/%Y %H:%M')}", "", "💰 *Saldos:*"]
        for c in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in c else "U$S"
            lines.append(f"  • {c}: {sym} {saldos.get(c,0):,.2f}")
        lines += ["","📈 *Totales:*",
            f"  • UYU: $ {t_uyu:,.2f}",f"  • USD: U$S {t_usd:,.2f}",
            f"  • Todo en UYU: $ {t_uyu+t_usd*usd_rate:,.2f}",
            f"  • Todo en USD: U$S {t_uyu/usd_rate+t_usd:,.2f}" if usd_rate else "  • Todo en USD: U$S 0",
            f"  • Cotización: $ {usd_rate:.2f}",
            "","📅 *Este mes:*",
            f"  • Ingresos UYU: $ {ctx.get('ing_uyu',0):,.2f}",
            f"  • Egresos UYU: $ {ctx.get('eg_uyu',0):,.2f}",
            f"  • Balance UYU: $ {ctx.get('ing_uyu',0)-ctx.get('eg_uyu',0):,.2f}",
            f"  • Ingresos USD: U$S {ctx.get('ing_usd',0):,.2f}",
            f"  • Egresos USD: U$S {ctx.get('eg_usd',0):,.2f}"]
        return "\n".join(lines)

    return "❌ No entendí la operación."

# ── Process Message ───────────────────────────────────────────────────────────
async def process_message(update: Update, user_message: str):
    user_id = update.effective_user.id
    ctx = get_sheets_context()
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    system = f"""Sos KkaynBot, el asistente financiero de Seba (Uruguay). Español rioplatense.

ESTADO ACTUAL DEL EXCEL:
Saldos: {json.dumps(ctx.get('saldos',{}),ensure_ascii=False)}
Últimos movimientos: {json.dumps(ctx.get('ultimos',[]),ensure_ascii=False)}
Inversiones: {json.dumps(ctx.get('inversiones',[]),ensure_ascii=False)}
Cotización USD/UYU: {ctx.get('usd_rate',40)}
Ingresos mes UYU: {ctx.get('ing_uyu',0)} | Egresos mes UYU: {ctx.get('eg_uyu',0)}
Ingresos mes USD: {ctx.get('ing_usd',0)} | Egresos mes USD: {ctx.get('eg_usd',0)}
CUENTAS: {', '.join(CUENTAS_VALIDAS)}

Respondé SOLO con JSON válido:
- Acción única:    {{"accion": {{...}}, "respuesta": "..."}}
- Varias acciones: {{"acciones": [{{...}},...], "respuesta": "..."}}
- Solo consulta:   {{"accion": null, "respuesta": "..."}}

Tipos de acción:
- gasto:           {{"tipo":"gasto","cuenta":"BBVA UYU","monto":500,"moneda":"UYU","descripcion":"súper","categoria":"Alimentación"}}
- ingreso:         {{"tipo":"ingreso","cuenta":"BBVA UYU","monto":50000,"moneda":"UYU","descripcion":"sueldo","categoria":"Sueldo"}}
- transferencia:   {{"tipo":"transferencia","cuenta_origen":"BBVA UYU","cuenta_destino":"Itaú UYU","monto":10000,"moneda":"UYU"}}
- inversion:       {{"tipo":"inversion","activo":"BTC","cuenta":"Itaú USD","monto":200,"moneda":"USD"}}
- eliminar:        {{"tipo":"eliminar","fila":N}} — N viene de "ultimos" (campo "fila")
- editar:          {{"tipo":"editar","fila":N,"monto":48000}} o {{"tipo":"editar","fila":N,"categoria":"..."}} o {{"tipo":"editar","fila":N,"descripcion":"..."}}
- actualizar_saldo:{{"tipo":"actualizar_saldo","cuenta":"BBVA UYU","saldo":5000}} — SOLO si el usuario da un número explícito
- resumen:         {{"tipo":"resumen"}}

REGLAS IMPORTANTES:
- "saldo en X", "cuánto tengo", "cómo estoy en X" = CONSULTA, nunca acción
- actualizar_saldo SOLO con número explícito del usuario. Sin número = consulta
- Si el usuario corrige un monto ("fueron 3k no 5k") → usar "editar" con la fila correcta
- "el último/ese/lo que pusiste" → identificar fila en "ultimos"
- Si falta info crítica, preguntar antes de ejecutar
- Para múltiples cuentas en 0 → usar "acciones" con lista de actualizar_saldo
- SOLO JSON, sin texto extra"""

    conversation_history[user_id].append({"role":"user","content":user_message})
    if len(conversation_history[user_id]) > 10:
        conversation_history[user_id] = conversation_history[user_id][-10:]

    raw = call_groq([{"role":"system","content":system}] + conversation_history[user_id])
    raw = re.sub(r'```json\s*','',raw); raw = re.sub(r'```\s*','',raw)
    parsed = json.loads(raw)
    conversation_history[user_id].append({"role":"assistant","content":raw})

    acciones = parsed.get("acciones")
    accion   = parsed.get("accion")
    respuesta = parsed.get("respuesta","")

    if acciones and isinstance(acciones, list):
        resultados = []
        for a in acciones:
            try:
                r = execute_action(a)
                if r: resultados.append(r)
            except Exception as e:
                resultados.append(f"❌ Error: {e}")
        if len(resultados) > 3:
            return f"✅ *{len(resultados)} operaciones ejecutadas.*\n{respuesta}"
        return "\n\n".join(resultados) if resultados else respuesta
    elif accion:
        resultado = execute_action(accion)
        return resultado if resultado else respuesta
    return respuesta

# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ Sin acceso."); return
    await update.message.reply_text(
        "👋 *KkaynBot* — tu asistente financiero\\.\n\n"
        "Hablame natural:\n• _cobré sueldo 50k en BBVA_\n• _gasté 500 en súper con Itaú_\n"
        "• _pasé 10k de BBVA a Itaú_\n• _el sueldo fueron 48k no 50k_\n• _¿cuánto tengo en BBVA?_\n\n"
        "Comandos: /resumen /saldo /setup /limpiar", parse_mode="MarkdownV2")

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    await update.message.reply_text("⚙️ Aplicando diseño y reseteando...")
    try:
        await update.message.reply_text(setup_sheets())
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    await update.message.reply_text("🔄 Calculando...")
    try:
        await update.message.reply_text(execute_action({"tipo":"resumen"}), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    try:
        ctx = get_sheets_context(force=True)
        saldos = ctx.get("saldos",{}); rate = ctx.get("usd_rate",40)
        lines = ["💳 *SALDOS ACTUALES*\n"]
        for c in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in c else "U$S"
            lines.append(f"• {c}: {sym} {saldos.get(c,0):,.2f}")
        lines.append(f"\n💱 1 USD = $ {rate:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    conversation_history[update.effective_user.id] = []
    await update.message.reply_text("🧹 Historial limpiado.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    await update.message.reply_text("🤔 Procesando...")
    try:
        result = await process_message(update, update.message.text.strip())
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ {e}")

async def send_weekly_report(app):
    try:
        await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,
            text="📅 *REPORTE SEMANAL*\n\n"+execute_action({"tipo":"resumen"}), parse_mode="Markdown")
    except Exception as e: logger.error(f"Reporte: {e}")

async def check_low_balance(app):
    try:
        ctx = get_sheets_context(force=True)
        MIN_UYU = float(os.environ.get("MIN_BALANCE_UYU","500"))
        MIN_USD = float(os.environ.get("MIN_BALANCE_USD","50"))
        alerts = []
        for c,s in ctx.get("saldos",{}).items():
            if "UYU" in c and 0<s<MIN_UYU: alerts.append(f"⚠️ {c}: $ {s:,.2f}")
            elif "USD" in c and 0<s<MIN_USD: alerts.append(f"⚠️ {c}: U$S {s:,.2f}")
        if alerts:
            await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,
                text="🚨 *SALDO BAJO*\n\n"+"\n".join(alerts), parse_mode="Markdown")
    except Exception as e: logger.error(f"Balance check: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("setup",   cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo",   cmd_saldo))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    scheduler = AsyncIOScheduler(timezone=UYU_TZ)
    scheduler.add_job(send_weekly_report, "cron", day_of_week="mon", hour=9,  minute=0, args=[app])
    scheduler.add_job(check_low_balance,  "cron", hour=8,            minute=0, args=[app])
    scheduler.start()
    logger.info("🤖 KkaynBot v4 iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
