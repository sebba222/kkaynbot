"""Ejecución de acciones sobre la planilla: registrar, editar, transferir, resumir.

Todas las funciones son bloqueantes (gspread + time.sleep): los handlers las
ejecutan en un thread (run_blocking) para no congelar el bot.
Los errores esperables se levantan como ValueError con mensajes aptos para
mostrar directamente al usuario.
"""
import logging
import time
import unicodedata
from datetime import datetime
from typing import Callable, Dict

from config import BUDGET_WARN_PCT, CUENTAS, INVERSIONES, UYU_TZ
from kkaynbot.bot.reports import (gastos_mes_por_categoria, saldos_query_msg,
                                  top_categorias_lines)
from kkaynbot.sheets.client import get_ctx, get_ws, inv_cache
from kkaynbot.sheets.config_tab import get_config, set_budget, set_goal
from kkaynbot.sheets.global_view import update_global
from kkaynbot.sheets.inversiones import (add_investment, investment_totals,
                                         update_inversiones_view)
from kkaynbot.utils.helpers import bal, parse_amount, sf, usd_rate, with_retry
from kkaynbot.utils.normalize import nc, resolve_activo

logger = logging.getLogger(__name__)

FIRST_DATA_ROW = 4        # las filas 1-3 de "Cuentas" son encabezados
RECALC_BATCH = 50         # celdas de saldo por batch al recalcular
PAUSA_ENTRE_ESCRITURAS = 1  # segundos entre escrituras consecutivas (cuida la cuota)


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower().strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def _sym(moneda_o_cuenta: str) -> str:
    return "U$S" if "USD" in (moneda_o_cuenta or "") else "$"


def _fecha_ahora() -> str:
    return datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")


def _monto(action: dict, key: str = "monto") -> float:
    """Monto validado (> 0). Red de seguridad por si el LLM devuelve cualquier cosa."""
    m = parse_amount(action.get(key))
    if m is None:
        raise ValueError(
            f"El monto '{action.get(key)}' no me cierra: tiene que ser un número mayor a cero."
        )
    return m


def _cuenta(action: dict, key: str = "cuenta") -> str:
    """Nombre de cuenta normalizado y validado contra las cuentas reales."""
    nombre = nc(action.get(key), action.get("moneda"))
    if nombre not in CUENTAS:
        raise ValueError(
            f"No reconozco la cuenta '{action.get(key)}'. "
            f"Las válidas son: {', '.join(CUENTAS)}."
        )
    return nombre


def _moneda_de(cuenta: str) -> str:
    """La moneda la define la cuenta, no lo que diga el LLM (evita incoherencias)."""
    return "USD" if "USD" in cuenta else "UYU"


def _data_fresca() -> list:
    """Estado fresco de la pestaña Cuentas: evita calcular saldos sobre caché viejo."""
    ctx = get_ctx(force=True)
    if ctx and ctx.get("data"):
        return ctx["data"]
    raise ValueError("No pude leer la planilla en este momento. Probá de nuevo en unos segundos.")


def _alerta_presupuesto(categoria: str) -> str:
    """Advertencia si el gasto del mes en la categoría se acerca o pasa el presupuesto."""
    try:
        presupuestos = get_config().get("presupuestos", {})
        objetivo = next((v for k, v in presupuestos.items() if _plain(k) == _plain(categoria)), None)
        if not objetivo:
            return ""
        ctx = get_ctx()  # fresco: update_global acaba de forzar la lectura
        if not ctx:
            return ""
        now = datetime.now(UYU_TZ)
        gastos = gastos_mes_por_categoria(ctx, now.year, now.month)
        gastado = next((v for k, v in gastos.items() if _plain(k) == _plain(categoria)), 0.0)
        pct = gastado / objetivo * 100
        if pct >= 100:
            return (f"\n\n🚨 Te pasaste del presupuesto de {categoria}: "
                    f"$ {gastado:,.0f} de $ {objetivo:,.0f} ({pct:.0f}%)")
        if pct >= BUDGET_WARN_PCT:
            return (f"\n\n⚠️ Vas por el {pct:.0f}% del presupuesto de {categoria} "
                    f"($ {gastado:,.0f} de $ {objetivo:,.0f})")
        return ""
    except Exception as e:
        logger.warning(f"alerta presupuesto: {e}")
        return ""


