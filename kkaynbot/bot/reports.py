"""Reportes y exportación: saldos con variación, resumen mensual/semanal, CSV, metas."""
import csv
import io
import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Optional, Tuple

from config import CUENTAS, UYU_TZ
from kkaynbot.utils.helpers import sf

logger = logging.getLogger(__name__)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre"]

# Categorías que no son gasto real (movimientos internos)
_NO_GASTO = {"transferencia", "ajuste", "cambio"}


def _fecha_mov(r: list) -> Optional[datetime]:
    """Parsea la fecha de una fila de movimientos ('dd/mm/yyyy HH:MM')."""
    try:
        return datetime.strptime(r[0].split(" ")[0], "%d/%m/%Y")
    except (ValueError, IndexError, AttributeError):
        return None


def _es_usd(r: list) -> bool:
    return "USD" in (r[4] if len(r) > 4 else "")


def gastos_mes_por_categoria(ctx: dict, year: int, month: int) -> dict:
    """Egresos reales del mes por categoría, todo convertido a UYU."""
    rate = ctx.get("rate", 40)
    tot: dict = {}
    for r in ctx.get("movs", []):
        f = _fecha_mov(r)
        if not f or f.year != year or f.month != month:
            continue
        e = sf(r[6])
        if e <= 0:
            continue
        cat = (r[2] or "Otro").strip() or "Otro"
        if cat.lower() in _NO_GASTO:
            continue
        tot[cat] = tot.get(cat, 0.0) + (e * rate if _es_usd(r) else e)
    return tot


def _variacion(actual: float, anterior: float) -> str:
    if not anterior:
        return ""
    pct = (actual - anterior) / abs(anterior) * 100
    flecha = "📈" if pct >= 0 else "📉"
    return f"  {flecha} {pct:+.1f}% vs fin del mes pasado"


def saldo_msg(ctx: dict) -> str:
    """Texto de /saldo: saldos por cuenta + totales con variación vs mes anterior."""
    s = ctx.get("saldos", {})
    rate = ctx.get("rate", 40)
    tu = sum(v for k, v in s.items() if "UYU" in k)
    td = sum(v for k, v in s.items() if "USD" in k)
    # saldo al cierre del mes pasado = actual menos el neto de este mes
    prev_u = tu - (ctx.get("iu", 0) - ctx.get("eu", 0))
    prev_d = td - (ctx.get("id", 0) - ctx.get("ed", 0))
    lines = ["💳 *SALDOS ACTUALES*\n"]
    for c in CUENTAS:
        sym = "$" if "UYU" in c else "U$S"
        lines.append(f"• {c}: {sym} {s.get(c, 0):,.2f}")
    lines.append("")
    lines.append(f"Total UYU: $ {tu:,.2f}{_variacion(tu, prev_u)}")
    lines.append(f"Total USD: U$S {td:,.2f}{_variacion(td, prev_d)}")
    lines.append(f"\n💱 1 USD = $ {rate:.2f}")
    return "\n".join(lines)


def saldos_query_msg(ctx: dict, filtro: str = None) -> str:
    """Saldos en lista vertical (una cuenta por renglón), para responder consultas.

    Si `filtro` matchea un banco o cuenta ("BBVA", "Itaú USD"), muestra solo esas;
    si no matchea nada, cae a mostrar todas.
    """
    s = ctx.get("saldos", {})
    rate = ctx.get("rate", 40)
    cuentas = CUENTAS
    if filtro:
        fp = _norm(filtro)
        matches = [c for c in CUENTAS if fp in _norm(c)]
        if matches:
            cuentas = matches
    lines = ["💳 *SALDOS*", ""]
    for c in cuentas:
        sym = "$" if "UYU" in c else "U$S"
        lines.append(f"• {c}: {sym} {s.get(c, 0):,.2f}")
    lines.append(f"\n💱 1 USD = $ {rate:.2f}")
    return "\n".join(lines)


def top_categorias_lines(ctx: dict, n: int = 3) -> list:
    """Líneas con el top de categorías de gasto del mes actual (en UYU)."""
    now = datetime.now(UYU_TZ)
    tot = gastos_mes_por_categoria(ctx, now.year, now.month)
    top = sorted(tot.items(), key=lambda kv: -kv[1])[:n]
    if not top:
        return []
    lines = ["", "🏷 *Top gastos del mes (en UYU):*"]
    medallas = ["🥇", "🥈", "🥉"]
    for i, (cat, v) in enumerate(top):
        pre = medallas[i] if i < len(medallas) else " •"
        lines.append(f"  {pre} {cat}: $ {v:,.0f}")
    return lines


