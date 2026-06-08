import logging

from config import BANCOS
from kkaynbot.sheets.client import get_ctx, ss
from kkaynbot.sheets.format import fr, mg, cw, rh
from kkaynbot.sheets.theme import (AZ_OSC, AZ_MED, AZ_CLA, TURQ, VD_OSC, VD_CLA,
                                    RJ_OSC, RJ_CLA, GR_OSC, GR_CLA, BLANCO, T_BLA, T_OSC)

logger = logging.getLogger(__name__)

def update_por_cuenta():
    """Reconstruye toda la pestaña Por Cuenta desde cero.
    3 bloques dinámicos (BBVA, ITAÚ, EFECTIVO), separados por 3 filas vacías.
    Cada bloque:
      Fila A: nombre banco (merge 1-16, azul oscuro)
      Fila B: sub-headers PESOS(UYU) [1-6] | sep[7] | DÓLARES(USD) [8-13] | sep[14] | TOTALES[15-16]
      Fila C: col-headers
      Filas D+: datos en paralelo (UYU izq, USD der)
    Cols: 1-6=UYU, 7=sep(14px), 8-13=USD, 14=sep(14px), 15=SAL UYU, 16=SAL USD
    """
    ctx = get_ctx()
    if not ctx: return
    sp = ss()
    try: wp = sp.worksheet("Por Cuenta")
    except: return
    wpc = wp._properties['sheetId']
    s = ctx["saldos"]; rate = ctx["rate"]; movs = ctx["movs"]
    H = ['FECHA', 'DESCRIPCIÓN', 'CATEGORÍA', 'INGRESO', 'EGRESO', 'SALDO']
    HT = ['SAL UYU', 'SAL USD']
    NCOLS = 16

    wp.batch_clear(["A2:P500"])
    rqs = []; bv = []; cur = 2

    for bname, cuyu, cusd in BANCOS:
        muyu = [r for r in movs if r[3] == cuyu]
        musd = [r for r in movs if r[3] == cusd]
        n = max(len(muyu), len(musd), 0)
        suyu = s.get(cuyu, 0); susd = s.get(cusd, 0)

        # ── Fila A: nombre banco ──
        rqs += [fr(wpc, cur, 1, cur, NCOLS, bold=True, bg=AZ_OSC, fg=T_BLA, sz=12, al="CENTER"),
                mg(wpc, cur, 1, cur, NCOLS), rh(wpc, cur, 34)]
        bv.append({"range": f"A{cur}", "values": [[f"🏦  {bname}"]]})
        cur += 1

        # ── Fila B: sub-headers ──
        rqs += [
            fr(wpc, cur, 1, cur, 6,   bold=True, bg=TURQ, fg=T_BLA, sz=10, al="CENTER"), mg(wpc, cur, 1, cur, 6),
            fr(wpc, cur, 7, cur, 7,   bg=BLANCO),
            fr(wpc, cur, 8, cur, 13,  bold=True, bg=TURQ, fg=T_BLA, sz=10, al="CENTER"), mg(wpc, cur, 8, cur, 13),
            fr(wpc, cur, 14, cur, 14, bg=BLANCO),
            fr(wpc, cur, 15, cur, 16, bold=True, bg=AZ_MED, fg=T_BLA, sz=10, al="CENTER"), mg(wpc, cur, 15, cur, 16),
            rh(wpc, cur, 26),
        ]
        bv += [{"range": f"A{cur}", "values": [["PESOS (UYU)"]]},
               {"range": f"H{cur}", "values": [["DÓLARES (USD)"]]},
               {"range": f"O{cur}", "values": [["TOTALES"]]}]
        cur += 1

        # ── Fila C: col-headers ──
        rqs += [
            fr(wpc, cur, 1, cur, 6,   bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
            fr(wpc, cur, 7, cur, 7,   bg=BLANCO),
            fr(wpc, cur, 8, cur, 13,  bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
            fr(wpc, cur, 14, cur, 14, bg=BLANCO),
            fr(wpc, cur, 15, cur, 16, bold=True, bg=GR_OSC, fg=T_BLA, al="CENTER"),
            rh(wpc, cur, 24),
        ]
        bv += [{"range": f"A{cur}", "values": [H]},
               {"range": f"H{cur}", "values": [H]},
               {"range": f"O{cur}", "values": [HT]}]
        cur += 1

        # ── Totales (primera fila de datos) ──
        sym_u = "$"; sym_d = "U$S"
        bv.append({"range": f"O{cur}", "values": [[f"{sym_u} {suyu:,.0f}", f"{sym_d} {susd:,.2f}"]]})
        rqs.append(fr(wpc, cur, 15, cur, 16, bold=True, bg=AZ_CLA, fg=AZ_OSC, al="CENTER"))

        # ── Filas de datos ──
        if n == 0:
            rqs += [fr(wpc, cur, 1, cur, 6, bg=GR_CLA), fr(wpc, cur, 8, cur, 13, bg=GR_CLA), rh(wpc, cur, 22)]
            cur += 1
        else:
            for i in range(n):
                fi = cur + i
                if i < len(muyu):
                    r = muyu[i]
                    bv.append({"range": f"A{fi}", "values": [[r[0], r[1], r[2], r[5], r[6], r[7]]]})
                    ei = bool(r[5]); ee = bool(r[6])
                    if ei and not ee:   bg, fg = VD_CLA, VD_OSC
                    elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
                    else:               bg, fg = GR_CLA, T_OSC
                    rqs.append(fr(wpc, fi, 1, fi, 6, bg=bg, fg=fg, al="CENTER"))
                else:
                    rqs.append(fr(wpc, fi, 1, fi, 6, bg=GR_CLA))
                if i < len(musd):
                    r = musd[i]
                    bv.append({"range": f"H{fi}", "values": [[r[0], r[1], r[2], r[5], r[6], r[7]]]})
                    ei = bool(r[5]); ee = bool(r[6])
                    if ei and not ee:   bg, fg = VD_CLA, VD_OSC
                    elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
                    else:               bg, fg = GR_CLA, T_OSC
                    rqs.append(fr(wpc, fi, 8, fi, 13, bg=bg, fg=fg, al="CENTER"))
                else:
                    rqs.append(fr(wpc, fi, 8, fi, 13, bg=GR_CLA))
                rqs.append(fr(wpc, fi, 7, fi, 7,   bg=BLANCO))
                rqs.append(fr(wpc, fi, 14, fi, 14, bg=BLANCO))
                rqs.append(rh(wpc, fi, 22))
            cur += n

        # ── 3 filas separadoras ──
        for sep in range(3):
            rqs += [fr(wpc, cur, 1, cur, NCOLS, bg=BLANCO), rh(wpc, cur, 10)]
            cur += 1

    if bv: wp.batch_update(bv)
    if rqs: sp.batch_update({"requests": rqs})