def _gasto(action: dict) -> str:
    c = _cuenta(action)
    m = _monto(action)
    mo = _moneda_de(c)
    desc = action.get("descripcion") or "gasto"
    cat = action.get("categoria") or "Otro"
    wc = get_ws("Cuentas")
    s = bal(_data_fresca(), c) - m
    with_retry(wc.append_row, [_fecha_ahora(), desc, cat, c, mo, "", m, round(s, 2)])
    update_global()
    sym = _sym(mo)
    msg = (f"✅ *Gasto registrado*\n📝 {desc}\n💸 {sym} {m:,.2f} | {cat}\n"
           f"🏦 {c}\n💰 Saldo: {sym} {s:,.2f}")
    return msg + _alerta_presupuesto(cat)


def _ingreso(action: dict) -> str:
    c = _cuenta(action)
    m = _monto(action)
    mo = _moneda_de(c)
    desc = action.get("descripcion") or "ingreso"
    cat = action.get("categoria") or "Ingreso"
    wc = get_ws("Cuentas")
    s = bal(_data_fresca(), c) + m
    with_retry(wc.append_row, [_fecha_ahora(), desc, cat, c, mo, m, "", round(s, 2)])
    update_global()
    sym = _sym(mo)
    return (f"✅ *Ingreso registrado*\n📝 {desc}\n💚 {sym} {m:,.2f} | {cat}\n"
            f"🏦 {c}\n💰 Saldo: {sym} {s:,.2f}")


def _transferencia(action: dict) -> str:
    o = _cuenta(action, "cuenta_origen")
    d = _cuenta(action, "cuenta_destino")
    if o == d:
        raise ValueError("La cuenta de origen y la de destino son la misma.")
    if _moneda_de(o) != _moneda_de(d):
        raise ValueError(
            "Son cuentas de distinta moneda; decime cuánto salió y cuánto entró.\n"
            "Ej: _compré 100 dólares a 41 con plata del BBVA_."
        )
    m = _monto(action)
    mo = _moneda_de(o)
    wc = get_ws("Cuentas")
    data = _data_fresca()
    so = bal(data, o) - m
    sd = bal(data, d) + m
    fecha = _fecha_ahora()
    with_retry(wc.append_row, [fecha, f"Transferencia a {d}", "Transferencia", o, mo, "", m, round(so, 2)])
    time.sleep(PAUSA_ENTRE_ESCRITURAS)
    try:
        with_retry(wc.append_row, [fecha, f"Transferencia desde {o}", "Transferencia", d, mo, m, "", round(sd, 2)])
    except Exception as e:
        # falla parcial: quedó la salida sin la entrada — avisar claro, nunca en silencio
        logger.error(f"Transferencia incompleta {o} → {d}: {e}")
        inv_cache()
        raise ValueError(
            f"Quedó registrada la salida de {o} pero FALLÓ la entrada a {d}. "
            f"Mandame «ingreso de {m:g} en {d}» para completarla, o revisá la planilla."
        )
    update_global()
    sym = _sym(mo)
    return (f"✅ *Transferencia*\n📤 {o}: {sym} {so:,.2f}\n"
            f"📥 {d}: {sym} {sd:,.2f}\n💱 {sym} {m:,.2f}")


def _inversion(action: dict) -> str:
    plataforma, activo = resolve_activo(action.get("activo"))
    if not activo:
        raise ValueError(
            f"No reconozco el activo '{action.get('activo')}'. "
            f"Cripto (Binance): BTC, ETH, SOL. Acciones (XTB): SP500, QQQ, Oro, Nvidia."
        )
    m = _monto(action)
    cfg = INVERSIONES[plataforma]
    mo = cfg["moneda"]
    rate = usd_rate()
    fecha = _fecha_ahora()
    sym = "U$S"

    if cfg["descuenta"]:
        # XTB: compra directa con tarjeta → sale de una cuenta USD real
        co = _cuenta(action)
        if _moneda_de(co) != "USD":
            raise ValueError(
                "Las acciones de XTB se compran en dólares; decime desde qué cuenta USD "
                "(BBVA USD o Itaú USD)."
            )
        add_investment(fecha, plataforma, activo, m, mo, co, rate, action.get("descripcion", ""))
        wc = get_ws("Cuentas")
        s = bal(_data_fresca(), co) - m
        with_retry(wc.append_row,
                   [fecha, f"Inversión {activo} (XTB)", "Inversión", co, mo, "", m, round(s, 2)])
        update_global()
        update_inversiones_view()
        total = investment_totals().get(activo, 0.0)
        return (f"✅ *Inversión registrada*\n📈 {activo} · {plataforma}\n"
                f"💵 {sym} {m:,.2f}\n🧮 Total en {activo}: {sym} {total:,.2f}\n"
                f"🏦 Desde {co} · saldo {sym} {s:,.2f}")

    # BINANCE: se compra el USDT por P2P (ese gasto se registra aparte) → no toca cuentas
    co = nc(action.get("cuenta"), "USD") if action.get("cuenta") else ""
    if co not in CUENTAS:
        co = ""
    add_investment(fecha, plataforma, activo, m, mo, co, rate, action.get("descripcion", ""))
    update_inversiones_view()
    total = investment_totals().get(activo, 0.0)
    return (f"✅ *Inversión registrada*\n📈 {activo} · {plataforma}\n"
            f"💵 {sym} {m:,.2f}\n🧮 Total en {activo}: {sym} {total:,.2f}\n"
            f"ℹ️ No toqué tus cuentas (el USDT lo cargás con la compra P2P).")


