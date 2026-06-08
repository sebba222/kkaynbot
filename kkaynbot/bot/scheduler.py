import logging

from config import AUTHORIZED_USER_ID, MIN_BALANCE_UYU, MIN_BALANCE_USD
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.actions import exe

logger = logging.getLogger(__name__)

async def weekly_report(app):
    try:
        await app.bot.send_message(
            chat_id=AUTHORIZED_USER_ID,
            text="📅 *REPORTE SEMANAL*\n\n" + exe({"tipo": "resumen"}),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"report: {e}")

async def check_balance(app):
    try:
        ctx = get_ctx(force=True)
        al = []
        for c, v in ctx.get("saldos", {}).items():
            if "UYU" in c and 0 < v < MIN_BALANCE_UYU: al.append(f"⚠️ {c}: $ {v:,.2f}")
            elif "USD" in c and 0 < v < MIN_BALANCE_USD: al.append(f"⚠️ {c}: U$S {v:,.2f}")
        if al:
            await app.bot.send_message(
                chat_id=AUTHORIZED_USER_ID,
                text="🚨 *SALDO BAJO*\n\n" + "\n".join(al),
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error(f"balance: {e}")
