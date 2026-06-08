import os, json, logging, re, time
from datetime import datetime
import pytz, requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID     = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
UYU_TZ   = pytz.timezone("America/Montevideo")
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS  = ["BBVA UYU","BBVA USD","Itaú UYU","Itaú USD","Efectivo UYU","Efectivo USD"]
BANCOS   = [("BBVA","BBVA UYU","BBVA USD"), ("ITAÚ","Itaú UYU","Itaú USD"), ("EFECTIVO","Efectivo UYU","Efectivo USD")]
conversation_history = {}
_cache = {"ts": 0.0, "data": None}

# ── Colores ───────────────────────────────────────────────────────────────────
def C(r,g,b): return {"red":r/255,"green":g/255,"blue":b/255}
AZ_OSC=C(26,42,78);  AZ_MED=C(52,90,150);  AZ_CLA=C(220,232,247)
TURQ  =C(0,137,123); VD_OSC=C(27,94,32);   VD_CLA=C(220,245,220)
RJ_OSC=C(180,28,28); RJ_CLA=C(255,232,232); GR_OSC=C(55,71,79)
GR_CLA=C(245,247,248); BLANCO=C(255,255,255); T_BLA=C(255,255,255); T_OSC=C(25,35,50)
MORADO=C(74,20,140); MOR_MED=C(106,27,154)

# ── Normalización ──────────────────────────────────────────────────────────────
CMAP = {"bbva uyu":"BBVA UYU","bbva usd":"BBVA USD",
        "itau uyu":"Itaú UYU","itaú uyu":"Itaú UYU","itaù uyu":"Itaú UYU","itàu uyu":"Itaú UYU",
        "itau usd":"Itaú USD","itaú usd":"Itaú USD","itaù usd":"Itaú USD","itàu usd":"Itaú USD",
        "efectivo uyu":"Efectivo UYU","efectivo usd":"Efectivo USD"}
def nc(n): return CMAP.get((n or "").lower().strip(), n)

