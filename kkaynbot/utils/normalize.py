"""Normalización de nombres de cuenta con tolerancia a variantes uruguayas."""
import re
import unicodedata
from difflib import get_close_matches
from typing import Optional, Tuple

from config import ACTIVO_PLATAFORMA, CUENTAS, INV_ALIASES

# Palabras que identifican el banco/lugar
_BANCOS = {
    "bbva": "BBVA", "bva": "BBVA", "bbba": "BBVA",
    "itau": "Itaú", "ita": "Itaú", "itaw": "Itaú",
    "efectivo": "Efectivo", "cash": "Efectivo", "plata": "Efectivo",
    "billetera": "Efectivo", "mano": "Efectivo", "bolsillo": "Efectivo",
    "casa": "Efectivo",
}

# Palabras que identifican la moneda
_MONEDAS = {
    "uyu": "UYU", "peso": "UYU", "pesos": "UYU", "pesitos": "UYU", "uy": "UYU",
    "usd": "USD", "dolar": "USD", "dolares": "USD", "verdes": "USD",
    "dls": "USD", "us": "USD", "usds": "USD", "dolucas": "USD",
}


def _clean(s: str) -> str:
    """Minúsculas, sin tildes, con símbolos de moneda convertidos a palabras."""
    s = (s or "").lower().strip()
    s = s.replace("u$s", " usd ").replace("us$", " usd ").replace("$", " pesos ")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_CANONICAS = {_clean(c): c for c in CUENTAS}


def nc(n: Optional[str], moneda: Optional[str] = None) -> Optional[str]:
    """Normaliza un nombre de cuenta a uno canónico de CUENTAS.

    Entiende variantes ('bbva pesos', 'itau dolares', 'efectivo', 'cash', 'u$s'),
    typos leves (matching difuso) y usa `moneda` como pista cuando el usuario no
    la especificó. Si no puede resolver, devuelve el valor original; la validación
    final la hace quien registra el movimiento.
    """
    if not n:
        return n
    limpio = _clean(n)
    if limpio in _CANONICAS:
        return _CANONICAS[limpio]

    tokens = limpio.split()
    banco = next((_BANCOS[t] for t in tokens if t in _BANCOS), None)
    mon = next((_MONEDAS[t] for t in tokens if t in _MONEDAS), None)

    if banco is None:
        # tolerar typos: 'efectvo', 'bbav', 'itua'
        for t in tokens:
            m = get_close_matches(t, _BANCOS.keys(), n=1, cutoff=0.75)
            if m:
                banco = _BANCOS[m[0]]
                break
    if mon is None:
        for t in tokens:
            m = get_close_matches(t, _MONEDAS.keys(), n=1, cutoff=0.8)
            if m:
                mon = _MONEDAS[m[0]]
                break
    if mon is None and moneda in ("UYU", "USD"):
        mon = moneda

    if banco and mon:
        return f"{banco} {mon}"
    if banco:
        return f"{banco} UYU"  # sin más datos, pesos es el default razonable en Uruguay
    return n


def valid_account(name: Optional[str]) -> bool:
    """True si el nombre es exactamente una cuenta canónica."""
    return name in CUENTAS


def resolve_activo(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Mapea lo que escribe Seba a (plataforma, activo) canónicos.

    Entiende 'btc'→(BINANCE, BITCOIN), 'sp500'→(XTB, SP500), typos leves incluidos.
    Devuelve (None, None) si no reconoce ningún activo.
    """
    limpio = _clean(text)
    if not limpio:
        return None, None
    # coincidencia directa de la frase completa (cubre "sp 500", "s&p500" ya normalizado)
    directo = limpio.replace(" ", "")
    for cand in (limpio, directo):
        if cand in INV_ALIASES:
            activo = INV_ALIASES[cand]
            return ACTIVO_PLATAFORMA.get(activo), activo
    # token por token, con tolerancia a typos
    for tok in limpio.split():
        if tok in INV_ALIASES:
            activo = INV_ALIASES[tok]
            return ACTIVO_PLATAFORMA.get(activo), activo
        m = get_close_matches(tok, INV_ALIASES.keys(), n=1, cutoff=0.85)
        if m:
            activo = INV_ALIASES[m[0]]
            return ACTIVO_PLATAFORMA.get(activo), activo
    return None, None
