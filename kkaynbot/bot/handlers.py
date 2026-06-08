import logging

from config import AUTHORIZED_USER_ID, CUENTAS, conversation_history
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.actions import exe
from kkaynbot.sheets.setup import setup_sheets, reiniciar_sheets
from kkaynbot.ai.groq import process_msg

logger = logging.getLogger(__name__)

async def start(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: await u.message.reply_text("⛔"); return
    await u.message.reply_text("👋 *KkaynBot* listo\\.\n\nEjemplos:\n• _cobré sueldo 50k en BBVA_\n• _gasté 300 en farmacia con Itaú_\n• _pasé 10k de BBVA a Itaú_\n• _el sueldo fueron 48k no 50k_\n• _¿cuánto tengo en BBVA?_\n\nComandos: /resumen /saldo /setup /limpiar", parse_mode="MarkdownV2")

async def cmd_setup(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    await u.message.reply_text("⚙️ Aplicando diseño...")
    try: await u.message.reply_text(setup_sheets())
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_resumen(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    await u.message.reply_text("🔄 Calculando...")
    try: await u.message.reply_text(exe({"tipo": "resumen"}), parse_mode="Markdown")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_saldo(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    try:
        ctx = get_ctx(force=True)
        if not ctx:
            await u.message.reply_text("⏳ Sheets no disponible, intentá en unos segundos.")
            return
        s = ctx.get("saldos", {}); rate = ctx.get("rate", 40)
        lines = ["💳 *SALDOS ACTUALES*\n"]
        for c_ in CUENTAS:
            sym = "$" if "UYU" in c_ else "U$S"; lines.append(f"• {c_}: {sym} {s.get(c_, 0):,.2f}")
        lines.append(f"\n💱 1 USD = $ {rate:.2f}")
        await u.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_reiniciar(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    await u.message.reply_text("🗑️ Borrando todos los registros...")
    try: await u.message.reply_text(reiniciar_sheets())
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_limpiar(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    conversation_history[u.effective_user.id] = []
    await u.message.reply_text("🧹 Historial limpiado.")

async def handle_msg(u, c):
    if u.effective_user.id != AUTHORIZED_USER_ID: return
    await u.message.reply_text("🤔 Procesando...")
    try:
        r = await process_msg(u, u.message.text.strip())
        await u.message.reply_text(r, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error: {e}"); await u.message.reply_text(f"❌ {e}")
