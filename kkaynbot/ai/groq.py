import json
import re
import logging
import requests

from config import GROQ_API_KEY, CUENTAS, conversation_history
from kkaynbot.ai.prompt import SYSTEM_PROMPT
from kkaynbot.sheets.client import get_ctx
from kkaynbot.sheets.actions import exe

logger = logging.getLogger(__name__)

def groq(msgs):
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": msgs, "temperature": 0.1, "max_tokens": 1000},
        timeout=30)
    if r.status_code != 200: raise Exception(f"Groq {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"].strip()

async def process_msg(update, user_message):
    uid = update.effective_user.id; ctx = get_ctx()
    if uid not in conversation_history: conversation_history[uid] = []
    sys = SYSTEM_PROMPT.format(
        saldos=json.dumps(ctx.get('saldos', {}), ensure_ascii=False),
        ult=json.dumps(ctx.get('ult', []), ensure_ascii=False),
        inv=json.dumps(ctx.get('inv', []), ensure_ascii=False),
        rate=ctx.get('rate', 40),
        iu=ctx.get('iu', 0),
        eu=ctx.get('eu', 0),
        id_=ctx.get('id', 0),
        ed=ctx.get('ed', 0),
        cuentas=', '.join(CUENTAS),
    )
    conversation_history[uid].append({"role": "user", "content": user_message})
    if len(conversation_history[uid]) > 10: conversation_history[uid] = conversation_history[uid][-10:]
    raw = groq([{"role": "system", "content": sys}] + conversation_history[uid])
    raw = re.sub(r'```json\s*', '', raw); raw = re.sub(r'```\s*', '', raw)
    parsed = json.loads(raw)
    conversation_history[uid].append({"role": "assistant", "content": raw})
    acciones = parsed.get("acciones"); accion = parsed.get("accion"); resp = parsed.get("respuesta", "")
    if acciones and isinstance(acciones, list):
        res = []
        for a in acciones:
            try:
                r = exe(a)
                if r: res.append(r)
            except Exception as e: res.append(f"❌ {e}")
        if len(res) > 3: return f"✅ *{len(res)} operaciones ejecutadas.*\n{resp}"
        return "\n\n".join(res) if res else resp
    elif accion:
        r = exe(accion); return r if r else resp
    return resp
