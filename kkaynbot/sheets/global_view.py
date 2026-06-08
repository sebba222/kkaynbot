import logging
from datetime import datetime

from config import UYU_TZ
from kkaynbot.sheets.client import get_ctx, ss, inv_cache
from kkaynbot.sheets.format import fr
from kkaynbot.sheets.theme import VD_CLA, VD_OSC, RJ_CLA, RJ_OSC, GR_CLA, T_OSC
from kkaynbot.utils.helpers import with_retry
from kkaynbot.sheets.por_cuenta import update_por_cuenta

logger = logging.getLogger(__name__)

def update_global():
    try:
        inv_cache(); ctx = get_ctx(force=True)
        if not ctx: return
        sp = ss(); wg = sp.worksheet("Global"); wc = sp.worksheet("Cuentas")
        sid = wg._properties['sheetId']; cid = wc._properties['sheetId']
        s = ctx["saldos"]; rate = ctx["rate"]
        tu = sum(v for k, v in s.items() if "UYU" in k)
        td = sum(v for k, v in s.items() if "USD" in k)
        now = datetime.now(UYU_TZ)
        with_retry(wg.batch_update, [
            {"range": "B2", "values": [[now.strftime("%d/%m/%Y %H:%M")]]},
            {"range": "A5", "values": [[f"$ {tu:,.0f}", f"U$S {td:,.2f}", f"$ {tu+td*rate:,.0f}",
                                         f"U$S {tu/rate+td:,.2f}" if rate else "U$S 0", f"$ {rate:.2f}"]]},
            {"range": "A9",  "values": [["Ingresos", f"$ {ctx['iu']:,.0f}", "", f"U$S {ctx['id']:,.2f}", ""]]},
            {"range": "A10", "values": [["Egresos",  f"$ {ctx['eu']:,.0f}", "", f"U$S {ctx['ed']:,.2f}", ""]]},
            {"range": "A11", "values": [["Balance",  f"$ {ctx['iu']-ctx['eu']:,.0f}", "", f"U$S {ctx['id']-ctx['ed']:,.2f}", ""]]},
        ])
        movs = ctx["movs"]; inv = list(reversed(movs))
        rqs = []
        # Balance color
        for (r1, c1, r2, c2), v in [((11, 2, 11, 3), ctx['iu']-ctx['eu']), ((11, 4, 11, 5), ctx['id']-ctx['ed'])]:
            bg = VD_CLA if v >= 0 else RJ_CLA; fg = VD_OSC if v >= 0 else RJ_OSC
            rqs.append(fr(sid, r1, c1, r2, c2, bold=True, bg=bg, fg=fg, al="CENTER"))
        if inv:
            wg.batch_clear([f"A15:H{14+len(inv)+5}"])
            wg.update(values=inv, range_name="A15")
            for i, r in enumerate(inv):
                ei = bool(r[5]) if len(r) > 5 else False; ee = bool(r[6]) if len(r) > 6 else False
                fi = 15 + i
                if ei and not ee: bg, fg = VD_CLA, VD_OSC
                elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
                else: bg, fg = GR_CLA, T_OSC
                rqs.append(fr(sid, fi, 1, fi, 8, bg=bg, fg=fg, al="CENTER"))
        # Colorear Cuentas
        for i, r in enumerate(movs):
            ei = bool(r[5]) if len(r) > 5 else False; ee = bool(r[6]) if len(r) > 6 else False
            fi = 4 + i
            if ei and not ee: bg, fg = VD_CLA, VD_OSC
            elif ee and not ei: bg, fg = RJ_CLA, RJ_OSC
            else: bg, fg = GR_CLA, T_OSC
            rqs.append(fr(cid, fi, 1, fi, 8, bg=bg, fg=fg, al="CENTER"))
        if rqs: sp.batch_update({"requests": rqs})
        inv_cache()
        try: update_por_cuenta()
        except Exception as e: logger.warning(f"por_cuenta: {e}")
    except Exception as e:
        logger.error(f"update_global: {e}")
