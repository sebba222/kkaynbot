from kkaynbot.sheets.client import ss, inv_cache, reset_ws
from kkaynbot.sheets.format import fr, mg, cw, rh
from kkaynbot.sheets.theme import (AZ_OSC, AZ_MED, AZ_CLA, TURQ, GR_OSC, GR_CLA,
                                    BLANCO, T_BLA, T_OSC, MORADO, MOR_MED)

def setup_sheets():
    sp = ss(); existing = [w.title for w in sp.worksheets()]
    try: sp.del_worksheet(sp.worksheet("_t_"))
    except: pass
    temp = sp.add_worksheet("_t_", 1, 1)
    for t in ["Global", "Por Cuenta", "Inversiones", "Cuentas"]:
        if t in existing:
            try: sp.del_worksheet(sp.worksheet(t))
            except: pass

    # ── 1. GLOBAL ──
    wg = sp.add_worksheet("Global", rows=500, cols=10)
    sid = wg._properties['sheetId']
    wg.batch_update([
        {"range": "A1", "values": [["💰  GESTIÓN FINANCIERA — SEBA RODRÍGUEZ"]]},
        {"range": "A2", "values": [["Actualizado:", ""]]},
        {"range": "A3", "values": [["SALDOS TOTALES"]]},
        {"range": "A4", "values": [["Total UYU", "Total USD", "Todo en UYU", "Todo en USD", "Cotización USD/UYU"]]},
        {"range": "A5", "values": [["", "", "", "", ""]]},
        {"range": "A7", "values": [["RESUMEN DEL MES"]]},
        {"range": "A8", "values": [["", "PESOS (UYU)", "", "DÓLARES (USD)", ""]]},
        {"range": "A9", "values": [["Ingresos", "", "", "", ""]]},
        {"range": "A10", "values": [["Egresos", "", "", "", ""]]},
        {"range": "A11", "values": [["Balance", "", "", "", ""]]},
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
    wp = sp.add_worksheet("Por Cuenta", rows=500, cols=17)
    wpc = wp._properties['sheetId']
    wp.update(values=[["📊  MOVIMIENTOS POR CUENTA"]], range_name="A1")
    rqp = [fr(wpc,1,1,1,16,bold=True,bg=AZ_OSC,fg=T_BLA,sz=14,al="CENTER"),
           mg(wpc,1,1,1,16), rh(wpc,1,48),
           cw(wpc,7,14), cw(wpc,14,14)]
    for j, w in enumerate([125, 190, 108, 88, 88, 92]): rqp += [cw(wpc,1+j,w), cw(wpc,8+j,w)]
    rqp += [cw(wpc,15,115), cw(wpc,16,115)]
    sp.batch_update({"requests": rqp})

    # ── 3. INVERSIONES ──
    wi = sp.add_worksheet("Inversiones", rows=500, cols=7)
    wii = wi._properties['sheetId']
    wi.batch_update([
        {"range": "A1", "values": [["📈  REGISTRO DE INVERSIONES"]]},
        {"range": "A3", "values": [["FECHA", "ACTIVO", "MONTO", "MONEDA", "CUENTA ORIGEN", "COTIZACIÓN", "NOTAS"]]},
    ])
    sp.batch_update({"requests": [
        fr(wii,1,1,1,7,bold=True,bg=MORADO,fg=T_BLA,sz=13,al="CENTER"), mg(wii,1,1,1,7), rh(wii,1,45),
        fr(wii,2,1,2,7,bg=BLANCO), rh(wii,2,10),
        fr(wii,3,1,3,7,bold=True,bg=MOR_MED,fg=T_BLA,al="CENTER"), rh(wii,3,26),
        {"updateSheetProperties": {"properties": {"sheetId": wii, "gridProperties": {"frozenRowCount": 3}}, "fields": "gridProperties.frozenRowCount"}},
    ]})

    # ── 4. CUENTAS (storage) ──
    wc = sp.add_worksheet("Cuentas", rows=1000, cols=9)
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

    for h in ["_t_", "Sheet1", "Hoja 1", "Hoja1"]:
        try: sp.del_worksheet(sp.worksheet(h))
        except: pass

    inv_cache()
    reset_ws()
    return "✅ Todo listo. Orden: Global → Por Cuenta → Inversiones → Cuentas\nCargá tu primer movimiento y la pestaña Por Cuenta se construirá automáticamente."
