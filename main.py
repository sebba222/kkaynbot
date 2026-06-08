import logging
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TELEGRAM_TOKEN, UYU_TZ, WEBHOOK_URL, PORT
from kkaynbot.bot.handlers import start, cmd_setup, cmd_resumen, cmd_saldo, cmd_limpiar, handle_msg
from kkaynbot.bot.scheduler import weekly_report, check_balance

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Arranca el scheduler DENTRO del event loop de run_polling/run_webhook."""
    sch = AsyncIOScheduler(timezone=UYU_TZ)
    sch.add_job(weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    sch.add_job(check_balance, "cron", hour=8, minute=0, args=[app])
    sch.start()
    app.bot_data["scheduler"] = sch
    logger.info("🤖 KkaynBot v5!")


async def post_shutdown(app: Application) -> None:
    sch = app.bot_data.get("scheduler")
    if sch and sch.running:
        sch.shutdown(wait=False)


async def error_handler(update, context) -> None:
    if isinstance(context.error, Conflict):
        # Ocurre durante rolling deploys — se resuelve solo cuando la instancia vieja para
        logger.warning("Telegram 409 Conflict: otra instancia activa, ignorando.")
        return
    if isinstance(context.error, NetworkError):
        logger.warning(f"NetworkError transitorio: {context.error}")
        return
    logger.error(f"Error en update {update}: {context.error}", exc_info=context.error)


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    if WEBHOOK_URL:
        # Modo webhook: Railway rutea cada update a UNA sola instancia → sin duplicados
        logger.info(f"Iniciando webhook en puerto {PORT} → {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        # Modo polling: solo para desarrollo local (una sola instancia)
        logger.info("Iniciando polling (modo local)")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
