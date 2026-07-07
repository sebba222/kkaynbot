"""Punto de entrada de KkaynBot: arma la aplicación, el scheduler y el webhook."""
import asyncio
import hashlib
import logging

from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          MessageHandler, filters)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import PORT, TELEGRAM_TOKEN, UYU_TZ, WEBHOOK_URL, validate_config
from kkaynbot.bot.handlers import (cmd_exportar, cmd_limpiar, cmd_mes, cmd_metas,
                                   cmd_presupuesto, cmd_reiniciar, cmd_resumen,
                                   cmd_saldo, cmd_semana, cmd_setup, handle_msg,
                                   on_callback, start)
from kkaynbot.bot.scheduler import check_balance, weekly_report

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Token secreto del webhook: Telegram lo manda en cada POST y PTB rechaza
# cualquier request que no lo traiga (verifica que venga de Telegram de verdad).
WEBHOOK_SECRET = hashlib.sha256(f"kkayn:{TELEGRAM_TOKEN}".encode()).hexdigest()


async def post_init(app: Application) -> None:
    sch = AsyncIOScheduler(timezone=UYU_TZ)
    sch.add_job(weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    sch.add_job(check_balance, "cron", hour=8, minute=0, args=[app])
    sch.start()
    app.bot_data["scheduler"] = sch
    logger.info("🤖 KkaynBot v6!")

    if WEBHOOK_URL:
        full_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        logger.info("Webhook URL configurada (token omitido del log)")

        async def _set_webhook() -> None:
            # Esperar a que el servidor HTTP esté corriendo Y Railway esté enrutando tráfico.
            # run_webhook arranca el servidor DESPUÉS de post_init, así que el primer sleep
            # garantiza que aiohttp ya esté escuchando antes de llamar a Telegram.
            for attempt in range(3):
                await asyncio.sleep(15)
                try:
                    ok = await app.bot.set_webhook(
                        url=full_url,
                        allowed_updates=list(Update.ALL_TYPES),
                        drop_pending_updates=(attempt == 0),
                        secret_token=WEBHOOK_SECRET,
                    )
                    if ok:
                        logger.info(f"Webhook configurado exitosamente (intento {attempt + 1})")
                        return
                    logger.warning(f"set_webhook retornó False (intento {attempt + 1})")
                except Exception as e:
                    logger.warning(f"set_webhook intento {attempt + 1} fallido: {e}")
            logger.error("No se pudo configurar el webhook después de 3 intentos.")

        asyncio.create_task(_set_webhook())


async def post_shutdown(app: Application) -> None:
    sch = app.bot_data.get("scheduler")
    if sch and sch.running:
        sch.shutdown(wait=False)


async def error_handler(update, context) -> None:
    if isinstance(context.error, Conflict):
        logger.warning("Telegram 409 Conflict: otra instancia activa, ignorando.")
        return
    if isinstance(context.error, NetworkError):
        logger.warning(f"NetworkError transitorio: {context.error}")
        return
    logger.error(f"Error en update {update}: {context.error}", exc_info=context.error)


def main() -> None:
    validate_config()  # fallar rápido si falta alguna variable de entorno

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

    if WEBHOOK_URL:
        logger.info(f"Modo webhook en puerto {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            secret_token=WEBHOOK_SECRET,
            # Sin webhook_url: PTB no llama a setWebhook en el arranque.
            # El webhook se registra en post_init con delay (ver _set_webhook).
            # Sin drop_pending_updates: no borra el webhook existente al arrancar,
            # así las actualizaciones siguen llegando desde el deploy anterior.
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Modo polling (local)")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
