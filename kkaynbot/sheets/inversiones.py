"""Pestaña Inversiones: storage crudo (Inv Data) + vista rearmada por plataforma.

La vista "Inversiones" muestra 2 secciones verticales (BINANCE cripto, XTB acciones);
dentro de cada una los activos van en bloques horizontales (FECHA + MONTO) con su
total invertido. Se reconstruye entera desde el storage en cada cambio, igual que
"Por Cuenta" se reconstruye desde "Cuentas".
"""
import logging

import gspread

from config import INV_DISPLAY_TAB, INV_STORAGE_TAB, INVERSIONES
from kkaynbot.sheets.client import get_ws, reset_ws, ss
from kkaynbot.sheets.format import col_letter, cw, fr, mg, rh
from kkaynbot.sheets.theme import (AZ_CLA, AZ_MED, AZ_OSC, BLANCO, GR_CLA, GR_OSC,
                                   MOR_MED, MORADO, T_BLA, T_OSC, TURQ)
from kkaynbot.utils.helpers import sf, with_retry

logger = logging.getLogger(__name__)

_STORAGE_HEADERS = ["FECHA", "PLATAFORMA", "ACTIVO", "MONTO", "MONEDA",
                    "CUENTA ORIGEN", "COTIZACIÓN", "NOTAS"]
_SECTION_BG = {"BINANCE": MOR_MED, "XTB": AZ_MED}
_MAX_ACTIVOS = max(len(c["activos"]) for c in INVERSIONES.values())
_NCOLS = _MAX_ACTIVOS * 3 - 1  # 2 cols por activo + 1 separador entre bloques


# ────────────────────────────── creación / storage ──────────────────────────────

