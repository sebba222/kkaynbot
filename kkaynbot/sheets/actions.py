import time
import logging
from datetime import datetime

from config import UYU_TZ, CUENTAS
from kkaynbot.sheets.client import get_ctx, ss
from kkaynbot.sheets.global_view import update_global
from kkaynbot.utils.helpers import sf, bal, usd_rate, with_retry
from kkaynbot.utils.normalize import nc

logger = logging.getLogger(__name__)

def exe(action):
    t = action.get("tipo"); sp = ss(); wc = sp.worksheet("Cuentas")
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    ctx_pre = get_ctx(); data = ctx_pre.get("data", []) if ctx_pre else with_retry(wc.get_all_values)

    if t == "gasto":
        c = nc(action["cuenta"]); m = float(action["monto"]); mo = action.get("moneda", "UYU")
        s = bal(data, c) - m; wc.append_row([fecha, action["descripcion"], action.get("categoria", "Otro"), c, mo, "", m, round(s, 2)])
        update_global(); sym = "$" if "UYU" in mo else "U$S"
        return f"✅ *Gasto registrado*\n📝 {action['descripcion']}\n💸 {sym} {m:,.2f} | {action.get('categoria','Otro')}\n🏦 {c}\n💰 Saldo: {sym} {s:,.2f}"

    elif t == "ingreso":
        c = nc(action["cuenta"]); m = float(action["monto"]); mo = action.get("moneda", "UYU")
        s = bal(data, c) + m; wc.append_row([fecha, action["descripcion"], action.get("categoria", "Sueldo"), c, mo, m, "", round(s, 2)])
        update_global(); sym = "$" if "UYU" in mo else "U$S"
        return f"✅ *Ingreso registrado*\n📝 {action['descripcion']}\n💚 {sym} {m:,.2f} | {action.get('categoria','Ingreso')}\n🏦 {c}\n💰 Saldo: {sym} {s:,.2f}"

    elif t == "transferencia":
        o = nc(action["cuenta_origen"]); d = nc(action["cuenta_destino"]); m = float(action["monto"]); mo = action.get("moneda", "UYU")
        so = bal(data, o) - m; sd = bal(data, d) + m
        with_retry(wc.append_row, [fecha, f"Transferencia a {d}", "Transferencia", o, mo, "", m, round(so, 2)])
        time.sleep(1)
        with_retry(wc.append_row, [fecha, f"Transferencia desde {o}", "Transferencia", d, mo, m, "", round(sd, 2)])
        update_global(); sym = "$" if "UYU" in mo else "U$S"
        return f"✅ *Transferencia*\n📤 {o}: {sym} {so:,.2f}\n📥 {d}: {sym} {sd:,.2f}\n💱 {sym} {m:,.2f}"

    elif t == "inversion":
        a = action["activo"]; m = float(action["monto"]); mo = action.get("moneda", "USD"); co = nc(action["cuenta"])
        rate = usd_rate(); wi = sp.worksheet("Inversiones")
        wi.append_row([fecha, a, m, mo, co, rate, action.get("descripcion", "")])
        s = bal(data, co) - m; wc.append_row([fecha, f"Inversión en {a}", "Inversión", co, mo, "", m, round(s, 2)])
        update_global(); sym = "$" if "UYU" in mo else "U$S"
        return f"✅ *Inversión*\n📈 {a}\n💸 {sym} {m:,.2f}\n🏦 {co}\n💰 Saldo: {sym} {s:,.2f}"

    elif t == "eliminar":
        f = action.get("fila")
        if f:
            fi = int(f)
            if fi <= len(data):
                desc = data[fi-1][1] if len(data[fi-1]) > 1 else "movimiento"
                wc.delete_rows(fi)
                sp.worksheet("Global").batch_clear(["A15:H500"])
                update_global()
                return f"✅ *Eliminado*: {desc}"
        return "❌ No pude identificar qué eliminar."

    elif t == "editar":
        f = action.get("fila")
        if f:
            fi = int(f)
            if fi <= len(data):
                row = data[fi-1]; desc_o = row[1] if len(row) > 1 else "movimiento"
                ei = bool(row[5]) if len(row) > 5 else False
                upd = []
                if "monto" in action:
                    nm = float(action["monto"])
                    if ei: upd += [{"range": f"F{fi}", "values": [[nm]]}, {"range": f"G{fi}", "values": [[""]]}]
                    else:  upd += [{"range": f"F{fi}", "values": [[""]]}, {"range": f"G{fi}", "values": [[nm]]}]
                if "descripcion" in action: upd.append({"range": f"B{fi}", "values": [[action["descripcion"]]]})
                if "categoria"   in action: upd.append({"range": f"C{fi}", "values": [[action["categoria"]]]})
                if "cuenta"      in action: upd.append({"range": f"D{fi}", "values": [[nc(action["cuenta"])]]})
                if upd: wc.batch_update(upd)
                time.sleep(1); fresh = with_retry(wc.get_all_values); spc = {}; cu = []
                for idx in range(3, len(fresh)):
                    r = fresh[idx]
                    if len(r) >= 7 and r[3]:
                        c = r[3]
                        if c not in spc: spc[c] = 0.0
                        spc[c] += sf(r[5]) - sf(r[6])
                        cu.append({"range": f"H{idx+1}", "values": [[round(spc[c], 2)]]})
                for i in range(0, len(cu), 50):
                    wc.batch_update(cu[i:i+50])
                    if i+50 < len(cu): time.sleep(1)
                sp.worksheet("Global").batch_clear(["A15:H500"])
                update_global()
                return f"✅ *Editado*: {action.get('descripcion', desc_o)}"
        return "❌ No pude identificar qué editar."

    elif t == "actualizar_saldo":
        c = nc(action["cuenta"]); nv = float(action["saldo"])
        act = bal(data, c); df = nv - act; mo = "USD" if "USD" in c else "UYU"
        if df > 0:   wc.append_row([fecha, "Ajuste de saldo", "Ajuste", c, mo, df, "", nv])
        elif df < 0: wc.append_row([fecha, "Ajuste de saldo", "Ajuste", c, mo, "", abs(df), nv])
        update_global(); sym = "$" if "UYU" in c else "U$S"
        return f"✅ *Saldo actualizado*\n🏦 {c}: {sym} {nv:,.2f}"

    elif t == "resumen":
        ctx = get_ctx(); s = ctx.get("saldos", {}); rate = ctx.get("rate", 40)
        now = datetime.now(UYU_TZ)
        tu = sum(v for k, v in s.items() if "UYU" in k); td = sum(v for k, v in s.items() if "USD" in k)
        lines = ["📊 *RESUMEN GLOBAL*", f"📅 {now.strftime('%d/%m/%Y %H:%M')}", "", "💰 *Saldos:*"]
        for c in CUENTAS:
            sym = "$" if "UYU" in c else "U$S"; lines.append(f"  • {c}: {sym} {s.get(c, 0):,.2f}")
        lines += ["", "📈 *Totales:*", f"  • UYU: $ {tu:,.2f}", f"  • USD: U$S {td:,.2f}",
            f"  • Todo en UYU: $ {tu+td*rate:,.2f}",
            f"  • Todo en USD: U$S {tu/rate+td:,.2f}" if rate else "  • Todo en USD: U$S 0",
            f"  • Cotización: $ {rate:.2f}", "", "📅 *Este mes:*",
            f"  • Ingresos UYU: $ {ctx.get('iu', 0):,.2f}", f"  • Egresos UYU: $ {ctx.get('eu', 0):,.2f}",
            f"  • Balance UYU: $ {ctx.get('iu', 0)-ctx.get('eu', 0):,.2f}",
            f"  • Ingresos USD: U$S {ctx.get('id', 0):,.2f}", f"  • Egresos USD: U$S {ctx.get('ed', 0):,.2f}"]
        return "\n".join(lines)

    return "❌ No entendí la operación."
