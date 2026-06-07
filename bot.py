import os
import json
import logging
import re
from datetime import datetime
import time
import pytz
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
UYU_TZ = pytz.timezone("America/Montevideo")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS_VALIDAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]
conversation_history = {}
_sheets_cache = {"data": None, "ts": 0}

def normalize_cuenta(nombre):
    """Normaliza el nombre de cuenta para evitar variaciones de tildes/mayúsculas"""
    if not nombre:
        return nombre
    # Mapa de variaciones conocidas
    variaciones = {
        "bbva uyu": "BBVA UYU", "bbva usd": "BBVA USD",
        "itau uyu": "Itaú UYU", "itaú uyu": "Itaú UYU", "itàu uyu": "Itaú UYU", "itaù uyu": "Itaú UYU",
        "itau usd": "Itaú USD", "itaú usd": "Itaú USD", "itàu usd": "Itaú USD", "itaù usd": "Itaú USD",
        "efectivo uyu": "Efectivo UYU", "efectivo usd": "Efectivo USD",
    }
    key = nombre.lower().strip()
    return variaciones.get(key, nombre)

def C(r, g, b):
    return {"red": r/255, "green": g/255, "blue": b/255}

# Paleta
AZUL_OSC   = C(26,42,78)
AZUL_MED   = C(52,90,150)
AZUL_CLA   = C(220,232,247)
TURQUESA   = C(0,137,123)
TURQ_CLA   = C(224,247,245)
VERDE_OSC  = C(30,100,50)
VERDE_CLA  = C(220,245,220)
ROJO_OSC   = C(180,28,28)
ROJO_CLA   = C(255,232,232)
GRIS_OSC   = C(60,72,80)
GRIS_CLA   = C(245,247,248)
BLANCO     = C(255,255,255)
TEXTO_BLA  = C(255,255,255)
TEXTO_OSC  = C(25,35,50)
MORADO     = C(74,20,140)
MORADO_MED = C(106,27,154)