# ── Sheets helpers ─────────────────────────────────────────────────────────────
def gs_client():
    return gspread.authorize(Credentials.from_service_account_info(
        json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON")), scopes=SCOPES))
def ss(): return gs_client().open_by_key(SPREADSHEET_ID)
def usd_rate():
    try: return requests.get("https://api.exchangerate-api.com/v4/latest/USD",timeout=5).json()["rates"].get("UYU",40.0)
    except: return 40.0
def sf(v):
    try: return float(str(v).replace(",",".")) if v else 0.0
    except: return 0.0
def bal(data, cuenta):
    b=0.0
    for r in data[3:]:
        if len(r)>=7 and r[3]==cuenta: b+=sf(r[5])-sf(r[6])
    return b

def get_ctx(force=False):
    global _cache
    now=time.time()
    if not force and _cache["data"] and (now-_cache["ts"])<20: return _cache["data"]
    try:
        sp=ss(); wc=sp.worksheet("Cuentas"); data=wc.get_all_values()
        saldos={c:bal(data,c) for c in CUENTAS}
        ult=[]
        for i,r in enumerate(data[3:],start=4):
            if len(r)>=7 and (r[5] or r[6]):
                ult.append({"fila":i,"fecha":r[0],"descripcion":r[1],"categoria":r[2],
                             "cuenta":r[3],"moneda":r[4],"ingreso":r[5],"egreso":r[6],
                             "saldo":r[7] if len(r)>7 else ""})
        ult=ult[-10:]
        wi=sp.worksheet("Inversiones")
        inv=[{"activo":r[1],"monto":r[2],"moneda":r[3],"fecha":r[0]}
             for r in wi.get_all_values()[3:] if len(r)>=4 and r[1]]
        rate=usd_rate(); now_dt=datetime.now(UYU_TZ)
        iu=eu=id_=ed=0.0
        for r in data[3:]:
            if len(r)>=7:
                try:
                    f=datetime.strptime(r[0].split(" ")[0],"%d/%m/%Y")
                    if f.month==now_dt.month and f.year==now_dt.year:
                        if "USD" in (r[4] if len(r)>4 else ""): id_+=sf(r[5]); ed+=sf(r[6])
                        else: iu+=sf(r[5]); eu+=sf(r[6])
                except: pass
        movs=[r for r in data[3:] if len(r)>=7 and (r[5] or r[6])]
        res={"saldos":saldos,"ult":ult,"inv":inv,"rate":rate,
             "iu":iu,"eu":eu,"id":id_,"ed":ed,"movs":movs,"data":data}
        _cache={"ts":time.time(),"data":res}; return res
    except Exception as e: logger.error(f"ctx: {e}"); return {}

def inv_cache(): _cache["ts"]=0.0; _cache["data"]=None

def with_retry(func, *args, max_retries=3, **kwargs):
    """Ejecuta una función con retry automático ante 429."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "RATE_LIMIT" in err:
                wait = 15 * (attempt + 1)
                logger.warning(f"Rate limit (intento {attempt+1}), esperando {wait}s...")
                time.sleep(wait)
                if attempt == max_retries - 1:
                    raise
            else:
                raise
    return None

# ── Formato helpers ────────────────────────────────────────────────────────────
def fr(sid,r1,c1,r2,c2,bold=False,bg=None,fg=None,sz=None,al=None):
    fmt={}; tf={}
    if bold: tf["bold"]=True
    if fg:   tf["foregroundColor"]=fg
    if sz:   tf["fontSize"]=sz
    if tf:   fmt["textFormat"]=tf
    if bg:   fmt["backgroundColor"]=bg
    if al:   fmt["horizontalAlignment"]=al
    fmt["verticalAlignment"]="MIDDLE"
    return {"repeatCell":{"range":{"sheetId":sid,"startRowIndex":r1-1,"endRowIndex":r2,
            "startColumnIndex":c1-1,"endColumnIndex":c2},"cell":{"userEnteredFormat":fmt},"fields":"userEnteredFormat"}}
def mg(sid,r1,c1,r2,c2):
    return {"mergeCells":{"range":{"sheetId":sid,"startRowIndex":r1-1,"endRowIndex":r2,
            "startColumnIndex":c1-1,"endColumnIndex":c2},"mergeType":"MERGE_ALL"}}
def cw(sid,c,px): return {"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"COLUMNS","startIndex":c-1,"endIndex":c},"properties":{"pixelSize":px},"fields":"pixelSize"}}
def rh(sid,r,px): return {"updateDimensionProperties":{"range":{"sheetId":sid,"dimension":"ROWS","startIndex":r-1,"endIndex":r},"properties":{"pixelSize":px},"fields":"pixelSize"}}
def col_letter(n): return chr(64+n) if n<=26 else chr(64+n//26)+chr(64+n%26)

# ── SETUP ──────────────────────────────────────────────────────────────────────
def setup_sheets():
    sp=ss(); existing=[w.title for w in sp.worksheets()]
    # Orden deseado: Global, Por Cuenta, Inversiones, Cuentas
    # Crear temp para no quedar sin hojas (borrar si ya existe)
    try: sp.del_worksheet(sp.worksheet("_t_"))
    except: pass
    temp=sp.add_worksheet("_t_",1,1)
    for t in ["Global","Por Cuenta","Inversiones","Cuentas"]:
        if t in existing:
            try: sp.del_worksheet(sp.worksheet(t))
            except: pass

    # ── 1. GLOBAL ──
    wg=sp.add_worksheet("Global",rows=500,cols=10)
    sid=wg._properties['sheetId']
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
    rqs=[fr(sid,1,1,1,8,bold=True,bg=AZ_OSC,fg=T_BLA,sz=14,al="CENTER"),mg(sid,1,1,1,8),rh(sid,1,48),
         fr(sid,2,1,2,8,bold=True,bg=AZ_MED,fg=T_BLA,sz=10,al="LEFT"),rh(sid,2,22),
         fr(sid,3,1,3,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"),mg(sid,3,1,3,8),rh(sid,3,32),
         fr(sid,4,1,4,5,bold=True,bg=AZ_MED,fg=T_BLA,al="CENTER"),rh(sid,4,26),
         fr(sid,5,1,5,5,bold=True,bg=AZ_CLA,fg=AZ_OSC,sz=11,al="CENTER"),rh(sid,5,30),
         fr(sid,6,1,6,8,bg=BLANCO),rh(sid,6,10),
         fr(sid,7,1,7,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"),mg(sid,7,1,7,8),rh(sid,7,32),
         fr(sid,8,1,8,5,bold=True,bg=AZ_MED,fg=T_BLA,al="CENTER"),
         mg(sid,8,2,8,3),mg(sid,8,4,8,5),rh(sid,8,26)]
    for r in [9,10,11]:
        rqs+=[fr(sid,r,1,r,1,bold=True,bg=AZ_CLA,fg=AZ_OSC,al="LEFT"),
              fr(sid,r,2,r,3,bg=GR_CLA,fg=T_OSC,al="CENTER"),mg(sid,r,2,r,3),
              fr(sid,r,4,r,5,bg=GR_CLA,fg=T_OSC,al="CENTER"),mg(sid,r,4,r,5),rh(sid,r,26)]
    rqs+=[fr(sid,12,1,12,8,bg=BLANCO),rh(sid,12,10),
          fr(sid,13,1,13,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"),mg(sid,13,1,13,8),rh(sid,13,32),
          fr(sid,14,1,14,8,bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"),rh(sid,14,26)]
    for i,w in enumerate([135,220,120,120,75,105,105,110]): rqs.append(cw(sid,i+1,w))
    rqs.append({"updateSheetProperties":{"properties":{"sheetId":sid,"gridProperties":{"frozenRowCount":14}},"fields":"gridProperties.frozenRowCount"}})
    sp.batch_update({"requests":rqs})

    # ── 2. POR CUENTA ──
    # Cols: 1-6=UYU, 7=sep(14px), 8-13=USD, 14=sep(14px), 15=SAL UYU, 16=SAL USD
    # Total = 16 cols. Sheet needs cols=16, format refs max col 16.
    wp=sp.add_worksheet("Por Cuenta",rows=500,cols=17)
    wpc=wp._properties['sheetId']
    wp.update(values=[["📊  MOVIMIENTOS POR CUENTA"]], range_name="A1")
    rqp=[fr(wpc,1,1,1,16,bold=True,bg=AZ_OSC,fg=T_BLA,sz=14,al="CENTER"),
         mg(wpc,1,1,1,16),rh(wpc,1,48),
         cw(wpc,7,14),cw(wpc,14,14)]  # separadores
    for j,w in enumerate([125,190,108,88,88,92]): rqp+=[cw(wpc,1+j,w),cw(wpc,8+j,w)]
    # Totales: solo 2 cols (15 y 16)
    rqp+=[cw(wpc,15,115),cw(wpc,16,115)]
    sp.batch_update({"requests":rqp})

    # ── 3. INVERSIONES ──
    wi=sp.add_worksheet("Inversiones",rows=500,cols=7)
    wii=wi._properties['sheetId']
    wi.batch_update([
        {"range":"A1","values":[["📈  REGISTRO DE INVERSIONES"]]},
        {"range":"A3","values":[["FECHA","ACTIVO","MONTO","MONEDA","CUENTA ORIGEN","COTIZACIÓN","NOTAS"]]},
    ])
    sp.batch_update({"requests":[
        fr(wii,1,1,1,7,bold=True,bg=MORADO,fg=T_BLA,sz=13,al="CENTER"),mg(wii,1,1,1,7),rh(wii,1,45),
        fr(wii,2,1,2,7,bg=BLANCO),rh(wii,2,10),
        fr(wii,3,1,3,7,bold=True,bg=MOR_MED,fg=T_BLA,al="CENTER"),rh(wii,3,26),
        {"updateSheetProperties":{"properties":{"sheetId":wii,"gridProperties":{"frozenRowCount":3}},"fields":"gridProperties.frozenRowCount"}},
    ]})

    # ── 4. CUENTAS (storage) ──
    wc=sp.add_worksheet("Cuentas",rows=1000,cols=9)
    wci=wc._properties['sheetId']
    wc.batch_update([
        {"range":"A1","values":[["📋  REGISTRO DE MOVIMIENTOS — STORAGE"]]},
        {"range":"A3","values":[["FECHA","DESCRIPCIÓN","CATEGORÍA","CUENTA","MONEDA","INGRESO","EGRESO","SALDO"]]},
    ])
    rqc=[fr(wci,1,1,1,8,bold=True,bg=AZ_OSC,fg=T_BLA,sz=12,al="CENTER"),mg(wci,1,1,1,8),rh(wci,1,40),
         fr(wci,2,1,2,8,bg=BLANCO),rh(wci,2,8),
         fr(wci,3,1,3,8,bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"),rh(wci,3,26),
         {"updateSheetProperties":{"properties":{"sheetId":wci,"gridProperties":{"frozenRowCount":3}},"fields":"gridProperties.frozenRowCount"}}]
    for i,w in enumerate([135,220,120,120,75,105,105,110]): rqc.append(cw(wci,i+1,w))
    sp.batch_update({"requests":rqc})

    # Limpiar temp y hojas viejas
    for h in ["_t_","Sheet1","Hoja 1","Hoja1"]:
        try: sp.del_worksheet(sp.worksheet(h))
        except: pass

    inv_cache()
    return "✅ Todo listo. Orden: Global → Por Cuenta → Inversiones → Cuentas\nCargá tu primer movimiento y la pestaña Por Cuenta se construirá automáticamente."

# ── UPDATE GLOBAL ──────────────────────────────────────────────────────────────
def update_global():
    try:
        inv_cache(); ctx=get_ctx(force=True)
        if not ctx: return
        sp=ss(); wg=sp.worksheet("Global"); wc=sp.worksheet("Cuentas")
        sid=wg._properties['sheetId']; cid=wc._properties['sheetId']
        s=ctx["saldos"]; rate=ctx["rate"]
        tu=sum(v for k,v in s.items() if "UYU" in k)
        td=sum(v for k,v in s.items() if "USD" in k)
        now=datetime.now(UYU_TZ)
        with_retry(wg.batch_update, [
            {"range":"B2","values":[[now.strftime("%d/%m/%Y %H:%M")]]},
            {"range":"A5","values":[[f"$ {tu:,.0f}",f"U$S {td:,.2f}",f"$ {tu+td*rate:,.0f}",f"U$S {tu/rate+td:,.2f}" if rate else "U$S 0",f"$ {rate:.2f}"]]},
            {"range":"A9", "values":[["Ingresos",f"$ {ctx['iu']:,.0f}","",f"U$S {ctx['id']:,.2f}",""]]},
            {"range":"A10","values":[["Egresos", f"$ {ctx['eu']:,.0f}","",f"U$S {ctx['ed']:,.2f}",""]]},
            {"range":"A11","values":[["Balance", f"$ {ctx['iu']-ctx['eu']:,.0f}","",f"U$S {ctx['id']-ctx['ed']:,.2f}",""]]},
        ])
        movs=ctx["movs"]; inv=list(reversed(movs))
        rqs=[]
        # Balance color
        for (r1,c1,r2,c2),v in [((11,2,11,3),ctx['iu']-ctx['eu']),((11,4,11,5),ctx['id']-ctx['ed'])]:
            bg=VD_CLA if v>=0 else RJ_CLA; fg=VD_OSC if v>=0 else RJ_OSC
            rqs.append(fr(sid,r1,c1,r2,c2,bold=True,bg=bg,fg=fg,al="CENTER"))
        if inv:
            wg.batch_clear([f"A15:H{14+len(inv)+5}"])
            wg.update(values=inv,range_name="A15")
            for i,r in enumerate(inv):
                ei=bool(r[5]) if len(r)>5 else False; ee=bool(r[6]) if len(r)>6 else False
                fi=15+i
                if ei and not ee: bg,fg=VD_CLA,VD_OSC
                elif ee and not ei: bg,fg=RJ_CLA,RJ_OSC
                else: bg,fg=GR_CLA,T_OSC
                rqs.append(fr(sid,fi,1,fi,8,bg=bg,fg=fg,al="CENTER"))
        # Colorear Cuentas
        for i,r in enumerate(movs):
            ei=bool(r[5]) if len(r)>5 else False; ee=bool(r[6]) if len(r)>6 else False
            fi=4+i
            if ei and not ee: bg,fg=VD_CLA,VD_OSC
            elif ee and not ei: bg,fg=RJ_CLA,RJ_OSC
            else: bg,fg=GR_CLA,T_OSC
            rqs.append(fr(cid,fi,1,fi,8,bg=bg,fg=fg,al="CENTER"))
        if rqs: sp.batch_update({"requests":rqs})
        inv_cache()
        # Actualizar Por Cuenta
        try: update_por_cuenta()
        except Exception as e: logger.warning(f"por_cuenta: {e}")
    except Exception as e: logger.error(f"update_global: {e}")

# ── UPDATE POR CUENTA ──────────────────────────────────────────────────────────
def update_por_cuenta():
    """Reconstruye toda la pestaña Por Cuenta desde cero.
    3 bloques dinámicos (BBVA, ITAÚ, EFECTIVO), separados por 3 filas vacías.
    Cada bloque:
      Fila A: nombre banco (merge 1-16, azul oscuro)
      Fila B: sub-headers PESOS(UYU) [1-6] | sep[7] | DÓLARES(USD) [8-13] | sep[14] | TOTALES[15-16]
      Fila C: col-headers
      Filas D+: datos en paralelo (UYU izq, USD der)
    Cols: 1-6=UYU, 7=sep(14px), 8-13=USD, 14=sep(14px), 15=SAL UYU, 16=SAL USD
    """
    ctx = get_ctx()
    if not ctx: return
    sp = ss()
    try: wp = sp.worksheet("Por Cuenta")
    except: return
    wpc = wp._properties['sheetId']
    s = ctx["saldos"]; rate = ctx["rate"]; movs = ctx["movs"]
    H = ['FECHA','DESCRIPCIÓN','CATEGORÍA','INGRESO','EGRESO','SALDO']
    HT = ['SAL UYU','SAL USD']
    NCOLS = 16

    # Limpiar todo desde fila 2
    wp.batch_clear(["A2:P500"])
    rqs = []; bv = []; cur = 2

    for bname, cuyu, cusd in BANCOS:
        muyu = [r for r in movs if r[3] == cuyu]
        musd = [r for r in movs if r[3] == cusd]
        n = max(len(muyu), len(musd), 0)
        suyu = s.get(cuyu, 0); susd = s.get(cusd, 0)

        # ── Fila A: nombre banco ──
        rqs += [fr(wpc,cur,1,cur,NCOLS, bold=True,bg=AZ_OSC,fg=T_BLA,sz=12,al="CENTER"),
                mg(wpc,cur,1,cur,NCOLS), rh(wpc,cur,34)]
        bv.append({"range":f"A{cur}","values":[[f"🏦  {bname}"]]})
        cur += 1

        # ── Fila B: sub-headers ──
        rqs += [
            fr(wpc,cur,1,cur,6,   bold=True,bg=TURQ,fg=T_BLA,sz=10,al="CENTER"), mg(wpc,cur,1,cur,6),
            fr(wpc,cur,7,cur,7,   bg=BLANCO),
            fr(wpc,cur,8,cur,13,  bold=True,bg=TURQ,fg=T_BLA,sz=10,al="CENTER"), mg(wpc,cur,8,cur,13),
            fr(wpc,cur,14,cur,14, bg=BLANCO),
            fr(wpc,cur,15,cur,16, bold=True,bg=AZ_MED,fg=T_BLA,sz=10,al="CENTER"), mg(wpc,cur,15,cur,16),
            rh(wpc,cur,26),
        ]
        bv += [{"range":f"A{cur}","values":[["PESOS (UYU)"]]},
               {"range":f"H{cur}","values":[["DÓLARES (USD)"]]},
               {"range":f"O{cur}","values":[["TOTALES"]]}]
        cur += 1

        # ── Fila C: col-headers ──
        rqs += [
            fr(wpc,cur,1,cur,6,   bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"),
            fr(wpc,cur,7,cur,7,   bg=BLANCO),
            fr(wpc,cur,8,cur,13,  bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"),
            fr(wpc,cur,14,cur,14, bg=BLANCO),
            fr(wpc,cur,15,cur,16, bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"),
            rh(wpc,cur,24),
        ]
        bv += [{"range":f"A{cur}","values":[H]},
               {"range":f"H{cur}","values":[H]},
               {"range":f"O{cur}","values":[HT]}]
        cur += 1

        # ── Totales (primera fila de datos) ──
        sym_u = "$"; sym_d = "U$S"
        bv.append({"range":f"O{cur}","values":[[f"{sym_u} {suyu:,.0f}", f"{sym_d} {susd:,.2f}"]]})
        rqs.append(fr(wpc,cur,15,cur,16, bold=True,bg=AZ_CLA,fg=AZ_OSC,al="CENTER"))

        # ── Filas de datos ──
        if n == 0:
            # Sin datos: una fila vacía para que se vea la estructura
            rqs += [fr(wpc,cur,1,cur,6,bg=GR_CLA), fr(wpc,cur,8,cur,13,bg=GR_CLA), rh(wpc,cur,22)]
            cur += 1
        else:
            for i in range(n):
                fi = cur + i
                if i < len(muyu):
                    r = muyu[i]
                    bv.append({"range":f"A{fi}","values":[[r[0],r[1],r[2],r[5],r[6],r[7]]]})
                    ei = bool(r[5]); ee = bool(r[6])
                    if ei and not ee:   bg, fg = VD_CLA, VD_OSC
                    elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
                    else:               bg, fg = GR_CLA, T_OSC
                    rqs.append(fr(wpc,fi,1,fi,6, bg=bg,fg=fg,al="CENTER"))
                else:
                    rqs.append(fr(wpc,fi,1,fi,6, bg=GR_CLA))
                if i < len(musd):
                    r = musd[i]
                    bv.append({"range":f"H{fi}","values":[[r[0],r[1],r[2],r[5],r[6],r[7]]]})
                    ei = bool(r[5]); ee = bool(r[6])
                    if ei and not ee:   bg, fg = VD_CLA, VD_OSC
                    elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
                    else:               bg, fg = GR_CLA, T_OSC
                    rqs.append(fr(wpc,fi,8,fi,13, bg=bg,fg=fg,al="CENTER"))
                else:
                    rqs.append(fr(wpc,fi,8,fi,13, bg=GR_CLA))
                rqs.append(fr(wpc,fi,7,fi,7,   bg=BLANCO))
                rqs.append(fr(wpc,fi,14,fi,14, bg=BLANCO))
                rqs.append(rh(wpc,fi,22))
            cur += n

        # ── 3 filas separadoras ──
        for sep in range(3):
            rqs += [fr(wpc,cur,1,cur,NCOLS,bg=BLANCO), rh(wpc,cur,10)]
            cur += 1

    if bv: wp.batch_update(bv)
    if rqs: sp.batch_update({"requests":rqs})

# ── GROQ ───────────────────────────────────────────────────────────────────────
# ── GROQ ───────────────────────────────────────────────────────────────────────
def groq(msgs):
    r=requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
        json={"model":"llama-3.3-70b-versatile","messages":msgs,"temperature":0.1,"max_tokens":1000},timeout=30)
    if r.status_code!=200: raise Exception(f"Groq {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"].strip()

# ── EXECUTE ACTION ─────────────────────────────────────────────────────────────
def exe(action):
    t=action.get("tipo"); sp=ss(); wc=sp.worksheet("Cuentas")
    fecha=datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    data=wc.get_all_values()

    if t=="gasto":
        c=nc(action["cuenta"]); m=float(action["monto"]); mo=action.get("moneda","UYU")
        s=bal(data,c)-m; wc.append_row([fecha,action["descripcion"],action.get("categoria","Otro"),c,mo,"",m,round(s,2)])
        update_global(); sym="$" if "UYU" in mo else "U$S"
        return f"✅ *Gasto registrado*\n📝 {action['descripcion']}\n💸 {sym} {m:,.2f} | {action.get('categoria','Otro')}\n🏦 {c}\n💰 Saldo: {sym} {s:,.2f}"

    elif t=="ingreso":
        c=nc(action["cuenta"]); m=float(action["monto"]); mo=action.get("moneda","UYU")
        s=bal(data,c)+m; wc.append_row([fecha,action["descripcion"],action.get("categoria","Sueldo"),c,mo,m,"",round(s,2)])
        update_global(); sym="$" if "UYU" in mo else "U$S"
        return f"✅ *Ingreso registrado*\n📝 {action['descripcion']}\n💚 {sym} {m:,.2f} | {action.get('categoria','Ingreso')}\n🏦 {c}\n💰 Saldo: {sym} {s:,.2f}"

    elif t=="transferencia":
        o=nc(action["cuenta_origen"]); d=nc(action["cuenta_destino"]); m=float(action["monto"]); mo=action.get("moneda","UYU")
        so=bal(data,o)-m; sd=bal(data,d)+m
        with_retry(wc.append_row, [fecha,f"Transferencia a {d}","Transferencia",o,mo,"",m,round(so,2)])
        with_retry(wc.append_row, [fecha,f"Transferencia desde {o}","Transferencia",d,mo,m,"",round(sd,2)])
        update_global(); sym="$" if "UYU" in mo else "U$S"
        return f"✅ *Transferencia*\n📤 {o}: {sym} {so:,.2f}\n📥 {d}: {sym} {sd:,.2f}\n💱 {sym} {m:,.2f}"

    elif t=="inversion":
        a=action["activo"]; m=float(action["monto"]); mo=action.get("moneda","USD"); co=nc(action["cuenta"])
        rate=usd_rate(); wi=sp.worksheet("Inversiones")
        wi.append_row([fecha,a,m,mo,co,rate,action.get("descripcion","")])
        s=bal(data,co)-m; wc.append_row([fecha,f"Inversión en {a}","Inversión",co,mo,"",m,round(s,2)])
        update_global(); sym="$" if "UYU" in mo else "U$S"
        return f"✅ *Inversión*\n📈 {a}\n💸 {sym} {m:,.2f}\n🏦 {co}\n💰 Saldo: {sym} {s:,.2f}"

    elif t=="eliminar":
        f=action.get("fila")
        if f:
            fi=int(f)
            if fi<=len(data):
                desc=data[fi-1][1] if len(data[fi-1])>1 else "movimiento"
                wc.delete_rows(fi)
                wg=sp.worksheet("Global"); ag=wg.get_all_values()
                if len(ag)>=15: wg.batch_clear([f"A15:H{len(ag)+5}"])
                update_global()
                return f"✅ *Eliminado*: {desc}"
        return "❌ No pude identificar qué eliminar."

    elif t=="editar":
        f=action.get("fila")
        if f:
            fi=int(f)
            if fi<=len(data):
                row=data[fi-1]; desc_o=row[1] if len(row)>1 else "movimiento"
                ei=bool(row[5]) if len(row)>5 else False
                upd=[]
                if "monto" in action:
                    nm=float(action["monto"])
                    if ei: upd+=[{"range":f"F{fi}","values":[[nm]]},{"range":f"G{fi}","values":[[""]]}]
                    else:  upd+=[{"range":f"F{fi}","values":[[""]]},{"range":f"G{fi}","values":[[nm]]}]
                if "descripcion" in action: upd.append({"range":f"B{fi}","values":[[action["descripcion"]]]})
                if "categoria"   in action: upd.append({"range":f"C{fi}","values":[[action["categoria"]]]})
                if "cuenta"      in action: upd.append({"range":f"D{fi}","values":[[nc(action["cuenta"])]]})
                if upd: wc.batch_update(upd)
                time.sleep(1); fresh=wc.get_all_values(); spc={}; cu=[]
                for idx in range(3,len(fresh)):
                    r=fresh[idx]
                    if len(r)>=7 and r[3]:
                        c=r[3]
                        if c not in spc: spc[c]=0.0
                        spc[c]+=sf(r[5])-sf(r[6])
                        cu.append({"range":f"H{idx+1}","values":[[round(spc[c],2)]]})
                for i in range(0,len(cu),50):
                    wc.batch_update(cu[i:i+50])
                    if i+50<len(cu): time.sleep(1)
                wg=sp.worksheet("Global"); ag=wg.get_all_values()
                if len(ag)>=15: wg.batch_clear([f"A15:H{len(ag)+5}"])
                update_global()
                return f"✅ *Editado*: {action.get('descripcion',desc_o)}"
        return "❌ No pude identificar qué editar."

    elif t=="actualizar_saldo":
        c=nc(action["cuenta"]); nv=float(action["saldo"])
        act=bal(data,c); df=nv-act; mo="USD" if "USD" in c else "UYU"
        if df>0:   wc.append_row([fecha,"Ajuste de saldo","Ajuste",c,mo,df,"",nv])
        elif df<0: wc.append_row([fecha,"Ajuste de saldo","Ajuste",c,mo,"",abs(df),nv])
        update_global(); sym="$" if "UYU" in c else "U$S"
        return f"✅ *Saldo actualizado*\n🏦 {c}: {sym} {nv:,.2f}"

    elif t=="resumen":
        ctx=get_ctx(); s=ctx.get("saldos",{}); rate=ctx.get("rate",40)
        now=datetime.now(UYU_TZ)
        tu=sum(v for k,v in s.items() if "UYU" in k); td=sum(v for k,v in s.items() if "USD" in k)
        lines=["📊 *RESUMEN GLOBAL*",f"📅 {now.strftime('%d/%m/%Y %H:%M')}","","💰 *Saldos:*"]
        for c in CUENTAS:
            sym="$" if "UYU" in c else "U$S"; lines.append(f"  • {c}: {sym} {s.get(c,0):,.2f}")
        lines+=["","📈 *Totales:*",f"  • UYU: $ {tu:,.2f}",f"  • USD: U$S {td:,.2f}",
            f"  • Todo en UYU: $ {tu+td*rate:,.2f}",
            f"  • Todo en USD: U$S {tu/rate+td:,.2f}" if rate else "  • Todo en USD: U$S 0",
            f"  • Cotización: $ {rate:.2f}","","📅 *Este mes:*",
            f"  • Ingresos UYU: $ {ctx.get('iu',0):,.2f}",f"  • Egresos UYU: $ {ctx.get('eu',0):,.2f}",
            f"  • Balance UYU: $ {ctx.get('iu',0)-ctx.get('eu',0):,.2f}",
            f"  • Ingresos USD: U$S {ctx.get('id',0):,.2f}",f"  • Egresos USD: U$S {ctx.get('ed',0):,.2f}"]
        return "\n".join(lines)

    return "❌ No entendí la operación."

# ── PROCESS MESSAGE ────────────────────────────────────────────────────────────
async def process_msg(update, user_message):
    uid=update.effective_user.id; ctx=get_ctx()
    if uid not in conversation_history: conversation_history[uid]=[]
    sys=f"""Sos KkaynBot, asistente financiero de Seba (Uruguay). Español rioplatense.
ESTADO:
Saldos: {json.dumps(ctx.get('saldos',{}),ensure_ascii=False)}
Últimos movimientos: {json.dumps(ctx.get('ult',[]),ensure_ascii=False)}
Inversiones: {json.dumps(ctx.get('inv',[]),ensure_ascii=False)}
Cotización USD/UYU: {ctx.get('rate',40)}
Ingresos mes UYU: {ctx.get('iu',0)} | Egresos mes UYU: {ctx.get('eu',0)}
Ingresos mes USD: {ctx.get('id',0)} | Egresos mes USD: {ctx.get('ed',0)}
CUENTAS: {', '.join(CUENTAS)}

Respondé SOLO con JSON:
- Acción única:    {{"accion":{{...}},"respuesta":"..."}}
- Varias acciones: {{"acciones":[{{...}}],"respuesta":"..."}}
- Solo consulta:   {{"accion":null,"respuesta":"..."}}

Tipos:
- gasto:           {{"tipo":"gasto","cuenta":"BBVA UYU","monto":500,"moneda":"UYU","descripcion":"súper","categoria":"Alimentación"}}
- ingreso:         {{"tipo":"ingreso","cuenta":"BBVA UYU","monto":50000,"moneda":"UYU","descripcion":"sueldo","categoria":"Sueldo"}}
- transferencia:   {{"tipo":"transferencia","cuenta_origen":"BBVA UYU","cuenta_destino":"Itaú UYU","monto":10000,"moneda":"UYU"}}
- inversion:       {{"tipo":"inversion","activo":"BTC","cuenta":"Itaú USD","monto":200,"moneda":"USD"}}
- eliminar:        {{"tipo":"eliminar","fila":N}}
- editar:          {{"tipo":"editar","fila":N,"monto":48000}} o {{"tipo":"editar","fila":N,"categoria":"..."}}
- actualizar_saldo:{{"tipo":"actualizar_saldo","cuenta":"BBVA UYU","saldo":5000}} SOLO con número explícito
- resumen:         {{"tipo":"resumen"}}

REGLAS:
- "saldo en X","cuánto tengo","cómo estoy en X" = CONSULTA, nunca acción
- actualizar_saldo SOLO si el usuario da número explícito
- Si corrige monto ("fueron 3k no 5k") → editar con fila de ult
- "el último/ese" → identificar en ult
- Si falta info → preguntar
- Múltiples cosas en un mensaje → usar "acciones"
- SOLO JSON, sin texto extra"""
    conversation_history[uid].append({"role":"user","content":user_message})
    if len(conversation_history[uid])>10: conversation_history[uid]=conversation_history[uid][-10:]
    raw=groq([{"role":"system","content":sys}]+conversation_history[uid])
    raw=re.sub(r'```json\s*','',raw); raw=re.sub(r'```\s*','',raw)
    parsed=json.loads(raw)
    conversation_history[uid].append({"role":"assistant","content":raw})
    acciones=parsed.get("acciones"); accion=parsed.get("accion"); resp=parsed.get("respuesta","")
    if acciones and isinstance(acciones,list):
        res=[]
        for a in acciones:
            try:
                r=exe(a)
                if r: res.append(r)
            except Exception as e: res.append(f"❌ {e}")
        if len(res)>3: return f"✅ *{len(res)} operaciones ejecutadas.*\n{resp}"
        return "\n\n".join(res) if res else resp
    elif accion:
        r=exe(accion); return r if r else resp
    return resp

# ── HANDLERS ───────────────────────────────────────────────────────────────────
async def start(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: await u.message.reply_text("⛔"); return
    await u.message.reply_text("👋 *KkaynBot* listo\\.\n\nEjemplos:\n• _cobré sueldo 50k en BBVA_\n• _gasté 300 en farmacia con Itaú_\n• _pasé 10k de BBVA a Itaú_\n• _el sueldo fueron 48k no 50k_\n• _¿cuánto tengo en BBVA?_\n\nComandos: /resumen /saldo /setup /limpiar",parse_mode="MarkdownV2")

async def cmd_setup(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: return
    await u.message.reply_text("⚙️ Aplicando diseño...")
    try: await u.message.reply_text(setup_sheets())
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_resumen(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: return
    await u.message.reply_text("🔄 Calculando...")
    try: await u.message.reply_text(exe({"tipo":"resumen"}),parse_mode="Markdown")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_saldo(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: return
    try:
        ctx=get_ctx(force=True); s=ctx.get("saldos",{}); rate=ctx.get("rate",40)
        lines=["💳 *SALDOS ACTUALES*\n"]
        for c_ in CUENTAS:
            sym="$" if "UYU" in c_ else "U$S"; lines.append(f"• {c_}: {sym} {s.get(c_,0):,.2f}")
        lines.append(f"\n💱 1 USD = $ {rate:.2f}")
        await u.message.reply_text("\n".join(lines),parse_mode="Markdown")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_limpiar(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: return
    conversation_history[u.effective_user.id]=[]
    await u.message.reply_text("🧹 Historial limpiado.")

async def handle_msg(u,c):
    if u.effective_user.id!=AUTHORIZED_USER_ID: return
    await u.message.reply_text("🤔 Procesando...")
    try:
        r=await process_msg(u,u.message.text.strip())
        await u.message.reply_text(r,parse_mode="Markdown")
    except Exception as e: logger.error(f"Error: {e}"); await u.message.reply_text(f"❌ {e}")

async def weekly_report(app):
    try: await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,text="📅 *REPORTE SEMANAL*\n\n"+exe({"tipo":"resumen"}),parse_mode="Markdown")
    except Exception as e: logger.error(f"report: {e}")

async def check_balance(app):
    try:
        ctx=get_ctx(force=True); MIN_U=float(os.environ.get("MIN_BALANCE_UYU","500")); MIN_D=float(os.environ.get("MIN_BALANCE_USD","50"))
        al=[]
        for c,v in ctx.get("saldos",{}).items():
            if "UYU" in c and 0<v<MIN_U: al.append(f"⚠️ {c}: $ {v:,.2f}")
            elif "USD" in c and 0<v<MIN_D: al.append(f"⚠️ {c}: U$S {v:,.2f}")
        if al: await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,text="🚨 *SALDO BAJO*\n\n"+"\n".join(al),parse_mode="Markdown")
    except Exception as e: logger.error(f"balance: {e}")

def main():
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("setup",cmd_setup))
    app.add_handler(CommandHandler("resumen",cmd_resumen))
    app.add_handler(CommandHandler("saldo",cmd_saldo))
    app.add_handler(CommandHandler("limpiar",cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_msg))
    sch=AsyncIOScheduler(timezone=UYU_TZ)
    sch.add_job(weekly_report,"cron",day_of_week="mon",hour=9,minute=0,args=[app])
    sch.add_job(check_balance,"cron",hour=8,minute=0,args=[app])
    sch.start(); logger.info("🤖 KkaynBot v5!"); app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
