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
_gs_client = None  # singleton — se crea una sola vez y se reutiliza

def gs_client():
    global _gs_client
    if _gs_client is None:
        _gs_client = gspread.authorize(Credentials.from_service_account_info(
            json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON")), scopes=SCOPES))
    return _gs_client

def ss():
    global _gs_client
    try:
        return gs_client().open_by_key(SPREADSHEET_ID)
    except Exception:
        _gs_client = None  # fuerza re-auth en el próximo intento
        raise

def get_ctx(force=False):
    global _cache
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < 20:
        return _cache["data"]
    try:
        sp = ss()
        wc = sp.worksheet("Cuentas")
        data = with_retry(wc.get_all_values)
        saldos = {c: bal(data, c) for c in CUENTAS}
        ult = []
        for i, r in enumerate(data[3:], start=4):
            if len(r) >= 7 and (r[5] or r[6]):
                ult.append({"fila": i, "fecha": r[0], "descripcion": r[1], "categoria": r[2],
                             "cuenta": r[3], "moneda": r[4], "ingreso": r[5], "egreso": r[6],
                             "saldo": r[7] if len(r) > 7 else ""})
        ult = ult[-10:]
        wi = sp.worksheet("Inversiones")
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
        res = {"saldos": saldos, "ult": ult, "inv": inv, "rate": rate,
               "iu": iu, "eu": eu, "id": id_, "ed": ed, "movs": movs, "data": data}
        _cache = {"ts": time.time(), "data": res}; return res
    except Exception as e:
        logger.error(f"ctx: {e}"); return {}

def inv_cache():
    _cache["ts"] = 0.0; _cache["data"] = None
