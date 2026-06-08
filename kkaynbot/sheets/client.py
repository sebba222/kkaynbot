import os
import json
import time
import logging
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

from config import SCOPES, SPREADSHEET_ID, CUENTAS, UYU_TZ
from kkaynbot.utils.helpers import sf, bal, usd_rate, with_retry

logger = logging.getLogger(__name__)

_cache = {"ts": 0.0, "data": None}
_gs_client = None   # gspread client — se crea una sola vez
_ss = None          # Spreadsheet object — se crea una sola vez
_ws = {}            # {title: Worksheet} — se populan al abrir el spreadsheet

def gs_client():
    global _gs_client
    if _gs_client is None:
        _gs_client = gspread.authorize(Credentials.from_service_account_info(
            json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON")), scopes=SCOPES))
    return _gs_client

def ss():
    """Retorna el Spreadsheet cacheado. En el primer llamado abre y cachea todos los worksheets."""
    global _ss, _ws, _gs_client
    if _ss is None:
        try:
            _ss = gs_client().open_by_key(SPREADSHEET_ID)
            _ws = {w.title: w for w in _ss.worksheets()}  # 1 sola llamada API para todos
        except Exception:
            _gs_client = None
            _ss = None
            _ws = {}
            raise
    return _ss

def get_ws(title: str) -> gspread.Worksheet:
    """Retorna un Worksheet cacheado. Si no está en caché, lo busca (y cachea)."""
    if title not in _ws:
        _ws[title] = ss().worksheet(title)
    return _ws[title]

def reset_ws():
    """Invalida el caché de worksheets. Llamar después de setup_sheets()."""
    global _ss, _ws
    _ss = None
    _ws = {}

def get_ctx(force=False):
    global _cache
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < 20:
        return _cache["data"]
    try:
        wc   = get_ws("Cuentas")
        data = with_retry(wc.get_all_values)
        saldos = {c: bal(data, c) for c in CUENTAS}
        ult = []
        for i, r in enumerate(data[3:], start=4):
            if len(r) >= 7 and (r[5] or r[6]):
                ult.append({"fila": i, "fecha": r[0], "descripcion": r[1], "categoria": r[2],
                             "cuenta": r[3], "moneda": r[4], "ingreso": r[5], "egreso": r[6],
                             "saldo": r[7] if len(r) > 7 else ""})
        ult = ult[-10:]
        wi  = get_ws("Inversiones")
        inv = [{"activo": r[1], "monto": r[2], "moneda": r[3], "fecha": r[0]}
               for r in with_retry(wi.get_all_values)[3:] if len(r) >= 4 and r[1]]
        rate = usd_rate(); now_dt = datetime.now(UYU_TZ)
        iu = eu = id_ = ed = 0.0
        for r in data[3:]:
            if len(r) >= 7:
                try:
                    f = datetime.strptime(r[0].split(" ")[0], "%d/%m/%Y")
                    if f.month == now_dt.month and f.year == now_dt.year:
                        if "USD" in (r[4] if len(r) > 4 else ""): id_ += sf(r[5]); ed += sf(r[6])
                        else: iu += sf(r[5]); eu += sf(r[6])
                except: pass
        movs = [r for r in data[3:] if len(r) >= 7 and (r[5] or r[6])]
        res  = {"saldos": saldos, "ult": ult, "inv": inv, "rate": rate,
                "iu": iu, "eu": eu, "id": id_, "ed": ed, "movs": movs, "data": data}
        _cache = {"ts": time.time(), "data": res}
        return res
    except Exception as e:
        logger.error(f"ctx: {e}")
        return {}

def inv_cache():
    _cache["ts"] = 0.0
    _cache["data"] = None
