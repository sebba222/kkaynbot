"""Handlers de Telegram: comandos, mensajes de texto y confirmaciones inline."""
import functools
import logging
import time
from collections import deque
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from config import (AUTHORIZED_USER_ID, RATE_LIMIT_MSGS, RATE_LIMIT_WINDOW,
                    conversation_history)
from kkaynbot.ai.groq import GroqError, process_msg
from kkaynbot.bot import reports
from kkaynbot.sheets.actions import exe
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.config_tab import get_config, set_budget
from kkaynbot.sheets.setup import reiniciar_sheets, setup_sheets
from kkaynbot.utils.aio import run_blocking
from kkaynbot.utils.helpers import parse_amount

logger = logging.getLogger(__name__)

_msg_times: deque = deque()  # timestamps de mensajes recientes (anti-spam)


def authorized(handler):
    """Restringe el handler al único usuario autorizado. Loggea los intentos ajenos."""
    @functools.wraps(handler)
    async def wrapper(u, c):
        user = u.effective_user
        if not user or user.id != AUTHORIZED_USER_ID:
            logger.warning(f"Acceso denegado a user_id={getattr(user, 'id', '?')}")
            return
        return await handler(u, c)
    return wrapper


def _rate_limited() -> bool:
    """True si se superó el máximo de mensajes por ventana."""
    now = time.monotonic()
    while _msg_times and now - _msg_times[0] > RATE_LIMIT_WINDOW:
        _msg_times.popleft()
    if len(_msg_times) >= RATE_LIMIT_MSGS:
        return True
    _msg_times.append(now)
    return False


async def _reply(u, text: str, **kw):
    """Responde con Markdown; si el formato rompe (caracteres raros), cae a texto plano."""
    try:
        await u.effective_message.reply_text(text, parse_mode="Markdown", **kw)
    except BadRequest:
        await u.effective_message.reply_text(text, **kw)


@authorized
async def start(u, c):
    await _reply(u,
        "👋 *KkaynBot* listo.\n\n"
        "Habláme normal:\n"
        "• _gasté 300 en farmacia con Itaú_\n"
        "• _cobré sueldo 50k en BBVA_\n"
        "• _pasé 10k de BBVA a Itaú_\n"
        "• _tope de 15k por mes para Alimentación_\n"
        "• _quiero ahorrar 500 USD para diciembre_\n"
        "• _¿cuánto tengo en BBVA?_\n\n"
        "Comandos: /saldo /resumen /mes /semana /metas /presupuesto /exportar /limpiar /setup")


@authorized
async def cmd_setup(u, c):
    await u.effective_message.reply_text("⚙️ Aplicando diseño...")
    try:
        r = await run_blocking(setup_sheets)
        await _reply(u, r)
    except Exception as e:
        logger.error(f"setup: {e}", exc_info=True)
        await u.effective_message.reply_text(
            "❌ No pude actualizar la estructura. Fijate los logs del servidor "
            "(journalctl -u kkaynbot).")


@authorized
async def cmd_resumen(u, c):
    await u.effective_message.reply_text("🔄 Calculando...")
    try:
        r = await run_blocking(exe, {"tipo": "resumen"})
        await _reply(u, r)
    except ValueError as e:
        await u.effective_message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.error(f"resumen: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude armar el resumen. Probá en unos segundos.")


@authorized
async def cmd_saldo(u, c):
    try:
        ctx = await run_blocking(get_ctx, True)
        if not ctx:
            await u.effective_message.reply_text("⏳ Sheets no está disponible, intentá en unos segundos.")
            return
        await _reply(u, reports.saldo_msg(ctx))
    except Exception as e:
        logger.error(f"saldo: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude leer los saldos. Probá en unos segundos.")


@authorized
async def cmd_mes(u, c):
    try:
        ctx = await run_blocking(get_ctx, True)
        if not ctx:
            await u.effective_message.reply_text("⏳ Sheets no está disponible, intentá en unos segundos.")
            return
        await _reply(u, reports.mes_msg(ctx))
    except Exception as e:
        logger.error(f"mes: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude armar el resumen del mes.")


@authorized
async def cmd_semana(u, c):
    try:
        ctx = await run_blocking(get_ctx, True)
        if not ctx:
            await u.effective_message.reply_text("⏳ Sheets no está disponible, intentá en unos segundos.")
            return
        await _reply(u, reports.semana_msg(ctx))
    except Exception as e:
        logger.error(f"semana: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude armar el resumen de la semana.")


