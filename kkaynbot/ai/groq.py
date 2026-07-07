"""Cliente de Groq: llamada al LLM con JSON mode, parsing robusto y ejecución de acciones."""
import json
import logging
import re
import time

import requests

from config import (CUENTAS, GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_TIMEOUT,
                    HISTORY_LIMIT, conversation_history)
from kkaynbot.ai.prompt import SYSTEM_PROMPT
from kkaynbot.sheets.actions import exe
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.config_tab import get_config
from kkaynbot.utils.aio import run_blocking

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


class GroqError(Exception):
    """Error hablando con Groq; el mensaje es apto para mostrarle al usuario."""


def groq(msgs: list, json_mode: bool = True) -> str:
    """Llama a Groq con reintentos ante errores transitorios. Bloqueante (correr en thread).

    Usa response_format json_object para que el modelo devuelva JSON válido;
    si el JSON mode falla (400 json_validate_failed), reintenta en modo libre.
    """
    ultimo = ""
    for attempt in range(GROQ_MAX_RETRIES + 1):
        payload = {"model": GROQ_MODEL, "messages": msgs,
                   "temperature": 0.1, "max_tokens": 1000}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json=payload, timeout=GROQ_TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"conexión: {e}"
            logger.warning(f"Groq intento {attempt + 1}: {ultimo}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        ultimo = f"{r.status_code}: {r.text[:200]}"
        if r.status_code == 400 and "json_validate_failed" in r.text and json_mode:
            logger.warning("Groq json_mode falló, reintentando en modo libre")
            json_mode = False
            continue
        if r.status_code in (429, 500, 502, 503):
            logger.warning(f"Groq transitorio intento {attempt + 1}: {ultimo}")
            time.sleep(2 * (attempt + 1))
            continue
        break
    logger.error(f"Groq sin respuesta: {ultimo}")
    raise GroqError("El cerebro del bot (Groq) no está respondiendo ahora. Probá de nuevo en un ratito.")


def _parse_json(raw: str) -> dict:
    """Parsea la respuesta del LLM tolerando fences de markdown y texto alrededor."""
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        ini, fin = raw.find("{"), raw.rfind("}")
        if ini == -1 or fin <= ini:
            raise
        parsed = json.loads(raw[ini:fin + 1])
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("la respuesta no es un objeto", raw, 0)
    return parsed


async def _run_action(a: dict) -> str:
    """Ejecuta una acción en un thread y traduce los errores a mensajes amigables."""
    try:
        return await run_blocking(exe, a)
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        logger.error(f"exe({a.get('tipo')}): {e}", exc_info=True)
        return "❌ Hubo un problema con la planilla al registrar esto. Probá de nuevo en un rato."


async def process_msg(update, user_message: str) -> str:
    """Procesa un mensaje de texto: arma el contexto, consulta a Groq y ejecuta acciones."""
    uid = update.effective_user.id
    ctx = await run_blocking(get_ctx)
    cfg = await run_blocking(get_config)
    sys = SYSTEM_PROMPT.format(
        saldos=json.dumps(ctx.get("saldos", {}), ensure_ascii=False),
        ult=json.dumps(ctx.get("ult", []), ensure_ascii=False),
        inv=json.dumps(ctx.get("inv", []), ensure_ascii=False),
        rate=ctx.get("rate", 40),
        iu=ctx.get("iu", 0),
        eu=ctx.get("eu", 0),
        id_=ctx.get("id", 0),
        ed=ctx.get("ed", 0),
        presupuestos=json.dumps(cfg.get("presupuestos", {}), ensure_ascii=False),
        metas=json.dumps(cfg.get("metas", []), ensure_ascii=False),
        cuentas=", ".join(CUENTAS),
    )
    historia = conversation_history.setdefault(uid, [])
    historia.append({"role": "user", "content": user_message})
    del historia[:-HISTORY_LIMIT]
    try:
        raw = await run_blocking(groq, [{"role": "system", "content": sys}] + historia)
        parsed = _parse_json(raw)
    except GroqError:
        historia.pop()  # no dejar el mensaje colgado sin respuesta en el historial
        raise
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        historia.pop()
        logger.error(f"Respuesta de Groq no parseable: {e}")
        raise GroqError("No pude interpretar eso 🤔 ¿Me lo decís con otras palabras?")
    historia.append({"role": "assistant", "content": raw})

    acciones = parsed.get("acciones")
    accion = parsed.get("accion")
    resp = parsed.get("respuesta", "")

    if acciones and isinstance(acciones, list):
        res = []
        for a in acciones:
            if isinstance(a, dict):
                r = await _run_action(a)
                if r:
                    res.append(r)
        if len(res) > 3:
            return f"✅ *{len(res)} operaciones ejecutadas.*\n{resp}"
        return "\n\n".join(res) if res else resp
    if isinstance(accion, dict):
        r = await _run_action(accion)
        return r if r else resp
    return resp
