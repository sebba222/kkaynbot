"""Conexión a Google Sheets (singleton con reconexión) y caché del contexto financiero."""
import json
import logging
import time
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from config import (CACHE_TTL_SECONDS, CUENTAS, GOOGLE_CREDENTIALS_JSON,
                    SCOPES, SPREADSHEET_ID, ULT_MOVS_LIMIT, UYU_TZ)
from kkaynbot.utils.helpers import bal, sf, usd_rate, with_retry

logger = logging.getLogger(__name__)

_cache = {"ts": 0.0, "data": None}
_gs_client: Optional[gspread.Client] = None   # cliente gspread — se crea una sola vez
_ss = None                                    # Spreadsheet — se crea una sola vez
_ws: dict = {}                                # {title: Worksheet} — cacheados al abrir

# Señales de que la sesión/credencial murió y hay que reconectar
_AUTH_MARKERS = ("401", "UNAUTHENTICATED", "invalid_grant", "expired", "PERMISSION_DENIED")


def gs_client() -> gspread.Client:
    """Cliente gspread singleton. Nunca loggea el contenido de las credenciales."""
    global _gs_client
    if _gs_client is None:
        try:
            info = json.loads(GOOGLE_CREDENTIALS_JSON)
        except (TypeError, json.JSONDecodeError):
            raise RuntimeError("GOOGLE_CREDENTIALS_JSON ausente o inválido")
        _gs_client = gspread.authorize(Credentials.from_service_account_info(info, scopes=SCOPES))
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


def reset_ws() -> None:
    """Invalida el caché de worksheets. Llamar después de crear/borrar pestañas."""
    global _ss, _ws
    _ss = None
    _ws = {}


def _reset_connection() -> None:
    """Descarta el cliente y los worksheets para reconectar desde cero."""
    global _gs_client
    _gs_client = None
    reset_ws()


def get_ctx(force: bool = False) -> dict:
    """Contexto financiero completo (saldos, movimientos, inversiones, cotización).

    Cachea CACHE_TTL_SECONDS. Ante un error invalida el caché (para no servir
    datos viejos después de una falla) y, si el error parece de autenticación,
    resetea la conexión para que el próximo llamado reconecte.
    """
    global _cache
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL_SECONDS:
        return _cache["data"]
    try:
        wc = get_ws("Cuentas")
        data = with_retry(wc.get_all_values)
        saldos = {c: bal(data, c) for c in CUENTAS}
        ult = []
        for i, r in enumerate(data[3:], start=4):
            if len(r) >= 7 and (r[5] or r[6]):
                ult.append({"fila": i, "fecha": r[0], "descripcion": r[1], "categoria": r[2],
                            "cuenta": r[3], "moneda": r[4], "ingreso": r[5], "egreso": r[6],
                            "saldo": r[7] if len(r) > 7 else ""})
        ult = ult[-ULT_MOVS_LIMIT:]
        wi = get_ws("Inversiones")
        inv = [{"activo": r[1], "monto": r[2], "moneda": r[3], "fecha": r[0]}
               for r in with_retry(wi.get_all_values)[3:] if len(r) >= 4 and r[1]]
        rate = usd_rate()
        now_dt = datetime.now(UYU_TZ)
        iu = eu = id_ = ed = 0.0
        for r in data[3:]:
            if len(r) >= 7:
                try:
                    f = datetime.strptime(r[0].split(" ")[0], "%d/%m/%Y")
                    if f.month == now_dt.month and f.year == now_dt.year:
                        if "USD" in (r[4] if len(r) > 4 else ""):
                            id_ += sf(r[5]); ed += sf(r[6])
                        else:
                            iu += sf(r[5]); eu += sf(r[6])
                except (ValueError, IndexError):
                    pass
        movs = [r for r in data[3:] if len(r) >= 7 and (r[5] or r[6])]
        res = {"saldos": saldos, "ult": ult, "inv": inv, "rate": rate,
               "iu": iu, "eu": eu, "id": id_, "ed": ed, "movs": movs, "data": data}
        _cache = {"ts": time.time(), "data": res}
        return res
    except Exception as e:
        err = str(e)
        logger.error(f"get_ctx: {err[:200]}")
        inv_cache()  # no servir datos viejos después de una falla
        if any(t in err for t in _AUTH_MARKERS):
            logger.warning("Posible credencial vencida: reseteando conexión a Sheets.")
            _reset_connection()
        return {}


def inv_cache() -> None:
    """Invalida el caché del contexto financiero."""
    _cache["ts"] = 0.0
    _cache["data"] = None
