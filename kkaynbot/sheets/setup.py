from config import INV_DISPLAY_TAB, INV_STORAGE_TAB
from kkaynbot.sheets.client import ss, inv_cache, reset_ws, get_ws
from kkaynbot.sheets.config_tab import ensure_extra_tabs
from kkaynbot.sheets.format import fr, mg, cw, rh
from kkaynbot.sheets.inversiones import (ensure_inv_tabs, setup_display_header,
                                         update_inversiones_view)
from kkaynbot.sheets.theme import (AZ_OSC, AZ_MED, AZ_CLA, TURQ, GR_OSC, GR_CLA,
                                    BLANCO, T_BLA, T_OSC, MORADO, MOR_MED)
from kkaynbot.utils.helpers import sf
from kkaynbot.utils.normalize import resolve_activo


def setup_sheets():
    """Actualiza la estructura y el diseño. Preserva los datos existentes."""
    sp = ss()
    existing = {w.title: w for w in sp.worksheets()}

    # ── 1. GLOBAL ──
    if "Global" not in existing:
        wg = sp.add_worksheet("Global", rows=500, cols=10)
    else:
        wg = existing["Global"]
    sid = wg._properties['sheetId']
    wg.batch_update([
        {"range": "A1", "values": [["💰  GESTIÓN FINANCIERA — SEBA RODRÍGUEZ"]]},
        {"range": "A2", "values": [["Actualizado:", ""]]},
        {"range": "A3", "values": [["SALDOS TOTALES"]]},
        {"range": "A4", "values": [["Total UYU", "Total USD", "Todo en UYU", "Todo en USD", "Cotización USD/UYU"]]},
        {"range": "A7", "values": [["RESUMEN DEL MES"]]},
        {"range": "A8", "values": [["", "PESOS (UYU)", "", "DÓLARES (USD)", ""]]},
        {"range": "A9", "values": [["Ingresos"]]},
        {"range": "A10", "values": [["Egresos"]]},
        {"range": "A11", "values": [["Balance"]]},
        {"range": "A13", "values": [["TODOS LOS MOVIMIENTOS"]]},
        {"range": "A14", "values": [["FECHA", "DESCRIPCIÓN", "CATEGORÍA", "CUENTA", "MONEDA", "INGRESO", "EGRESO", "SALDO"]]},
    ])
    rqs = [fr(sid,1,1,1,8,bold=True,bg=AZ_OSC,fg=T_BLA,sz=14,al="CENTER"), mg(sid,1,1,1,8), rh(sid,1,48),
           fr(sid,2,1,2,8,bold=True,bg=AZ_MED,fg=T_BLA,sz=10,al="LEFT"), rh(sid,2,22),
           fr(sid,3,1,3,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"), mg(sid,3,1,3,8), rh(sid,3,32),
           fr(sid,4,1,4,5,bold=True,bg=AZ_MED,fg=T_BLA,al="CENTER"), rh(sid,4,26),
           fr(sid,5,1,5,5,bold=True,bg=AZ_CLA,fg=AZ_OSC,sz=11,al="CENTER"), rh(sid,5,30),
           fr(sid,6,1,6,8,bg=BLANCO), rh(sid,6,10),
           fr(sid,7,1,7,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"), mg(sid,7,1,7,8), rh(sid,7,32),
           fr(sid,8,1,8,5,bold=True,bg=AZ_MED,fg=T_BLA,al="CENTER"),
           mg(sid,8,2,8,3), mg(sid,8,4,8,5), rh(sid,8,26)]
    for r in [9, 10, 11]:
        rqs += [fr(sid,r,1,r,1,bold=True,bg=AZ_CLA,fg=AZ_OSC,al="LEFT"),
                fr(sid,r,2,r,3,bg=GR_CLA,fg=T_OSC,al="CENTER"), mg(sid,r,2,r,3),
                fr(sid,r,4,r,5,bg=GR_CLA,fg=T_OSC,al="CENTER"), mg(sid,r,4,r,5), rh(sid,r,26)]
    rqs += [fr(sid,12,1,12,8,bg=BLANCO), rh(sid,12,10),
            fr(sid,13,1,13,8,bold=True,bg=TURQ,fg=T_BLA,sz=11,al="CENTER"), mg(sid,13,1,13,8), rh(sid,13,32),
            fr(sid,14,1,14,8,bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"), rh(sid,14,26)]
    for i, w in enumerate([135, 220, 120, 120, 75, 105, 105, 110]): rqs.append(cw(sid, i+1, w))
    rqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 14}}, "fields": "gridProperties.frozenRowCount"}})
    sp.batch_update({"requests": rqs})

    # ── 2. POR CUENTA ──
    if "Por Cuenta" not in existing:
        wp = sp.add_worksheet("Por Cuenta", rows=500, cols=17)
    else:
        wp = existing["Por Cuenta"]
    wpc = wp._properties['sheetId']
    wp.update(values=[["📊  MOVIMIENTOS POR CUENTA"]], range_name="A1")
    rqp = [fr(wpc,1,1,1,16,bold=True,bg=AZ_OSC,fg=T_BLA,sz=14,al="CENTER"),
           mg(wpc,1,1,1,16), rh(wpc,1,48),
           cw(wpc,7,14), cw(wpc,14,14)]
    for j, w in enumerate([125, 190, 108, 88, 88, 92]): rqp += [cw(wpc,1+j,w), cw(wpc,8+j,w)]
    rqp += [cw(wpc,15,115), cw(wpc,16,115)]
    sp.batch_update({"requests": rqp})

    # ── 3. INVERSIONES (vista por plataforma) + INV DATA (storage) ──
    # Si venía del formato plano viejo, migramos los activos reconocidos al storage nuevo.
    pendientes = []
    if "Inversiones" in existing and INV_STORAGE_TAB not in existing:
        try:
            for r in existing["Inversiones"].get_all_values()[3:]:
                if len(r) >= 3 and r[1].strip():
                    plat, act = resolve_activo(r[1])
                    if act:
                        pendientes.append([
                            r[0], plat, act, sf(r[2]),
                            r[3] if len(r) > 3 else "USD", r[4] if len(r) > 4 else "",
                            r[5] if len(r) > 5 else "", r[6] if len(r) > 6 else "",
                        ])
        except Exception:
            pendientes = []

    inv_creadas = ensure_inv_tabs()   # crea Inv Data (y la vista, si no existía)

    if pendientes:
        get_ws(INV_STORAGE_TAB).append_rows(pendientes)

    # Convertir/estilar la pestaña Inversiones como vista y rearmarla desde el storage
    wview = get_ws(INV_DISPLAY_TAB)
    wview.batch_clear(["A2:P500"])
    setup_display_header(wview)
    update_inversiones_view()

    # ── 4. CUENTAS (storage) ──
    if "Cuentas" not in existing:
        wc = sp.add_worksheet("Cuentas", rows=1000, cols=9)
    else:
        wc = existing["Cuentas"]
    wci = wc._properties['sheetId']
    wc.batch_update([
        {"range": "A1", "values": [["📋  REGISTRO DE MOVIMIENTOS — STORAGE"]]},
        {"range": "A3", "values": [["FECHA", "DESCRIPCIÓN", "CATEGORÍA", "CUENTA", "MONEDA", "INGRESO", "EGRESO", "SALDO"]]},
    ])
    rqc = [fr(wci,1,1,1,8,bold=True,bg=AZ_OSC,fg=T_BLA,sz=12,al="CENTER"), mg(wci,1,1,1,8), rh(wci,1,40),
           fr(wci,2,1,2,8,bg=BLANCO), rh(wci,2,8),
           fr(wci,3,1,3,8,bold=True,bg=GR_OSC,fg=T_BLA,al="CENTER"), rh(wci,3,26),
           {"updateSheetProperties": {"properties": {"sheetId": wci, "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}}]
    for i, w in enumerate([135, 220, 120, 120, 75, 105, 105, 110]): rqc.append(cw(wci, i+1, w))
    sp.batch_update({"requests": rqc})

    # Limpiar hojas temporales o basura
    for h in ["_t_", "Sheet1", "Hoja 1", "Hoja1"]:
        try: sp.del_worksheet(sp.worksheet(h))
        except: pass

    # Pestañas nuevas (presupuestos/metas y cotización) — migración automática
    extra = ensure_extra_tabs()

    inv_cache()
    reset_ws()
    nuevas = ([t for t in ["Global", "Por Cuenta", "Inversiones", "Cuentas"] if t not in existing]
              + list(inv_creadas) + extra)
    msg = "✅ Estructura y diseño actualizados. Datos preservados."
    if nuevas:
        msg = f"✅ Estructura lista. Pestañas creadas: {', '.join(nuevas)}\nTus datos existentes fueron preservados."
    if pendientes:
        msg += f"\n📈 Migré {len(pendientes)} inversión(es) a la nueva vista por plataforma."
    return msg


def reiniciar_sheets():
    """Borra todos los registros y pone los saldos en cero. La estructura queda intacta."""
    wc = get_ws("Cuentas")
    wg = get_ws("Global")
    wp = get_ws("Por Cuenta")

    wc.batch_clear(["A4:H1000"])
    wg.batch_clear(["A15:H500"])
    wg.batch_update([
        {"range": "A5",  "values": [["", "", "", "", ""]]},
        {"range": "B9",  "values": [["", "", "", ""]]},
        {"range": "B10", "values": [["", "", "", ""]]},
        {"range": "B11", "values": [["", "", "", ""]]},
        {"range": "B2",  "values": [["—"]]},
    ])
    wp.batch_clear(["A2:P500"])

    # Inversiones: vaciar el storage y rearmar la vista (queda con totales en cero)
    try:
        get_ws(INV_STORAGE_TAB).batch_clear(["A4:H1000"])
        update_inversiones_view()
    except Exception:
        pass

    inv_cache()
    return "✅ Todos los registros eliminados. Saldos en cero.\nLa estructura de las pestañas quedó intacta."
