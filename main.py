import logging
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TELEGRAM_TOKEN, UYU_TZ
from kkaynbot.bot.handlers import start, cmd_setup, cmd_resumen, cmd_saldo, cmd_limpiar, handle_msg
from kkaynbot.bot.scheduler import weekly_report, check_balance

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        # Otra instancia corriendo en paralelo durante el deploy — se resuelve solo
        logger.warning("Telegram 409 Conflict: otra instancia activa, ignorando.")
        return
    if isinstance(context.error, NetworkError):
        logger.warning(f"NetworkError transitorio: {context.error}")
        return
    logger.error(f"Error en update {update}: {context.error}", exc_info=context.error)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    sch = AsyncIOScheduler(timezone=UYU_TZ)
    sch.add_job(weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    sch.add_job(check_balance, "cron", hour=8, minute=0, args=[app])
    sch.start()
    logger.info("🤖 KkaynBot v5!")
    # drop_pending_updates descarta mensajes de la instancia anterior al arrancar
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