def mes_msg(ctx: dict) -> str:
    """Texto de /mes: detalle del mes por categoría con comparación al mes anterior."""
    now = datetime.now(UYU_TZ)
    rate = ctx.get("rate", 40)
    movs = ctx.get("movs", [])

    def _bucket(y: int, m: int) -> Tuple[dict, dict]:
        ing = {"UYU": 0.0, "USD": 0.0}
        egr = {"UYU": 0.0, "USD": 0.0}
        for r in movs:
            f = _fecha_mov(r)
            if not f or f.year != y or f.month != m:
                continue
            mo = "USD" if _es_usd(r) else "UYU"
            ing[mo] += sf(r[5])
            egr[mo] += sf(r[6])
        return ing, egr

    ing, egr = _bucket(now.year, now.month)
    prev_y, prev_m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    cats = gastos_mes_por_categoria(ctx, now.year, now.month)
    cats_prev = gastos_mes_por_categoria(ctx, prev_y, prev_m)

    lines = [f"📅 *{MESES[now.month - 1].upper()} {now.year}*", ""]
    lines.append(f"💚 Ingresos: $ {ing['UYU']:,.0f} | U$S {ing['USD']:,.2f}")
    lines.append(f"💸 Egresos: $ {egr['UYU']:,.0f} | U$S {egr['USD']:,.2f}")
    lines.append(f"⚖️ Balance: $ {ing['UYU'] - egr['UYU']:,.0f} | U$S {ing['USD'] - egr['USD']:,.2f}")

    if cats:
        lines += ["", "🏷 *Gastos por categoría (en UYU):*"]
        for cat, v in sorted(cats.items(), key=lambda kv: -kv[1]):
            antes = cats_prev.get(cat)
            comp = f"  (mes pasado: $ {antes:,.0f})" if antes else ""
            lines.append(f"  • {cat}: $ {v:,.0f}{comp}")
    else:
        lines += ["", "Sin gastos registrados este mes. 🎉"]

    # ritmo de gasto y proyección simple del mes (solo UYU, gastos reales)
    gasto_real = sum(cats.values())
    if gasto_real > 0 and now.day > 0:
        por_dia = gasto_real / now.day
        # días del mes actual
        prox = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
        dias_mes = (prox - timedelta(days=1)).day
        lines += ["", f"📉 Ritmo: $ {por_dia:,.0f}/día → proyección del mes: $ {por_dia * dias_mes:,.0f}"]

    lines.append(f"\n💱 1 USD = $ {rate:.2f}")
    return "\n".join(lines)


def semana_msg(ctx: dict, dias: int = 7) -> str:
    """Texto de /semana: resumen de los últimos N días con top de categorías."""
    now = datetime.now(UYU_TZ)
    desde = (now - timedelta(days=dias)).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    rate = ctx.get("rate", 40)
    ing = {"UYU": 0.0, "USD": 0.0}
    egr = {"UYU": 0.0, "USD": 0.0}
    cats: dict = {}
    detalle = []
    for r in ctx.get("movs", []):
        f = _fecha_mov(r)
        if not f or f < desde:
            continue
        mo = "USD" if _es_usd(r) else "UYU"
        i, e = sf(r[5]), sf(r[6])
        ing[mo] += i
        egr[mo] += e
        cat = (r[2] or "Otro").strip() or "Otro"
        if e > 0 and cat.lower() not in _NO_GASTO:
            cats[cat] = cats.get(cat, 0.0) + (e * rate if mo == "USD" else e)
        sym = "$" if mo == "UYU" else "U$S"
        signo = "＋" if i > 0 else "－"
        detalle.append(f"  {signo} {f.strftime('%d/%m')} {r[1][:28]} — {sym} {(i or e):,.0f}")

    lines = [f"🗓 *ÚLTIMOS {dias} DÍAS*", ""]
    lines.append(f"💚 Ingresos: $ {ing['UYU']:,.0f} | U$S {ing['USD']:,.2f}")
    lines.append(f"💸 Egresos: $ {egr['UYU']:,.0f} | U$S {egr['USD']:,.2f}")
    lines.append(f"⚖️ Balance: $ {ing['UYU'] - egr['UYU']:,.0f} | U$S {ing['USD'] - egr['USD']:,.2f}")
    if cats:
        top = sorted(cats.items(), key=lambda kv: -kv[1])[:3]
        lines += ["", "🏷 *Dónde se fue la plata (UYU):*"]
        lines += [f"  • {cat}: $ {v:,.0f}" for cat, v in top]
    if detalle:
        lines += ["", f"📝 *Movimientos ({len(detalle)}):*"] + detalle[-15:]
    else:
        lines += ["", "Sin movimientos en la semana."]
    return "\n".join(lines)


