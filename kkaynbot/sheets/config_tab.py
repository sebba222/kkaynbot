"""Pestañas auxiliares: Config (presupuestos y metas) y Cotización (historial USD/UYU).

Se crean automáticamente si no existen (migración transparente): las 4 pestañas
originales no se tocan.
"""
import logging
import time
import unicodedata
from datetime import datetime

import gspread

from config import CONFIG_TTL_SECONDS, UYU_TZ
from kkaynbot.sheets.client import get_ws, reset_ws, ss
from kkaynbot.sheets.format import fr, mg, rh
from kkaynbot.sheets.theme import AZ_MED, AZ_OSC, GR_OSC, T_BLA, TURQ
from kkaynbot.utils.helpers import sf, with_retry

logger = logging.getLogger(__name__)

CONFIG_TAB = "Config"
RATE_TAB = "Cotización"
_DATA_START = 5  # fila donde empiezan los datos en Config (1-indexed)

_cfg_cache = {"ts": 0.0, "data": None}


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def inv_cfg_cache() -> None:
    _cfg_cache["ts"] = 0.0
    _cfg_cache["data"] = None


def ensure_extra_tabs() -> list:
    """Crea las pestañas Config y Cotización si faltan. Devuelve las que creó."""
    sp = ss()
    existentes = {w.title for w in sp.worksheets()}
    creadas = []
    if CONFIG_TAB not in existentes:
        w = sp.add_worksheet(CONFIG_TAB, rows=60, cols=10)
        sid = w._properties["sheetId"]
        w.batch_update([
            {"range": "A1", "values": [["⚙️  CONFIGURACIÓN — PRESUPUESTOS Y METAS"]]},
            {"range": "A3", "values": [["PRESUPUESTOS MENSUALES (UYU)"]]},
            {"range": "A4", "values": [["CATEGORÍA", "MONTO/MES"]]},
            {"range": "D3", "values": [["METAS DE AHORRO"]]},
            {"range": "D4", "values": [["NOMBRE", "OBJETIVO", "MONEDA", "FECHA LÍMITE", "BASE", "CREADA"]]},
        ])
        sp.batch_update({"requests": [
            fr(sid, 1, 1, 1, 9, bold=True, bg=AZ_OSC, fg=T_BLA, sz=13, al="CENTER"),
            mg(sid, 1, 1, 1, 9), rh(sid, 1, 40),
            fr(sid, 3, 1, 3, 2, bold=True, bg=TURQ, fg=T_BLA, al="CENTER"), mg(sid, 3, 1, 3, 2),
            fr(sid, 3, 4, 3, 9, bold=True, bg=TURQ, fg=T_BLA, al="CENTER"), mg(sid, 3, 4, 3, 9),
            fr(sid, 4, 1, 4, 2, bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
            fr(sid, 4, 4, 4, 9, bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
        ]})
        creadas.append(CONFIG_TAB)
    if RATE_TAB not in existentes:
        w = sp.add_worksheet(RATE_TAB, rows=2000, cols=3)
        sid = w._properties["sheetId"]
        w.batch_update([
            {"range": "A1", "values": [["💱  HISTORIAL DE COTIZACIÓN USD/UYU"]]},
            {"range": "A3", "values": [["FECHA", "UYU POR USD"]]},
        ])
        sp.batch_update({"requests": [
            fr(sid, 1, 1, 1, 2, bold=True, bg=AZ_OSC, fg=T_BLA, sz=12, al="CENTER"),
            mg(sid, 1, 1, 1, 2), rh(sid, 1, 36),
            fr(sid, 3, 1, 3, 2, bold=True, bg=AZ_MED, fg=T_BLA, al="CENTER"),
        ]})
        creadas.append(RATE_TAB)
    if creadas:
        reset_ws()
        inv_cfg_cache()
        logger.info(f"Pestañas creadas por migración automática: {creadas}")
    return creadas


def _tab(title: str) -> gspread.Worksheet:
    """Worksheet auxiliar; si no existe, dispara la migración automática."""
    try:
        return get_ws(title)
    except gspread.WorksheetNotFound:
        ensure_extra_tabs()
        return get_ws(title)


def get_config(force: bool = False) -> dict:
    """Lee presupuestos y metas de la pestaña Config (con caché de 5 minutos).

    Devuelve {"presupuestos": {categoria: monto_uyu}, "metas": [dict, ...]}.
    """
    now = time.time()
    if not force and _cfg_cache["data"] is not None and now - _cfg_cache["ts"] < CONFIG_TTL_SECONDS:
        return _cfg_cache["data"]
    try:
        w = _tab(CONFIG_TAB)
        vals = with_retry(w.get_all_values)
        presupuestos = {}
        metas = []
        for r in vals[_DATA_START - 1:]:
            if len(r) >= 2 and r[0].strip() and sf(r[1]) > 0:
                presupuestos[r[0].strip()] = sf(r[1])
            if len(r) >= 5 and r[3].strip() and sf(r[4]) > 0:
                metas.append({
                    "nombre": r[3].strip(),
                    "objetivo": sf(r[4]),
                    "moneda": (r[5].strip() if len(r) > 5 and r[5].strip() else "UYU"),
                    "fecha_limite": r[6].strip() if len(r) > 6 else "",
                    "base": sf(r[7]) if len(r) > 7 else 0.0,
                    "creada": r[8].strip() if len(r) > 8 else "",
                })
        data = {"presupuestos": presupuestos, "metas": metas}
        _cfg_cache["ts"] = now
        _cfg_cache["data"] = data
        return data
    except Exception as e:
        logger.error(f"get_config: {e}")
        return {"presupuestos": {}, "metas": []}


def _find_row(vals: list, col: int, nombre: str) -> tuple:
    """(fila del nombre si existe, primera fila libre) en la columna dada (0-indexed)."""
    fila = None
    libre = None
    for i, r in enumerate(vals[_DATA_START - 1:], start=_DATA_START):
        celda = r[col].strip() if len(r) > col else ""
        if celda and _plain(celda) == _plain(nombre):
            fila = i
            break
        if not celda and libre is None:
            libre = i
    return fila, libre


def set_budget(categoria: str, monto: float) -> str:
    """Crea, actualiza o borra (monto <= 0) el presupuesto mensual de una categoría."""
    w = _tab(CONFIG_TAB)
    vals = with_retry(w.get_all_values)
    fila, libre = _find_row(vals, 0, categoria)
    if monto <= 0:
        if fila:
            with_retry(w.batch_clear, [f"A{fila}:B{fila}"])
            inv_cfg_cache()
            return f"✅ Presupuesto de *{categoria}* eliminado."
        return f"No había presupuesto definido para {categoria}."
    destino = fila or libre or len(vals) + 1
    with_retry(w.update, values=[[categoria, monto]], range_name=f"A{destino}")
    inv_cfg_cache()
    return f"✅ Presupuesto de *{categoria}*: $ {monto:,.0f} por mes. Te aviso cuando te acerques."


def set_goal(nombre: str, objetivo: float, moneda: str = "USD",
             fecha_limite: str = "", base: float = 0.0) -> str:
    """Crea, actualiza o borra (objetivo <= 0) una meta de ahorro.

    `base` es el total actual en esa moneda: el progreso se mide como lo
    ahorrado desde que se creó la meta.
    """
    w = _tab(CONFIG_TAB)
    vals = with_retry(w.get_all_values)
    fila, libre = _find_row(vals, 3, nombre)
    if objetivo <= 0:
        if fila:
            with_retry(w.batch_clear, [f"D{fila}:I{fila}"])
            inv_cfg_cache()
            return f"✅ Meta *{nombre}* eliminada."
        return f"No encontré una meta llamada {nombre}."
    destino = fila or libre or len(vals) + 1
    creada = datetime.now(UYU_TZ).strftime("%d/%m/%Y")
    sym = "U$S" if moneda == "USD" else "$"
    with_retry(w.update, values=[[nombre, objetivo, moneda, fecha_limite, round(base, 2), creada]],
               range_name=f"D{destino}")
    inv_cfg_cache()
    extra = f" antes del {fecha_limite}" if fecha_limite else ""
    return (f"🎯 Meta *{nombre}*: ahorrar {sym} {objetivo:,.2f}{extra}.\n"
            f"Arranco a contar desde tu total actual en {moneda}. Consultá el avance con /metas.")


def log_rate(rate: float) -> None:
    """Guarda la cotización del día en la pestaña Cotización (una fila por día)."""
    w = _tab(RATE_TAB)
    hoy = datetime.now(UYU_TZ).strftime("%d/%m/%Y")
    vals = with_retry(w.get_all_values)
    if vals and vals[-1] and vals[-1][0].strip() == hoy:
        with_retry(w.update, values=[[hoy, round(rate, 2)]], range_name=f"A{len(vals)}")
    else:
        with_retry(w.append_row, [hoy, round(rate, 2)])