@authorized
async def cmd_metas(u, c):
    try:
        ctx = await run_blocking(get_ctx)
        cfg = await run_blocking(get_config)
        await _reply(u, reports.metas_msg(cfg, ctx))
    except Exception as e:
        logger.error(f"metas: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude leer las metas.")


@authorized
async def cmd_presupuesto(u, c):
    """Sin argumentos lista el estado; con argumentos define: /presupuesto Alimentación 15000."""
    args = c.args or []
    try:
        if not args:
            ctx = await run_blocking(get_ctx)
            cfg = await run_blocking(get_config)
            await _reply(u, reports.presupuestos_msg(cfg, ctx))
            return
        if args[0].lower() in ("borrar", "eliminar") and len(args) >= 2:
            cat, monto = " ".join(args[1:]), 0.0
        else:
            monto = parse_amount(args[-1])
            cat = " ".join(args[:-1])
            if monto is None or not cat:
                await _reply(u, "Usá: `/presupuesto Alimentación 15000` o `/presupuesto borrar Alimentación`")
                return
        r = await run_blocking(set_budget, cat, monto)
        await _reply(u, r)
    except Exception as e:
        logger.error(f"presupuesto: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude guardar el presupuesto.")


@authorized
async def cmd_exportar(u, c):
    """Genera un CSV del mes (o de todo con /exportar todo) y lo manda como documento."""
    alcance = "todo" if (c.args and c.args[0].lower() == "todo") else "mes"
    try:
        ctx = await run_blocking(get_ctx, True)
        if not ctx:
            await u.effective_message.reply_text("⏳ Sheets no está disponible, intentá en unos segundos.")
            return
        nombre, data = reports.csv_export(ctx, alcance)
        doc = BytesIO(data)
        doc.name = nombre
        etiqueta = "del mes" if alcance == "mes" else "completo"
        await u.effective_message.reply_document(document=doc, filename=nombre,
                                                 caption=f"📎 Export {etiqueta} listo.")
    except Exception as e:
        logger.error(f"exportar: {e}", exc_info=True)
        await u.effective_message.reply_text("❌ No pude generar el CSV.")


@authorized
async def cmd_limpiar(u, c):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🧹 Sí, limpiar", callback_data="limpiar_si"),
        InlineKeyboardButton("Cancelar", callback_data="cancelar"),
    ]])
    await u.effective_message.reply_text(
        "¿Limpio el historial de conversación? (los movimientos en la planilla no se tocan)",
        reply_markup=kb)


@authorized
async def cmd_reiniciar(u, c):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑️ Sí, borrar TODO", callback_data="reiniciar_si"),
        InlineKeyboardButton("Cancelar", callback_data="cancelar"),
    ]])
    await u.effective_message.reply_text(
        "⚠️ Esto borra TODOS los registros de la planilla y deja los saldos en cero.\n"
        "¿Estás seguro?",
        reply_markup=kb)


@authorized
async def on_callback(u, c):
    """Procesa las confirmaciones de /limpiar y /reiniciar."""
    q = u.callback_query
    await q.answer()
    if q.data == "cancelar":
        await q.edit_message_text("👍 Cancelado, no toqué nada.")
    elif q.data == "limpiar_si":
        conversation_history[u.effective_user.id] = []
        await q.edit_message_text("🧹 Historial de conversación limpiado.")
    elif q.data == "reiniciar_si":
        await q.edit_message_text("🗑️ Borrando todos los registros...")
        try:
            r = await run_blocking(reiniciar_sheets)
            await q.edit_message_text(r)
        except Exception as e:
            logger.error(f"reiniciar: {e}", exc_info=True)
            await q.edit_message_text("❌ No pude reiniciar la planilla. Probá de nuevo.")


@authorized
async def handle_msg(u, c):
    if _rate_limited():
        await u.effective_message.reply_text(
            "⏳ Pará un toque, me mandaste demasiados mensajes seguidos. Esperá un minuto.")
        return
    texto = (u.effective_message.text or "").strip()
    if not texto:
        return
    await u.effective_message.reply_text("🤔 Procesando...")
    try:
        r = await process_msg(u, texto)
        await _reply(u, r or "🤷 No tengo nada para responder a eso.")
    except GroqError as e:
        await u.effective_message.reply_text(f"❌ {e}")
    except Exception as e:
        logger.error(f"handle_msg: {e}", exc_info=True)
        await u.effective_message.reply_text(
            "❌ Algo salió mal procesando eso. Quedó registrado en los logs; probá de nuevo.")