def _recalc_saldos(wc) -> None:
    """Recalcula la columna SALDO de todas las filas (necesario tras editar o eliminar)."""
    fresh = with_retry(wc.get_all_values)
    saldo_por_cuenta: dict = {}
    updates = []
    for idx in range(FIRST_DATA_ROW - 1, len(fresh)):
        r = fresh[idx]
        if len(r) >= 7 and r[3]:
            cta = r[3]
            saldo_por_cuenta[cta] = saldo_por_cuenta.get(cta, 0.0) + sf(r[5]) - sf(r[6])
            updates.append({"range": f"H{idx + 1}", "values": [[round(saldo_por_cuenta[cta], 2)]]})
    for i in range(0, len(updates), RECALC_BATCH):
        with_retry(wc.batch_update, updates[i:i + RECALC_BATCH])
        if i + RECALC_BATCH < len(updates):
            time.sleep(PAUSA_ENTRE_ESCRITURAS)


def _fila_valida(action: dict, data: list) -> int:
    try:
        fi = int(action.get("fila"))
    except (TypeError, ValueError):
        raise ValueError("No pude identificar a qué movimiento te referís.")
    if not (FIRST_DATA_ROW <= fi <= len(data)):
        raise ValueError(f"La fila {fi} no corresponde a un movimiento válido.")
    return fi


def _eliminar(action: dict) -> str:
    wc = get_ws("Cuentas")
    data = _data_fresca()
    fi = _fila_valida(action, data)
    desc = data[fi - 1][1] if len(data[fi - 1]) > 1 else "movimiento"
    with_retry(wc.delete_rows, fi)
    time.sleep(PAUSA_ENTRE_ESCRITURAS)
    # los saldos corrientes de las filas posteriores quedan desfasados: recalcular
    _recalc_saldos(wc)
    get_ws("Global").batch_clear(["A15:H500"])
    update_global()
    return f"✅ *Eliminado*: {desc}"


def _editar(action: dict) -> str:
    wc = get_ws("Cuentas")
    data = _data_fresca()
    fi = _fila_valida(action, data)
    row = data[fi - 1]
    desc_o = row[1] if len(row) > 1 else "movimiento"
    es_ingreso = bool(row[5]) if len(row) > 5 else False
    upd = []
    if "monto" in action:
        nm = parse_amount(action.get("monto"))
        if nm is None:
            raise ValueError(f"El monto '{action.get('monto')}' no es válido.")
        if es_ingreso:
            upd += [{"range": f"F{fi}", "values": [[nm]]}, {"range": f"G{fi}", "values": [[""]]}]
        else:
            upd += [{"range": f"F{fi}", "values": [[""]]}, {"range": f"G{fi}", "values": [[nm]]}]
    if "descripcion" in action:
        upd.append({"range": f"B{fi}", "values": [[action["descripcion"]]]})
    if "categoria" in action:
        upd.append({"range": f"C{fi}", "values": [[action["categoria"]]]})
    if "cuenta" in action:
        nueva = nc(action["cuenta"], action.get("moneda"))
        if nueva not in CUENTAS:
            raise ValueError(f"No reconozco la cuenta '{action['cuenta']}'.")
        upd.append({"range": f"D{fi}", "values": [[nueva]]})
    if not upd:
        raise ValueError("No me dijiste qué cambiarle al movimiento.")
    with_retry(wc.batch_update, upd)
    time.sleep(PAUSA_ENTRE_ESCRITURAS)
    _recalc_saldos(wc)
    get_ws("Global").batch_clear(["A15:H500"])
    update_global()
    return f"✅ *Editado*: {action.get('descripcion', desc_o)}"