def ensure_inv_tabs() -> list:
    """Crea el storage (Inv Data) y la vista (Inversiones) si faltan. Devuelve las creadas."""
    sp = ss()
    existentes = {w.title for w in sp.worksheets()}
    creadas = []
    if INV_STORAGE_TAB not in existentes:
        w = sp.add_worksheet(INV_STORAGE_TAB, rows=1000, cols=8)
        sid = w._properties["sheetId"]
        w.batch_update([
            {"range": "A1", "values": [["🗃️  INVERSIONES — STORAGE (no tocar a mano)"]]},
            {"range": "A3", "values": [_STORAGE_HEADERS]},
        ])
        sp.batch_update({"requests": [
            fr(sid, 1, 1, 1, 8, bold=True, bg=AZ_OSC, fg=T_BLA, sz=11, al="CENTER"),
            mg(sid, 1, 1, 1, 8), rh(sid, 1, 32),
            fr(sid, 3, 1, 3, 8, bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
            {"updateSheetProperties": {"properties": {"sheetId": sid,
             "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}},
        ]})
        creadas.append(INV_STORAGE_TAB)
    if INV_DISPLAY_TAB not in existentes:
        w = sp.add_worksheet(INV_DISPLAY_TAB, rows=500, cols=max(16, _NCOLS))
        setup_display_header(w)
        creadas.append(INV_DISPLAY_TAB)
    if creadas:
        reset_ws()
    return creadas


def setup_display_header(w) -> None:
    """Título + anchos de columna + fila congelada de la vista Inversiones.

    Agranda la grilla si hace falta: la pestaña vieja tenía 7 columnas y el nuevo
    diseño necesita hasta 16 (si no, Sheets rechaza escribir/limpiar más allá del borde).
    """
    need_cols = max(16, _NCOLS)
    if w.col_count < need_cols or w.row_count < 400:
        with_retry(w.resize, rows=max(w.row_count, 400), cols=max(w.col_count, need_cols))
    sid = w._properties["sheetId"]
    w.update(values=[["📈  REGISTRO DE INVERSIONES"]], range_name="A1")
    rqs = [fr(sid, 1, 1, 1, _NCOLS, bold=True, bg=MORADO, fg=T_BLA, sz=13, al="CENTER"),
           mg(sid, 1, 1, 1, _NCOLS), rh(sid, 1, 45),
           {"updateSheetProperties": {"properties": {"sheetId": sid,
            "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}}]
    for i in range(_MAX_ACTIVOS):
        rqs.append(cw(sid, 3 * i + 1, 96))   # FECHA
        rqs.append(cw(sid, 3 * i + 2, 82))   # MONTO
        if i < _MAX_ACTIVOS - 1:
            rqs.append(cw(sid, 3 * i + 3, 16))  # separador
    ss().batch_update({"requests": rqs})


def _storage_ws() -> gspread.Worksheet:
    try:
        return get_ws(INV_STORAGE_TAB)
    except gspread.WorksheetNotFound:
        ensure_inv_tabs()
        return get_ws(INV_STORAGE_TAB)


def add_investment(fecha: str, plataforma: str, activo: str, monto: float,
                   moneda: str = "USD", cuenta: str = "", cotiz=None, notas: str = "") -> None:
    """Agrega una fila al storage de inversiones."""
    w = _storage_ws()
    with_retry(w.append_row, [fecha, plataforma, activo, monto, moneda, cuenta,
                              cotiz if cotiz is not None else "", notas])


def get_investments() -> dict:
    """Lee el storage y agrupa los movimientos por activo: {activo: [registros]}."""
    w = _storage_ws()
    vals = with_retry(w.get_all_values)
    recs: dict = {}
    for r in vals[3:]:
        if len(r) >= 4 and r[2].strip():
            monto = sf(r[3])
            if monto <= 0:
                continue
            activo = r[2].strip().upper()
            recs.setdefault(activo, []).append({
                "fecha": r[0], "monto": monto,
                "moneda": r[4].strip() if len(r) > 4 else "USD",
                "cuenta": r[5].strip() if len(r) > 5 else "",
                "cotiz": r[6] if len(r) > 6 else "",
                "notas": r[7] if len(r) > 7 else "",
            })
    return recs


def investment_totals(recs: dict = None) -> dict:
    """Total invertido por activo. Reusa `recs` si se lo pasan (evita releer)."""
    if recs is None:
        recs = get_investments()
    return {a: sum(x["monto"] for x in lst) for a, lst in recs.items()}


# ────────────────────────────── vista ──────────────────────────────

def _build_layout(sid: int, recs: dict):
    """Arma (valores, formato) de la vista. Función pura: testeable sin tocar la API."""
    bv = []   # [{"range","values"}]
    rqs = []  # requests de formato
    cur = 3   # fila 1 = título, fila 2 = spacer

    for plataforma, cfg in INVERSIONES.items():
        activos = cfg["activos"]
        width = len(activos) * 3 - 1
        moneda = cfg["moneda"]
        sec_bg = _SECTION_BG.get(plataforma, TURQ)
        icono = "🪙" if plataforma == "BINANCE" else "📊"

        # ── fila: encabezado de sección ──
        bv.append({"range": f"A{cur}", "values": [[f"{icono}  {plataforma} — {cfg['tipo']} ({moneda})"]]})
        rqs += [fr(sid, cur, 1, cur, width, bold=True, bg=sec_bg, fg=T_BLA, sz=12, al="CENTER"),
                mg(sid, cur, 1, cur, width), rh(sid, cur, 30)]
        cur += 1

        # ── fila: títulos de cada activo ──
        for i, a in enumerate(activos):
            c = 3 * i + 1
            bv.append({"range": f"{col_letter(c)}{cur}", "values": [[a]]})
            rqs += [fr(sid, cur, c, cur, c + 1, bold=True, bg=TURQ, fg=T_BLA, sz=11, al="CENTER"),
                    mg(sid, cur, c, cur, c + 1)]
        rqs.append(rh(sid, cur, 26))
        cur += 1

        # ── fila: encabezados de columna (FECHA | MONTO) ──
        for i, a in enumerate(activos):
            c = 3 * i + 1
            bv.append({"range": f"{col_letter(c)}{cur}", "values": [["FECHA", "MONTO"]]})
            rqs.append(fr(sid, cur, c, cur, c + 1, bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"))
        rqs.append(rh(sid, cur, 22))
        cur += 1

        # ── filas de datos (cada activo crece independiente; se rellena parejo) ──
        maxlen = max([len(recs.get(a, [])) for a in activos] + [1])
        data_start = cur
        for i, a in enumerate(activos):
            c = 3 * i + 1
            lst = recs.get(a, [])
            for j in range(maxlen):
                fila = data_start + j
                if j < len(lst):
                    rec = lst[j]
                    fecha_corta = (rec["fecha"] or "").split(" ")[0]
                    bv.append({"range": f"{col_letter(c)}{fila}",
                               "values": [[fecha_corta, rec["monto"]]]})
                    rqs.append(fr(sid, fila, c, fila, c + 1, bg=GR_CLA, fg=T_OSC, al="CENTER"))
                else:
                    rqs.append(fr(sid, fila, c, fila, c + 1, bg=BLANCO))
        for j in range(maxlen):
            rqs.append(rh(sid, data_start + j, 22))
        cur = data_start + maxlen

        # ── fila: total por activo ──
        totales = investment_totals(recs)
        for i, a in enumerate(activos):
            c = 3 * i + 1
            bv.append({"range": f"{col_letter(c)}{cur}", "values": [["TOTAL", totales.get(a, 0.0)]]})
            rqs.append(fr(sid, cur, c, cur, c + 1, bold=True, bg=AZ_CLA, fg=AZ_OSC, al="CENTER"))
        rqs.append(rh(sid, cur, 26))
        cur += 1

        # ── 2 filas separadoras entre secciones ──
        for _ in range(2):
            rqs += [fr(sid, cur, 1, cur, _NCOLS, bg=BLANCO), rh(sid, cur, 10)]
            cur += 1

    return bv, rqs


def update_inversiones_view() -> None:
    """Reconstruye la vista Inversiones desde el storage."""
    try:
        recs = get_investments()
    except Exception as e:
        logger.warning(f"inversiones: no pude leer el storage ({e})")
        return
    sp = ss()
    try:
        w = get_ws(INV_DISPLAY_TAB)
    except gspread.WorksheetNotFound:
        ensure_inv_tabs()
        w = get_ws(INV_DISPLAY_TAB)
    sid = w._properties["sheetId"]
    w.batch_clear(["A2:P500"])
    bv, rqs = _build_layout(sid, recs)
    unmerge = {"unmergeCells": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 500,
               "startColumnIndex": 0, "endColumnIndex": 16}}}
    if rqs:
        with_retry(sp.batch_update, {"requests": [unmerge] + rqs})
    if bv:
        with_retry(w.batch_update, bv)
