"""Trabajos programados: reporte semanal (lunes 9:00) y chequeo diario (8:00)."""
import asyncio
import logging

from config import AUTHORIZED_USER_ID, MIN_BALANCE_USD, MIN_BALANCE_UYU
from kkaynbot.bot import reports
from kkaynbot.sheets.actions import exe
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.config_tab import get_config, log_rate
from kkaynbot.utils.helpers import usd_rate

logger = logging.getLogger(__name__)


async def weekly_report(app) -> None:
    """Lunes 9:00 — resumen de la semana pasada + metas + estado global."""
    try:
        ctx = await asyncio.to_thread(get_ctx, True)
        if not ctx:
            logger.warning("weekly_report: Sheets no disponible")
            return
        partes = ["📅 *REPORTE SEMANAL*", "", reports.semana_msg(ctx)]
        try:
            cfg = await asyncio.to_thread(get_config)
            if cfg.get("metas"):
                partes += ["", reports.metas_msg(cfg, ctx)]
        except Exception as e:
            logger.warning(f"weekly_report metas: {e}")
        try:
            partes += ["", await asyncio.to_thread(exe, {"tipo": "resumen"})]
        except Exception as e:
            logger.warning(f"weekly_report resumen: {e}")
        await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,
                                   text="\n".join(partes), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"weekly_report: {e}")


async def check_balance(app) -> None:
    """Diario 8:00 — registra la cotización del día y alerta saldos bajos con el faltante."""
    try:
        ctx = await asyncio.to_thread(get_ctx, True)
        # historial de cotización (para ver la tendencia en la pestaña Cotización)
        try:
            await asyncio.to_thread(log_rate, ctx.get("rate") or usd_rate())
        except Exception as e:
            logger.warning(f"log_rate: {e}")
        alertas = []
        for cuenta, saldo in ctx.get("saldos", {}).items():
            minimo = MIN_BALANCE_UYU if "UYU" in cuenta else MIN_BALANCE_USD
            sym = "$" if "UYU" in cuenta else "U$S"
            if 0 < saldo < minimo:
                alertas.append(
                    f"⚠️ {cuenta}: {sym} {saldo:,.2f} — te faltan {sym} {minimo - saldo:,.2f} "
                    f"para el mínimo ({sym} {minimo:,.0f})")
        if alertas:
            await app.bot.send_message(chat_id=AUTHORIZED_USER_ID,
                                       text="🚨 *SALDO BAJO*\n\n" + "\n".join(alertas),
                                       parse_mode="Markdown")
    except Exception as e:
        logger.error(f"check_balance: {e}")