def csv_export(ctx: dict, alcance: str = "mes") -> Tuple[str, bytes]:
    """Genera un CSV con los movimientos ('mes' actual o 'todo'). Devuelve (nombre, bytes)."""
    now = datetime.now(UYU_TZ)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Fecha", "Descripción", "Categoría", "Cuenta", "Moneda", "Ingreso", "Egreso", "Saldo"])
    filas = 0
    for r in ctx.get("movs", []):
        if alcance == "mes":
            f = _fecha_mov(r)
            if not f or f.year != now.year or f.month != now.month:
                continue
        w.writerow((r + [""] * 8)[:8])
        filas += 1
    nombre = (f"movimientos_{now.strftime('%Y_%m')}.csv" if alcance == "mes"
              else f"movimientos_todo_{now.strftime('%Y%m%d')}.csv")
    # BOM para que Excel abra bien las tildes
    return nombre, buf.getvalue().encode("utf-8-sig")


def _barra(pct: float) -> str:
    llenos = max(0, min(10, int(pct / 10)))
    return "█" * llenos + "░" * (10 - llenos)


def metas_msg(cfg: dict, ctx: dict) -> str:
    """Texto de /metas: progreso de cada meta de ahorro."""
    metas = cfg.get("metas", [])
    if not metas:
        return ("No tenés metas definidas todavía.\n"
                "Decime por ejemplo: _quiero ahorrar 500 USD para diciembre_.")
    s = ctx.get("saldos", {})
    lines = ["🎯 *METAS DE AHORRO*", ""]
    for m in metas:
        total = sum(v for k, v in s.items() if m["moneda"] in k)
        ahorrado = total - m.get("base", 0.0)
        objetivo = m.get("objetivo", 0.0) or 1.0
        pct = max(0.0, ahorrado / objetivo * 100)
        sym = "U$S" if m["moneda"] == "USD" else "$"
        limite = f" · límite {m['fecha_limite']}" if m.get("fecha_limite") else ""
        estado = " ✅" if pct >= 100 else ""
        lines.append(f"*{m['nombre']}* — {sym} {max(ahorrado, 0):,.2f} de {sym} {objetivo:,.2f}{estado}")
        lines.append(f"`{_barra(pct)}` {min(pct, 999):.0f}%{limite}")
        lines.append("")
    return "\n".join(lines).rstrip()


def presupuestos_msg(cfg: dict, ctx: dict) -> str:
    """Texto de /presupuesto sin argumentos: uso de cada presupuesto este mes."""
    pres = cfg.get("presupuestos", {})
    if not pres:
        return ("No tenés presupuestos definidos.\n"
                "Decime por ejemplo: _tope de 15k por mes para Alimentación_\n"
                "o usá: /presupuesto Alimentación 15000")
    now = datetime.now(UYU_TZ)
    gastos = gastos_mes_por_categoria(ctx, now.year, now.month)
    gastos_plain = { _norm(k): v for k, v in gastos.items() }
    lines = [f"📊 *PRESUPUESTOS — {MESES[now.month - 1]}*", ""]
    for cat, objetivo in sorted(pres.items()):
        gastado = gastos_plain.get(_norm(cat), 0.0)
        pct = gastado / objetivo * 100 if objetivo else 0
        icono = "🚨" if pct >= 100 else ("⚠️" if pct >= 80 else "✅")
        lines.append(f"{icono} *{cat}*: $ {gastado:,.0f} de $ {objetivo:,.0f}")
        lines.append(f"`{_barra(pct)}` {min(pct, 999):.0f}%")
        lines.append("")
    return "\n".join(lines).rstrip()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