def _actualizar_saldo(action: dict) -> str:
    c = _cuenta(action)
    raw = action.get("saldo")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and float(raw) == 0:
        nv = 0.0  # poner una cuenta en cero es legítimo
    else:
        nv = parse_amount(raw)
        if nv is None:
            raise ValueError("El saldo nuevo tiene que ser un número (puede ser 0).")
    wc = get_ws("Cuentas")
    act = bal(_data_fresca(), c)
    df = nv - act
    mo = _moneda_de(c)
    fecha = _fecha_ahora()
    if df > 0:
        with_retry(wc.append_row, [fecha, "Ajuste de saldo", "Ajuste", c, mo, df, "", nv])
    elif df < 0:
        with_retry(wc.append_row, [fecha, "Ajuste de saldo", "Ajuste", c, mo, "", abs(df), nv])
    update_global()
    return f"✅ *Saldo actualizado*\n🏦 {c}: {_sym(c)} {nv:,.2f}"


def _presupuesto(action: dict) -> str:
    cat = (action.get("categoria") or "").strip()
    if not cat:
        raise ValueError("¿Presupuesto para qué categoría?")
    monto = parse_amount(action.get("monto")) or 0.0
    return set_budget(cat, monto)


def _meta(action: dict) -> str:
    nombre = (action.get("nombre") or "").strip()
    if not nombre:
        raise ValueError("¿Cómo querés que se llame la meta?")
    objetivo = parse_amount(action.get("objetivo")) or 0.0
    moneda = "USD" if "USD" in str(action.get("moneda", "USD")).upper() else "UYU"
    if objetivo <= 0:
        return set_goal(nombre, 0, moneda)
    ctx = get_ctx()
    saldos = ctx.get("saldos", {}) if ctx else {}
    base = sum(v for k, v in saldos.items() if moneda in k)
    fecha_limite = str(action.get("fecha_limite") or "")
    return set_goal(nombre, objetivo, moneda, fecha_limite, base)


def _consulta_saldo(action: dict) -> str:
    ctx = get_ctx()
    if not ctx:
        raise ValueError("No pude leer la planilla. Probá de nuevo en unos segundos.")
    return saldos_query_msg(ctx, action.get("cuenta"))


def _resumen(action: dict) -> str:
    ctx = get_ctx()
    if not ctx:
        raise ValueError("No pude leer la planilla. Probá de nuevo en unos segundos.")
    s = ctx.get("saldos", {})
    rate = ctx.get("rate", 40)
    now = datetime.now(UYU_TZ)
    tu = sum(v for k, v in s.items() if "UYU" in k)
    td = sum(v for k, v in s.items() if "USD" in k)
    lines = ["📊 *RESUMEN GLOBAL*", f"📅 {now.strftime('%d/%m/%Y %H:%M')}", "", "💰 *Saldos:*"]
    for c in CUENTAS:
        sym = "$" if "UYU" in c else "U$S"
        lines.append(f"  • {c}: {sym} {s.get(c, 0):,.2f}")
    lines += ["", "📈 *Totales:*", f"  • UYU: $ {tu:,.2f}", f"  • USD: U$S {td:,.2f}",
              f"  • Todo en UYU: $ {tu + td * rate:,.2f}",
              f"  • Todo en USD: U$S {tu / rate + td:,.2f}" if rate else "  • Todo en USD: U$S 0",
              f"  • Cotización: $ {rate:.2f}", "", "📅 *Este mes:*",
              f"  • Ingresos UYU: $ {ctx.get('iu', 0):,.2f}",
              f"  • Egresos UYU: $ {ctx.get('eu', 0):,.2f}",
              f"  • Balance UYU: $ {ctx.get('iu', 0) - ctx.get('eu', 0):,.2f}",
              f"  • Ingresos USD: U$S {ctx.get('id', 0):,.2f}",
              f"  • Egresos USD: U$S {ctx.get('ed', 0):,.2f}"]
    lines += top_categorias_lines(ctx)
    return "\n".join(lines)


_HANDLERS: Dict[str, Callable[[dict], str]] = {
    "gasto": _gasto,
    "ingreso": _ingreso,
    "transferencia": _transferencia,
    "inversion": _inversion,
    "eliminar": _eliminar,
    "editar": _editar,
    "actualizar_saldo": _actualizar_saldo,
    "presupuesto": _presupuesto,
    "meta": _meta,
    "consulta_saldo": _consulta_saldo,
    "resumen": _resumen,
}


def exe(action: dict) -> str:
    """Ejecuta una acción (del LLM o de un comando) y devuelve el mensaje para el usuario."""
    if not isinstance(action, dict):
        raise ValueError("Acción inválida.")
    handler = _HANDLERS.get(action.get("tipo"))
    if handler is None:
        return "❌ No entendí la operación."
    return handler(action)