def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def sheets_retry(func, *args, retries=3, **kwargs):
    """Ejecuta una operación de Sheets con retry automático ante 429"""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "RATE_LIMIT" in str(e):
                if attempt < retries - 1:
                    wait = (attempt + 1) * 15  # 15s, 30s, 45s
                    logger.warning(f"Rate limit hit, esperando {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise

def get_usd_rate():
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        return r.json()["rates"].get("UYU", 40.0)
    except:
        return 40.0

def get_spreadsheet():
    return get_sheets_client().open_by_key(SPREADSHEET_ID)

def get_balance(ws, cuenta):
    try:
        balance = 0.0
        for row in ws.get_all_values()[3:]:
            if len(row) >= 8 and row[3] == cuenta:
                balance += (float(row[5].replace(',','.')) if row[5] else 0) - (float(row[6].replace(',','.')) if row[6] else 0)
        return balance
    except:
        return 0.0

def get_sheets_context(force_refresh=False):
    import time as _time
    global _sheets_cache
    now = _time.time()
    if not force_refresh and _sheets_cache["data"] and (now - _sheets_cache["ts"]) < 30:
        return _sheets_cache["data"]
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet("Cuentas")
        all_data = ws.get_all_values()
        saldos = {c: get_balance(ws, c) for c in CUENTAS_VALIDAS}
        ultimos = []
        for i, row in enumerate(all_data[3:], start=4):
            if len(row) >= 7 and (row[5] or row[6]):
                ultimos.append({"fila": i, "fecha": row[0], "descripcion": row[1], "categoria": row[2], "cuenta": row[3], "moneda": row[4], "ingreso": row[5], "egreso": row[6], "saldo": row[7] if len(row) > 7 else ""})
        ultimos = ultimos[-10:]
        ws_inv = ss.worksheet("Inversiones")
        inversiones = [{"activo": r[1], "monto": r[2], "moneda": r[3], "fecha": r[0]} for r in ws_inv.get_all_values()[3:] if len(r) >= 4 and r[1]]
        usd_rate = get_usd_rate()
        now = datetime.now(UYU_TZ)
        ing_mes = eg_mes = 0.0
        for row in all_data[3:]:
            if len(row) >= 7:
                try:
                    f = datetime.strptime(row[0].split(" ")[0], "%d/%m/%Y")
                    if f.month == now.month and f.year == now.year:
                        if row[5]: ing_mes += float(row[5].replace(',','.'))
                        if row[6]: eg_mes += float(row[6].replace(',','.'))
                except: pass
        result = {"saldos": saldos, "ultimos_movimientos": ultimos, "inversiones": inversiones, "usd_rate": usd_rate, "ingresos_mes": ing_mes, "egresos_mes": eg_mes, "balance_mes": ing_mes - eg_mes, "all_movimientos": [r for r in all_data[3:] if len(r) >= 7 and (r[5] or r[6])]}
        _sheets_cache["data"] = result
        _sheets_cache["ts"] = now
        return result
    except Exception as e:
        logger.error(f"Error contexto: {e}")
        return {}

def call_groq(messages):
    resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": messages, "temperature": 0.1, "max_tokens": 1000},
        timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Groq error {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"].strip()

def fmt_req(ws_id, r1, c1, r2, c2, bold=False, bg=None, fg=None, size=None, align=None, italic=False):
    fmt = {}
    tf = {}
    if bold: tf["bold"] = True
    if italic: tf["italic"] = True
    if fg: tf["foregroundColor"] = fg
    if size: tf["fontSize"] = size
    if tf: fmt["textFormat"] = tf
    if bg: fmt["backgroundColor"] = bg
    if align: fmt["horizontalAlignment"] = align
    fmt["verticalAlignment"] = "MIDDLE"
    return {"repeatCell": {"range": {"sheetId": ws_id, "startRowIndex": r1-1, "endRowIndex": r2, "startColumnIndex": c1-1, "endColumnIndex": c2}, "cell": {"userEnteredFormat": fmt}, "fields": "userEnteredFormat"}}

def merge_req(ws_id, r1, c1, r2, c2):
    return {"mergeCells": {"range": {"sheetId": ws_id, "startRowIndex": r1-1, "endRowIndex": r2, "startColumnIndex": c1-1, "endColumnIndex": c2}, "mergeType": "MERGE_ALL"}}

def col_w(ws_id, col, px):
    return {"updateDimensionProperties": {"range": {"sheetId": ws_id, "dimension": "COLUMNS", "startIndex": col-1, "endIndex": col}, "properties": {"pixelSize": px}, "fields": "pixelSize"}}

def row_h(ws_id, row, px):
    return {"updateDimensionProperties": {"range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": row-1, "endIndex": row}, "properties": {"pixelSize": px}, "fields": "pixelSize"}}

def setup_sheets():
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]

    # ── GLOBAL ──
    # Eliminar y recrear para evitar conflictos con merges anteriores
    if "Global" in existing:
        ss.del_worksheet(ss.worksheet("Global"))
    ws = ss.add_worksheet(title="Global", rows=1000, cols=10)
    ws_id = ws._properties['sheetId']

    # Batch all content updates into one call
    ws.batch_update([
        {"range": "A1", "values": [["💰  GESTIÓN FINANCIERA — SEBA RODRÍGUEZ"]]},
        {"range": "A2", "values": [["Actualizado:", ""]]},
        {"range": "A3", "values": [["SALDOS TOTALES"]]},
        {"range": "A4", "values": [["Total UYU", "Total USD", "Todo en UYU", "Todo en USD", "Cotización USD/UYU"]]},
        {"range": "A5", "values": [["", "", "", "", ""]]},
        {"range": "A7", "values": [["RESUMEN DEL MES"]]},
        {"range": "A8", "values": [["", "PESOS (UYU)", "", "DÓLARES (USD)", ""]]},
        {"range": "A9", "values": [["Ingresos", "", "", "", ""]]},
        {"range": "A10", "values": [["Egresos", "", "", "", ""]]},
        {"range": "A11", "values": [["Balance", "", "", "", ""]]},
        {"range": "A13", "values": [["TODOS LOS MOVIMIENTOS"]]},
        {"range": "A14", "values": [["FECHA", "DESCRIPCIÓN", "CATEGORÍA", "CUENTA", "MONEDA", "INGRESO", "EGRESO", "SALDO"]]},
    ])

    reqs = []
    # Fila 1 título
    reqs += [fmt_req(ws_id,1,1,1,8, bold=True, bg=AZUL_OSC, fg=TEXTO_BLA, size=14, align="CENTER"), merge_req(ws_id,1,1,1,8), row_h(ws_id,1,48)]
    # Fila 2 actualizado
    reqs += [fmt_req(ws_id,2,1,2,8, bold=True, bg=AZUL_MED, fg=TEXTO_BLA, size=10, align="LEFT"), row_h(ws_id,2,22)]
    # Fila 3 SALDOS TOTALES header
    reqs += [fmt_req(ws_id,3,1,3,8, bold=True, bg=TURQUESA, fg=TEXTO_BLA, size=11, align="CENTER"), merge_req(ws_id,3,1,3,8), row_h(ws_id,3,32)]
    # Fila 4 sub-headers totales
    reqs += [fmt_req(ws_id,4,1,4,5, bold=True, bg=AZUL_MED, fg=TEXTO_BLA, align="CENTER"), row_h(ws_id,4,26)]
    # Fila 5 valores totales
    reqs += [fmt_req(ws_id,5,1,5,5, bold=True, bg=AZUL_CLA, fg=AZUL_OSC, size=11, align="CENTER"), row_h(ws_id,5,30)]
    # Fila 6 espacio
    reqs += [fmt_req(ws_id,6,1,6,8, bg=BLANCO), row_h(ws_id,6,10)]
    # Fila 7 RESUMEN DEL MES header
    reqs += [fmt_req(ws_id,7,1,7,8, bold=True, bg=TURQUESA, fg=TEXTO_BLA, size=11, align="CENTER"), merge_req(ws_id,7,1,7,8), row_h(ws_id,7,32)]
    # Fila 8 sub-headers UYU/USD
    reqs += [fmt_req(ws_id,8,1,8,5, bold=True, bg=AZUL_MED, fg=TEXTO_BLA, align="CENTER"), merge_req(ws_id,8,2,8,3), merge_req(ws_id,8,4,8,5), row_h(ws_id,8,26)]
    # Filas 9-11 datos del mes
    for r in [9,10,11]:
        reqs += [fmt_req(ws_id,r,1,r,1, bold=True, bg=AZUL_CLA, fg=AZUL_OSC, align="LEFT"), fmt_req(ws_id,r,2,r,3, bg=GRIS_CLA, fg=TEXTO_OSC, align="CENTER"), fmt_req(ws_id,r,4,r,5, bg=GRIS_CLA, fg=TEXTO_OSC, align="CENTER"), merge_req(ws_id,r,2,r,3), merge_req(ws_id,r,4,r,5), row_h(ws_id,r,26)]
    # Fila 12 espacio
    reqs += [fmt_req(ws_id,12,1,12,8, bg=BLANCO), row_h(ws_id,12,10)]
    # Fila 13 TODOS LOS MOVIMIENTOS
    reqs += [fmt_req(ws_id,13,1,13,8, bold=True, bg=TURQUESA, fg=TEXTO_BLA, size=11, align="CENTER"), merge_req(ws_id,13,1,13,8), row_h(ws_id,13,32)]
    # Fila 14 headers tabla
    reqs += [fmt_req(ws_id,14,1,14,8, bold=True, bg=GRIS_OSC, fg=TEXTO_BLA, align="CENTER"), row_h(ws_id,14,26)]
    # Anchos columnas
    widths = [135,220,120,120,75,105,105,110]
    for i,w in enumerate(widths): reqs.append(col_w(ws_id,i+1,w))
    # Freeze fila 14
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": ws_id, "gridProperties": {"frozenRowCount": 14}}, "fields": "gridProperties.frozenRowCount"}})
    ss.batch_update({"requests": reqs})

    # ── CUENTAS ──
    if "Cuentas" in existing:
        ss.del_worksheet(ss.worksheet("Cuentas"))
    ws2 = ss.add_worksheet(title="Cuentas", rows=1000, cols=8)
    ws2_id = ws2._properties['sheetId']
    ws2.batch_update([
        {"range": "A1", "values": [["📋  REGISTRO DE MOVIMIENTOS — TODAS LAS CUENTAS"]]},
        {"range": "A3", "values": [["FECHA","DESCRIPCIÓN","CATEGORÍA","CUENTA","MONEDA","INGRESO","EGRESO","SALDO"]]},
    ])
    reqs2 = [
        fmt_req(ws2_id,1,1,1,8, bold=True, bg=AZUL_OSC, fg=TEXTO_BLA, size=13, align="CENTER"),
        merge_req(ws2_id,1,1,1,8), row_h(ws2_id,1,45),
        fmt_req(ws2_id,2,1,2,8, bg=BLANCO), row_h(ws2_id,2,10),
        fmt_req(ws2_id,3,1,3,8, bold=True, bg=GRIS_OSC, fg=TEXTO_BLA, align="CENTER"), row_h(ws2_id,3,26),
        {"updateSheetProperties": {"properties": {"sheetId": ws2_id, "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}},
    ]
    widths2 = [135,220,120,120,75,105,105,110]
    for i,w in enumerate(widths2): reqs2.append(col_w(ws2_id,i+1,w))
    ss.batch_update({"requests": reqs2})

    # ── INVERSIONES ──
    if "Inversiones" in existing:
        ss.del_worksheet(ss.worksheet("Inversiones"))
    ws3 = ss.add_worksheet(title="Inversiones", rows=500, cols=7)
    ws3_id = ws3._properties['sheetId']
    ws3.batch_update([
        {"range": "A1", "values": [["📈  REGISTRO DE INVERSIONES"]]},
        {"range": "A3", "values": [["FECHA","ACTIVO","MONTO","MONEDA","CUENTA ORIGEN","COTIZACIÓN","NOTAS"]]},
    ])
    reqs3 = [
        fmt_req(ws3_id,1,1,1,7, bold=True, bg=MORADO, fg=TEXTO_BLA, size=13, align="CENTER"),
        merge_req(ws3_id,1,1,1,7), row_h(ws3_id,1,45),
        fmt_req(ws3_id,2,1,2,7, bg=BLANCO), row_h(ws3_id,2,10),
        fmt_req(ws3_id,3,1,3,7, bold=True, bg=MORADO_MED, fg=TEXTO_BLA, align="CENTER"), row_h(ws3_id,3,26),
        {"updateSheetProperties": {"properties": {"sheetId": ws3_id, "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}},
    ]
    ss.batch_update({"requests": reqs3})

    for hoja in ["Sheet1","Hoja 1","Hoja1"]:
        if hoja in existing:
            try: ss.del_worksheet(ss.worksheet(hoja))
            except: pass

    return "✅ Diseño aplicado y todo reseteado a cero. ¡Listo para empezar!"

def update_global_summary():
    try:
        ctx = get_sheets_context()
        if not ctx: return
        ss = get_spreadsheet()
        ws_global = ss.worksheet("Global")
        ws_cuentas = ss.worksheet("Cuentas")
        ws_id = ws_global._properties['sheetId']
        ws_cuentas_id = ws_cuentas._properties['sheetId']

        saldos = ctx["saldos"]
        usd_rate = ctx["usd_rate"]
        total_uyu = sum(v for k,v in saldos.items() if "UYU" in k)
        total_usd = sum(v for k,v in saldos.items() if "USD" in k)
        total_en_uyu = total_uyu + total_usd * usd_rate
        total_en_usd = total_uyu / usd_rate + total_usd if usd_rate > 0 else 0
        now = datetime.now(UYU_TZ)

        ws_global.update(values=[[now.strftime("%d/%m/%Y %H:%M")]], range_name="B2")
        ws_global.update(values=[[f"$ {total_uyu:,.0f}", f"U$S {total_usd:,.2f}", f"$ {total_en_uyu:,.0f}", f"U$S {total_en_usd:,.2f}", f"$ {usd_rate:.2f}"]], range_name="A5")

        all_data = ws_cuentas.get_all_values()
        ing_uyu = eg_uyu = ing_usd = eg_usd = 0.0
        for row in all_data[3:]:
            if len(row) >= 7:
                try:
                    f = datetime.strptime(row[0].split(" ")[0], "%d/%m/%Y")
                    if f.month == now.month and f.year == now.year:
                        moneda = row[4] if len(row) > 4 else "UYU"
                        ing = float(row[5].replace(',','.')) if row[5] else 0
                        eg = float(row[6].replace(',','.')) if row[6] else 0
                        if "USD" in moneda: ing_usd += ing; eg_usd += eg
                        else: ing_uyu += ing; eg_uyu += eg
                except: pass

        ws_global.update(values=[["Ingresos", f"$ {ing_uyu:,.0f}", "", f"U$S {ing_usd:,.2f}", ""]], range_name="A9")
        ws_global.update(values=[["Egresos",  f"$ {eg_uyu:,.0f}",  "", f"U$S {eg_usd:,.2f}",  ""]], range_name="A10")
        ws_global.update(values=[["Balance",  f"$ {ing_uyu-eg_uyu:,.0f}", "", f"U$S {ing_usd-eg_usd:,.2f}", ""]], range_name="A11")

        reqs = []
        bal_uyu = ing_uyu - eg_uyu
        bal_usd = ing_usd - eg_usd
        for col_range, val in [((9,2,9,3), bal_uyu), ((9,4,9,5), bal_usd)]:
            bg = VERDE_CLA if val >= 0 else ROJO_CLA
            fg = VERDE_OSC if val >= 0 else ROJO_OSC
            reqs.append(fmt_req(ws_id, *col_range, bold=True, bg=bg, fg=fg, align="CENTER"))

        # Copiar movimientos al global (más nuevos primero)
        movs = [r for r in all_data[3:] if len(r) >= 7 and (r[5] or r[6])]
        movs_inv = list(reversed(movs))
        if movs_inv:
            ws_global.update(values=movs_inv, range_name="A15")
            for i, row in enumerate(movs_inv):
                es_ing = bool(row[5]) if len(row) > 5 else False
                es_eg  = bool(row[6]) if len(row) > 6 else False
                fi = 14 + i
                if es_ing and not es_eg:   bg, fg = VERDE_CLA, VERDE_OSC
                elif es_eg and not es_ing: bg, fg = ROJO_CLA, ROJO_OSC
                else:                      bg, fg = GRIS_CLA, TEXTO_OSC
                reqs.append(fmt_req(ws_id, fi+1, 1, fi+1, 8, bg=bg, fg=fg, align="CENTER"))

        # Colorear cuentas
        for i, row in enumerate(movs):
            es_ing = bool(row[5]) if len(row) > 5 else False
            es_eg  = bool(row[6]) if len(row) > 6 else False
            fi = 3 + i
            if es_ing and not es_eg:   bg, fg = VERDE_CLA, VERDE_OSC
            elif es_eg and not es_ing: bg, fg = ROJO_CLA, ROJO_OSC
            else:                      bg, fg = GRIS_CLA, TEXTO_OSC
            reqs.append(fmt_req(ws_cuentas_id, fi+1, 1, fi+1, 8, bg=bg, fg=fg, align="CENTER"))

        if reqs:
            ss.batch_update({"requests": reqs})
        # Invalidar cache después de escribir
        _sheets_cache["ts"] = 0
    except Exception as e:
        logger.error(f"Error update_global: {e}")

def execute_action(action):
    tipo = action.get("tipo")
    ss = get_spreadsheet()
    ws = ss.worksheet("Cuentas")
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    usd_rate = get_usd_rate()

    if tipo == "gasto":
        cuenta = normalize_cuenta(action["cuenta"]); monto = float(action["monto"]); moneda = action.get("moneda","UYU")
        saldo = get_balance(ws, cuenta) - monto
        time.sleep(1)
        ws.append_row([fecha, action["descripcion"], action.get("categoria","Otro"), cuenta, moneda, "", monto, round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Gasto registrado*\n📝 {action['descripcion']}\n💸 {sym} {monto:,.2f} | {action.get('categoria','Otro')}\n🏦 {cuenta}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "ingreso":
        cuenta = normalize_cuenta(action["cuenta"]); monto = float(action["monto"]); moneda = action.get("moneda","UYU")
        saldo = get_balance(ws, cuenta) + monto
        time.sleep(1)
        ws.append_row([fecha, action["descripcion"], action.get("categoria","Sueldo"), cuenta, moneda, monto, "", round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Ingreso registrado*\n📝 {action['descripcion']}\n💚 {sym} {monto:,.2f} | {action.get('categoria','Ingreso')}\n🏦 {cuenta}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "transferencia":
        origen = normalize_cuenta(action["cuenta_origen"]); destino = normalize_cuenta(action["cuenta_destino"])
        monto = float(action["monto"]); moneda = action.get("moneda","UYU")
        s_orig = get_balance(ws, origen) - monto
        s_dest = get_balance(ws, destino) + monto
        ws.append_row([fecha, f"Transferencia a {destino}", "Transferencia", origen, moneda, "", monto, round(s_orig,2)])
        ws.append_row([fecha, f"Transferencia desde {origen}", "Transferencia", destino, moneda, monto, "", round(s_dest,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Transferencia*\n📤 {origen}: {sym} {s_orig:,.2f}\n📥 {destino}: {sym} {s_dest:,.2f}\n💱 {sym} {monto:,.2f}"

    elif tipo == "inversion":
        activo = action["activo"]; monto = float(action["monto"])
        moneda = action.get("moneda","USD"); cuenta_orig = normalize_cuenta(action["cuenta"])
        ws_inv = ss.worksheet("Inversiones")
        ws_inv.append_row([fecha, activo, monto, moneda, cuenta_orig, usd_rate, action.get("descripcion","")])
        saldo = get_balance(ws, cuenta_orig) - monto
        ws.append_row([fecha, f"Inversión en {activo}", "Inversión", cuenta_orig, moneda, "", monto, round(saldo,2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Inversión registrada*\n📈 {activo}\n💸 {sym} {monto:,.2f}\n🏦 {cuenta_orig}\n💰 Saldo: {sym} {saldo:,.2f}"

    elif tipo == "eliminar":
        fila = action.get("fila")
        if fila:
            all_data = ws.get_all_values()
            fila_int = int(fila)
            if fila_int <= len(all_data):
                desc = all_data[fila_int-1][1] if len(all_data[fila_int-1]) > 1 else "movimiento"
                ws.delete_rows(fila_int)
                # Limpiar y reconstruir la tabla de movimientos en Global
                ws_global = ss.worksheet("Global")
                # Borrar desde fila 15 en adelante en Global
                all_global = ws_global.get_all_values()
                if len(all_global) >= 15:
                    rows_to_clear = len(all_global) - 14
                    ws_global.batch_clear([f"A15:H{14 + rows_to_clear + 10}"])
                update_global_summary()
                return f"✅ *Eliminado*: {desc}"
        return "❌ No pude identificar qué eliminar."

    elif tipo == "actualizar_saldo":
        cuenta = normalize_cuenta(action["cuenta"]); nuevo = float(action["saldo"])
        actual = get_balance(ws, cuenta); diff = nuevo - actual
        moneda = "USD" if "USD" in cuenta else "UYU"
        if diff > 0: ws.append_row([fecha, "Ajuste de saldo", "Ajuste", cuenta, moneda, diff, "", nuevo])
        elif diff < 0: ws.append_row([fecha, "Ajuste de saldo", "Ajuste", cuenta, moneda, "", abs(diff), nuevo])
        update_global_summary()
        sym = "$" if "UYU" in cuenta else "U$S"
        return f"✅ *Saldo actualizado*\n🏦 {cuenta}: {sym} {nuevo:,.2f}"

    elif tipo == "resumen":
        ctx = get_sheets_context()
        saldos = ctx["saldos"]; usd_rate = ctx["usd_rate"]
        now = datetime.now(UYU_TZ)
        total_uyu = sum(v for k,v in saldos.items() if "UYU" in k)
        total_usd = sum(v for k,v in saldos.items() if "USD" in k)
        lines = ["📊 *RESUMEN GLOBAL*", f"📅 {now.strftime('%d/%m/%Y %H:%M')}", "", "💰 *Saldos:*"]
        for c in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in c else "U$S"
            lines.append(f"  • {c}: {sym} {saldos[c]:,.2f}")
        lines += ["", "📈 *Totales:*",
            f"  • UYU: $ {total_uyu:,.2f}", f"  • USD: U$S {total_usd:,.2f}",
            f"  • Todo en UYU: $ {total_uyu + total_usd*usd_rate:,.2f}",
            f"  • Todo en USD: U$S {total_uyu/usd_rate + total_usd:,.2f}",
            f"  • Cotización: $ {usd_rate:.2f}",
            "", f"📅 *Este mes:*",
            f"  • Ingresos: $ {ctx['ingresos_mes']:,.2f}", f"  • Egresos: $ {ctx['egresos_mes']:,.2f}",
            f"  • Balance: $ {ctx['balance_mes']:,.2f}"]
        return "\n".join(lines)

    elif tipo == "editar":
        fila = action.get("fila")
        if fila:
            fila_int = int(fila)
            all_data = ws.get_all_values()
            if fila_int <= len(all_data):
                row = all_data[fila_int - 1]
                # Aplicar cambios
                if "monto" in action:
                    nuevo_monto = float(action["monto"])
                    es_ingreso = bool(row[5]) if len(row) > 5 else False
                    if es_ingreso:
                        ws.update_cell(fila_int, 6, nuevo_monto)
                        ws.update_cell(fila_int, 7, "")
                    else:
                        ws.update_cell(fila_int, 6, "")
                        ws.update_cell(fila_int, 7, nuevo_monto)
                if "descripcion" in action:
                    ws.update_cell(fila_int, 2, action["descripcion"])
                if "categoria" in action:
                    ws.update_cell(fila_int, 3, action["categoria"])
                if "cuenta" in action:
                    ws.update_cell(fila_int, 4, action["cuenta"])
                # Recalcular todos los saldos desde esa fila en adelante
                all_data2 = ws.get_all_values()
                for i, r in enumerate(all_data2[3:], start=4):
                    if len(r) >= 7 and r[3]:
                        cuenta_r = r[3]
                        saldo = 0.0
                        for prev in all_data2[3:i-1]:
                            if len(prev) >= 8 and prev[3] == cuenta_r:
                                saldo += (float(prev[5].replace(',','.')) if prev[5] else 0) - (float(prev[6].replace(',','.')) if prev[6] else 0)
                        ing = float(r[5].replace(',','.')) if r[5] else 0
                        eg = float(r[6].replace(',','.')) if r[6] else 0
                        ws.update_cell(i, 8, round(saldo + ing - eg, 2))
                # Limpiar Global y actualizar
                ws_global = ss.worksheet("Global")
                all_global = ws_global.get_all_values()
                if len(all_global) >= 15:
                    ws_global.batch_clear([f"A15:H{len(all_global) + 5}"])
                update_global_summary()
                desc = action.get("descripcion", all_data[fila_int-1][1] if len(all_data[fila_int-1]) > 1 else "movimiento")
                return f"✅ *Editado correctamente*\n📝 {desc}"
        return "❌ No pude identificar qué editar."

    return "❌ No entendí la operación."

async def process_message(update: Update, user_message: str):
    user_id = update.effective_user.id
    ctx = get_sheets_context()
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    system_prompt = f"""Sos KkaynBot, el asistente financiero personal de Seba (Uruguay).
Manejás su Google Sheets de gestión financiera mediante lenguaje natural en español rioplatense.

ESTADO ACTUAL:
Saldos: {json.dumps(ctx.get('saldos',{}), ensure_ascii=False)}
Últimos movimientos: {json.dumps(ctx.get('ultimos_movimientos',[]), ensure_ascii=False)}
Inversiones: {json.dumps(ctx.get('inversiones',[]), ensure_ascii=False)}
Cotización USD/UYU: {ctx.get('usd_rate',40)}
Ingresos este mes: {ctx.get('ingresos_mes',0)}
Egresos este mes: {ctx.get('egresos_mes',0)}

CUENTAS: {', '.join(CUENTAS_VALIDAS)}

Interpretá el mensaje y respondé con JSON:
- Acción única: {{"accion": {{...}}, "respuesta": "mensaje"}}
- Múltiples acciones: {{"acciones": [{{...}}, {{...}}], "respuesta": "mensaje"}}
- Solo consulta: {{"accion": null, "respuesta": "respuesta directa"}}

Tipos de acción:
- gasto: {{"tipo":"gasto","cuenta":"BBVA UYU","monto":500,"moneda":"UYU","descripcion":"supermercado","categoria":"Alimentación"}}
- ingreso: {{"tipo":"ingreso","cuenta":"BBVA UYU","monto":8000,"moneda":"UYU","descripcion":"sueldo","categoria":"Sueldo"}}
- transferencia: {{"tipo":"transferencia","cuenta_origen":"BBVA UYU","cuenta_destino":"Itaú UYU","monto":4000,"moneda":"UYU"}}
- inversion: {{"tipo":"inversion","activo":"BTC","cuenta":"Itaú USD","monto":200,"moneda":"USD"}}
- eliminar: {{"tipo":"eliminar","fila":NUMERO}} (buscá en ultimos_movimientos)
- editar: {{"tipo":"editar","fila":NUMERO,"monto":3000}} o {{"tipo":"editar","fila":NUMERO,"categoria":"Transporte"}} o {{"tipo":"editar","fila":NUMERO,"descripcion":"nuevo nombre"}} (para corregir un registro existente, buscá la fila en ultimos_movimientos)
- actualizar_saldo: {{"tipo":"actualizar_saldo","cuenta":"BBVA UYU","saldo":5000}}
- resumen: {{"tipo":"resumen"}}

REGLAS:
- Si el usuario dice "el último", "ese", "lo que pusiste" → usá ultimos_movimientos
- Si el usuario corrige un monto ("fueron 3k no 5k", "me equivoqué era 200") → usá "editar" con la fila correcta, NO elimines y agregues uno nuevo
- Si el usuario quiere cambiar categoría, descripción o monto de algo ya registrado → usá "editar"
- Si falta info crítica, preguntá antes de ejecutar
- Respondé siempre en español rioplatense, conciso
- Para múltiples operaciones (ej: "poné todas en 0") usá "acciones"
- SOLO JSON válido, sin texto extra
- "saldo en X", "cuánto tengo en X", "cómo estoy en X" = CONSULTA, nunca acción. Respondé con el saldo actual.
- "actualizar_saldo" SOLO cuando el usuario da un número explícito Y una intención clara de cambiar el saldo, ejemplo: "poné el saldo de BBVA en 5000" o "el saldo de Itaú es 3000". Si hay duda, preguntá.
- NUNCA hagas actualizar_saldo sin que el usuario haya dado un número explícito."""

    conversation_history[user_id].append({"role": "user", "content": user_message})
    if len(conversation_history[user_id]) > 10:
        conversation_history[user_id] = conversation_history[user_id][-10:]

    response_text = call_groq([{"role": "system", "content": system_prompt}] + conversation_history[user_id])
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)

    parsed = json.loads(response_text)
    conversation_history[user_id].append({"role": "assistant", "content": response_text})

    acciones = parsed.get("acciones")
    accion = parsed.get("accion")
    respuesta = parsed.get("respuesta", "")

    if acciones and isinstance(acciones, list):
        resultados = []
        for a in acciones:
            try:
                r = execute_action(a)
                if r: resultados.append(r)
            except Exception as e:
                resultados.append(f"❌ Error: {e}")
        # Si hay muchos resultados, resumirlos
        if len(resultados) > 3:
            return f"✅ *{len(resultados)} operaciones ejecutadas correctamente.*\n" + respuesta
        return "\n\n".join(resultados) if resultados else respuesta
    elif accion:
        resultado = execute_action(accion)
        return resultado if resultado else respuesta
    return respuesta

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ Sin acceso.")
        return
    await update.message.reply_text(
        "👋 ¡Hola Seba\\! Soy *KkaynBot*, tu asistente financiero\\.\n\n"
        "Hablame natural:\n"
        "• _cobré el sueldo, 8000 pesos en BBVA_\n"
        "• _gasté 500 en el súper con Itaú_\n"
        "• _pasé 4000 de BBVA a Itaú_\n"
        "• _puse 200 dólares en BTC desde Itaú_\n"
        "• _borrá el último movimiento_\n"
        "• _¿cuánto gasté esta semana?_\n\n"
        "Comandos: /resumen /saldo /setup /limpiar",
        parse_mode="MarkdownV2")

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID: return
    await update.message.reply_text("⚙️ Aplicando diseño...")
    try:
        result = setup_sheets()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

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
        ctx = get_sheets_context()
        saldos = ctx.get("saldos",{}); usd_rate = ctx.get("usd_rate",40)
        lines = ["💳 *SALDOS ACTUALES*\n"]
        for c in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in c else "U$S"
            lines.append(f"• {c}: {sym} {saldos.get(c,0):,.2f}")
        lines.append(f"\n💱 1 USD = $ {usd_rate:.2f}")
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
        await update.message.reply_text(f"❌ Error: {e}")

async def send_weekly_report(app):
    try:
        await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text="📅 *REPORTE SEMANAL*\n\n" + execute_action({"tipo":"resumen"}), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error reporte: {e}")

async def check_low_balance(app):
    try:
        ctx = get_sheets_context()
        MIN_UYU = float(os.environ.get("MIN_BALANCE_UYU","500"))
        MIN_USD = float(os.environ.get("MIN_BALANCE_USD","50"))
        alerts = []
        for c, s in ctx.get("saldos",{}).items():
            if "UYU" in c and 0 < s < MIN_UYU: alerts.append(f"⚠️ {c}: $ {s:,.2f}")
            elif "USD" in c and 0 < s < MIN_USD: alerts.append(f"⚠️ {c}: U$S {s:,.2f}")
        if alerts:
            await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text="🚨 *SALDO BAJO*\n\n" + "\n".join(alerts), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error balance check: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    scheduler = AsyncIOScheduler(timezone=UYU_TZ)
    scheduler.add_job(send_weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    scheduler.add_job(check_low_balance, "cron", hour=8, minute=0, args=[app])
    scheduler.start()
    logger.info("🤖 KkaynBot v3 iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
