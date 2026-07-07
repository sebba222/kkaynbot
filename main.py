"""Punto de entrada de KkaynBot: arma la aplicación, el scheduler y el polling.

Corre como servicio systemd en una VM Ubuntu (Oracle Cloud). Sin webhook: usa
long polling, así que no hace falta puerto ni URL pública.
"""
import logging

from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import UYU_TZ, TELEGRAM_TOKEN, validate_config
from kkaynbot.bot.handlers import (cmd_exportar, cmd_limpiar, cmd_mes, cmd_metas,
                                   cmd_presupuesto, cmd_reiniciar, cmd_resumen,
                                   cmd_saldo, cmd_semana, cmd_setup, handle_msg,
                                   on_callback, start)
from kkaynbot.bot.scheduler import check_balance, weekly_report

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    sch = AsyncIOScheduler(timezone=UYU_TZ)
    sch.add_job(weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    sch.add_job(check_balance, "cron", hour=8, minute=0, args=[app])
    sch.start()
    app.bot_data["scheduler"] = sch
    logger.info("🤖 KkaynBot v6 (polling) arriba")


async def post_shutdown(app: Application) -> None:
    sch = app.bot_data.get("scheduler")
    if sch and sch.running:
        sch.shutdown(wait=False)


async def error_handler(update, context) -> None:
    if isinstance(context.error, Conflict):
        # Con polling, un Conflict casi siempre significa que hay OTRA instancia
        # del bot corriendo con el mismo token (ej. quedó un proceso viejo tras
        # un restart del servicio). No es transitorio: conviene que se vea en logs.
        logger.error(
            "Telegram 409 Conflict: otra instancia del bot está haciendo polling "
            "con el mismo token. Revisá 'systemctl status kkaynbot' y procesos huérfanos."
        )
        return
    if isinstance(context.error, NetworkError):
        logger.warning(f"NetworkError transitorio: {context.error}")
        return
    logger.error(f"Error en update {update}: {context.error}", exc_info=context.error)


def main() -> None:
    validate_config()  # fallar rápido si falta alguna variable de entorno o el archivo de credenciales

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
    app.add_handler(CommandHandler("mes", cmd_mes))
    app.add_handler(CommandHandler("semana", cmd_semana))
    app.add_handler(CommandHandler("metas", cmd_metas))
    app.add_handler(CommandHandler("presupuesto", cmd_presupuesto))
    app.add_handler(CommandHandler("exportar", cmd_exportar))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(CommandHandler("reiniciar", cmd_reiniciar))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    logger.info("Modo polling")
    # run_polling ya maneja SIGTERM/SIGINT con graceful shutdown (systemd manda
    # SIGTERM en `systemctl stop/restart`, así que esto alcanza sin nada extra).
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
