"""Utilidades compartidas: parsing de montos, saldos, cotización y reintentos."""
import logging
import random
import re
import time
from typing import Any, Callable, Optional

import requests

from config import (RETRY_BASE_SECONDS, RETRY_MAX_SECONDS, SHEETS_MAX_RETRIES,
                    USD_RATE_FALLBACK, USD_RATE_TTL)

logger = logging.getLogger(__name__)

# Símbolos/monedas y espacios que se limpian antes de parsear un número
_CURRENCY_JUNK = re.compile(r"(u\$s|us\$|usd|uyu|\$|\s)", re.IGNORECASE)
# Notación abreviada: "1k"=1000, "1k5"=1500, "2.5k"/"2,5k"=2500
_K_NOTATION = re.compile(r"^(\d+)(?:[.,](\d+))?k(\d*)$", re.IGNORECASE)

_rate_cache = {"ts": 0.0, "rate": None}


def _normalizar_separadores(s: str) -> str:
    """Resuelve la ambigüedad coma/punto (formato uruguayo: punto=miles, coma=decimal)."""
    if "," in s and "." in s:
        # el separador decimal es el que aparece más a la derecha
        if s.rfind(",") > s.rfind("."):
            return s.replace(".", "").replace(",", ".")
        return s.replace(",", "")
    if "," in s:
        # una coma con 1-2 dígitos después es decimal; si no, es separador de miles
        return s.replace(",", ".") if len(s.split(",")[-1]) <= 2 else s.replace(",", "")
    return s


def sf(v: Any) -> float:
    """Convierte un valor de celda de Sheets a float. Tolera '', None, '1.234,56', '$ 300'."""
    if v is None:
        return 0.0
    s = _CURRENCY_JUNK.sub("", str(v))
    if not s:
        return 0.0
    try:
        return float(_normalizar_separadores(s))
    except ValueError:
        return 0.0


def parse_amount(value: Any) -> Optional[float]:
    """Valida y normaliza un monto que viene del LLM o del usuario.

    Acepta números, '1.000,50', '$300', '1k' (1000), '1k5' (1500), '2.5k' (2500).
    Devuelve None si no es un monto válido y positivo (red de seguridad post-LLM).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        m = float(value)
        return m if 0 < m < 1e9 else None
    if not isinstance(value, str):
        return None
    s = _CURRENCY_JUNK.sub("", value.strip().lower())
    if not s:
        return None
    km = _K_NOTATION.match(s)
    if km:
        entero, dec, resto = km.groups()
        if resto:            # "1k5" → 1500, "1k50" → 1500
            m = float(entero) * 1000 + float(resto) * (1000 / 10 ** len(resto))
        elif dec:            # "2.5k" / "2,5k" → 2500
            m = float(f"{entero}.{dec}") * 1000
        else:                # "3k" → 3000
            m = float(entero) * 1000
        return m if 0 < m < 1e9 else None
    s = _normalizar_separadores(s)
    if "." in s:
        # '1.000' escrito por un uruguayo es mil; '10.50' es decimal
        ent, dec = s.rsplit(".", 1)
        if len(dec) == 3 and ent.replace("-", "").isdigit():
            s = s.replace(".", "")
    try:
        m = float(s)
    except ValueError:
        return None
    return m if 0 < m < 1e9 else None


def bal(data: list, cuenta: str) -> float:
    """Calcula el saldo de una cuenta sumando ingresos y restando egresos de todas las filas."""
    b = 0.0
    for r in data[3:]:
        if len(r) >= 7 and r[3] == cuenta:
            b += sf(r[5]) - sf(r[6])
    return b


def usd_rate() -> float:
    """Cotización USD/UYU con caché de 1 hora.

    Si la API falla, devuelve la última cotización conocida (aunque esté vencida)
    en lugar de un valor fijo; el fallback duro solo aplica si nunca hubo una real.
    """
    now = time.time()
    if _rate_cache["rate"] and now - _rate_cache["ts"] < USD_RATE_TTL:
        return _rate_cache["rate"]
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD",
                         timeout=5).json()["rates"].get("UYU")
        if r and float(r) > 0:
            _rate_cache["ts"] = now
            _rate_cache["rate"] = float(r)
            return _rate_cache["rate"]
    except Exception as e:
        logger.warning(f"usd_rate: sin cotización fresca ({e})")
    if _rate_cache["rate"]:
        return _rate_cache["rate"]
    return USD_RATE_FALLBACK


_TRANSIENT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "RATE_LIMIT", "500", "502", "503",
                      "backendError", "internalError", "Connection", "Timeout", "timed out")


def with_retry(func: Callable, *args, max_retries: int = SHEETS_MAX_RETRIES, **kwargs):
    """Ejecuta una función con backoff exponencial + jitter ante errores transitorios.

    Bloqueante: las acciones que la usan corren en un thread (run_blocking),
    así el event loop del bot nunca se congela.
    """
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e)
            transitorio = any(t in err for t in _TRANSIENT_MARKERS)
            if not transitorio or attempt == max_retries - 1:
                raise
            wait = min(RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 3), RETRY_MAX_SECONDS)
            logger.warning(f"Sheets transitorio (intento {attempt + 1}/{max_retries}): "
                           f"{err[:120]} — esperando {wait:.0f}s")
            time.sleep(wait)
    return None
