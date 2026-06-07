import os
import json
import logging
import re
from datetime import datetime, timedelta
import pytz
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
UYU_TZ = pytz.timezone("America/Montevideo")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS_VALIDAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]

# Historial de conversación por usuario (memoria)
conversation_history = {}

def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def get_usd_rate():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        data = response.json()
        return data["rates"].get("UYU", 40.0)
    except:
        return 40.0

def get_spreadsheet():
    client = get_sheets_client()
    return client.open_by_key(SPREADSHEET_ID)

def get_sheets_context():
    """Obtiene contexto completo del Excel para pasarle a la IA"""
    try:
        ss = get_spreadsheet()
        ws_cuentas = ss.worksheet("Cuentas")
        all_data = ws_cuentas.get_all_values()
        
        saldos = {}
        for cuenta in CUENTAS_VALIDAS:
            balance = 0.0
            for row in all_data[3:]:
                if len(row) >= 8 and row[3] == cuenta:
                    try:
                        ingreso = float(row[5].replace(',', '.')) if row[5] else 0
                        egreso = float(row[6].replace(',', '.')) if row[6] else 0
                        balance += ingreso - egreso
                    except:
                        pass
            saldos[cuenta] = balance
        
        # Últimos 10 movimientos
        ultimos = []
        for i, row in enumerate(all_data[3:], start=4):
            if len(row) >= 7 and (row[5] or row[6]):
                ultimos.append({
                    "fila": i,
                    "fecha": row[0],
                    "descripcion": row[1],
                    "categoria": row[2],
                    "cuenta": row[3],
                    "moneda": row[4],
                    "ingreso": row[5],
                    "egreso": row[6],
                    "saldo": row[7] if len(row) > 7 else ""
                })
        ultimos = ultimos[-10:]  # últimos 10
        
        # Inversiones
        ws_inv = ss.worksheet("Inversiones")
        inv_data = ws_inv.get_all_values()
        inversiones = []
        for row in inv_data[3:]:
            if len(row) >= 4 and row[1]:
                inversiones.append({"activo": row[1], "monto": row[2], "moneda": row[3], "fecha": row[0]})
        
        usd_rate = get_usd_rate()
        
        now = datetime.now(UYU_TZ)
        ingresos_mes = 0
        egresos_mes = 0
        for row in all_data[3:]:
            if len(row) >= 7:
                try:
                    fecha_str = row[0].split(" ")[0]
                    fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
                    if fecha.month == now.month and fecha.year == now.year:
                        if row[5]: ingresos_mes += float(row[5].replace(',', '.'))
                        if row[6]: egresos_mes += float(row[6].replace(',', '.'))
                except:
                    pass
        
        return {
            "saldos": saldos,
            "ultimos_movimientos": ultimos,
            "inversiones": inversiones,
            "usd_rate": usd_rate,
            "ingresos_mes": ingresos_mes,
            "egresos_mes": egresos_mes,
            "balance_mes": ingresos_mes - egresos_mes
        }
    except Exception as e:
        logger.error(f"Error obteniendo contexto: {e}")
        return {}

def call_groq(messages):
    """Llama a Groq con historial de conversación"""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1000
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Groq API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()

def setup_sheets():
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]

    if "Global" not in existing:
        ws = ss.add_worksheet(title="Global", rows=50, cols=10)
    else:
        ws = ss.worksheet("Global")
    ws.clear()
    ws.update("A1", [
        ["GESTIÓN FINANCIERA - RESUMEN GLOBAL"],
        ["Actualizado:", "", ""],
        [""],
        ["SALDOS POR MONEDA"],
        ["Total UYU (todas las cuentas)", "", ""],
        ["Total USD (todas las cuentas)", "", ""],
        [""],
        ["TOTALES CONVERTIDOS"],
        ["Cotización USD/UYU:", "", ""],
        ["Total general en UYU", "", ""],
        ["Total general en USD", "", ""],
        [""],
        ["MOVIMIENTOS DEL MES"],
        ["Ingresos del mes:", "", ""],
        ["Egresos del mes:", "", ""],
        ["Balance del mes:", "", ""],
    ])

    if "Cuentas" not in existing:
        ws2 = ss.add_worksheet(title="Cuentas", rows=1000, cols=8)
    else:
        ws2 = ss.worksheet("Cuentas")
    ws2.clear()
    ws2.update("A1", [["REGISTRO DE MOVIMIENTOS - CUENTAS"]])
    ws2.update("A3", [["Fecha", "Descripción", "Categoría", "Cuenta", "Moneda", "Ingreso", "Egreso", "Saldo"]])

    if "Inversiones" not in existing:
        ws3 = ss.add_worksheet(title="Inversiones", rows=500, cols=8)
    else:
        ws3 = ss.worksheet("Inversiones")
    ws3.clear()
    ws3.update("A1", [["REGISTRO DE INVERSIONES"]])
    ws3.update("A3", [["Fecha", "Activo", "Monto Invertido", "Moneda", "Cuenta Origen", "Cotización Entrada", "Notas"]])

    for hoja in ["Sheet1", "Hoja 1"]:
        if hoja in existing:
            try:
                ss.del_worksheet(ss.worksheet(hoja))
            except:
                pass

    return "✅ Hojas configuradas correctamente"

def update_global_summary():
    ctx = get_sheets_context()
    if not ctx:
        return
    ss = get_spreadsheet()
    ws_global = ss.worksheet("Global")
    saldos = ctx["saldos"]
    usd_rate = ctx["usd_rate"]
    total_uyu = sum(v for k, v in saldos.items() if "UYU" in k)
    total_usd = sum(v for k, v in saldos.items() if "USD" in k)
    total_en_uyu = total_uyu + (total_usd * usd_rate)
    total_en_usd = (total_uyu / usd_rate) + total_usd if usd_rate > 0 else 0
    now = datetime.now(UYU_TZ)
    ws_global.update("C2", [[now.strftime("%d/%m/%Y %H:%M")]])
    ws_global.update("C5", [[round(total_uyu, 2)]])
    ws_global.update("C6", [[round(total_usd, 2)]])
    ws_global.update("C9", [[round(usd_rate, 2)]])
    ws_global.update("C10", [[round(total_en_uyu, 2)]])
    ws_global.update("C11", [[round(total_en_usd, 2)]])
    ws_global.update("C14", [[round(ctx["ingresos_mes"], 2)]])
    ws_global.update("C15", [[round(ctx["egresos_mes"], 2)]])
    ws_global.update("C16", [[round(ctx["balance_mes"], 2)]])

def execute_action(action):
    """Ejecuta una acción sobre el Excel"""
    tipo = action.get("tipo")
    ss = get_spreadsheet()
    ws = ss.worksheet("Cuentas")
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    usd_rate = get_usd_rate()

    def get_balance(cuenta):
        all_data = ws.get_all_values()
        balance = 0.0
        for row in all_data[3:]:
            if len(row) >= 8 and row[3] == cuenta:
                try:
                    ingreso = float(row[5].replace(',', '.')) if row[5] else 0
                    egreso = float(row[6].replace(',', '.')) if row[6] else 0
                    balance += ingreso - egreso
                except:
                    pass
        return balance

    if tipo == "gasto":
        cuenta = action["cuenta"]
        monto = float(action["monto"])
        saldo = get_balance(cuenta) - monto
        ws.append_row([fecha, action["descripcion"], action.get("categoria", "Otro"), cuenta, action.get("moneda", "UYU"), "", monto, round(saldo, 2)])
        update_global_summary()
        sym = "$" if "UYU" in cuenta else "U$S"
        return f"✅ *Gasto registrado*\n📝 {action['descripcion']}\n💸 {sym} {monto:,.2f} | {action.get('categoria', 'Otro')}\n🏦 {cuenta}\n💰 Saldo nuevo: {sym} {saldo:,.2f}"

    elif tipo == "ingreso":
        cuenta = action["cuenta"]
        monto = float(action["monto"])
        saldo = get_balance(cuenta) + monto
        ws.append_row([fecha, action["descripcion"], action.get("categoria", "Sueldo"), cuenta, action.get("moneda", "UYU"), monto, "", round(saldo, 2)])
        update_global_summary()
        sym = "$" if "UYU" in cuenta else "U$S"
        return f"✅ *Ingreso registrado*\n📝 {action['descripcion']}\n💚 {sym} {monto:,.2f} | {action.get('categoria', 'Ingreso')}\n🏦 {cuenta}\n💰 Saldo nuevo: {sym} {saldo:,.2f}"

    elif tipo == "transferencia":
        origen = action["cuenta_origen"]
        destino = action["cuenta_destino"]
        monto = float(action["monto"])
        moneda = action.get("moneda", "UYU")
        saldo_origen = get_balance(origen) - monto
        saldo_destino = get_balance(destino) + monto
        ws.append_row([fecha, f"Transferencia a {destino}", "Transferencia", origen, moneda, "", monto, round(saldo_origen, 2)])
        ws.append_row([fecha, f"Transferencia desde {origen}", "Transferencia", destino, moneda, monto, "", round(saldo_destino, 2)])
        update_global_summary()
        sym = "$" if "UYU" in origen else "U$S"
        return f"✅ *Transferencia registrada*\n📤 {origen}: {sym} {saldo_origen:,.2f}\n📥 {destino}: {sym} {saldo_destino:,.2f}\n💱 Monto: {sym} {monto:,.2f}"

    elif tipo == "inversion":
        activo = action["activo"]
        monto = float(action["monto"])
        moneda = action.get("moneda", "USD")
        cuenta_origen = action["cuenta"]
        ws_inv = ss.worksheet("Inversiones")
        ws_inv.append_row([fecha, activo, monto, moneda, cuenta_origen, usd_rate, action.get("descripcion", "")])
        saldo_cuenta = get_balance(cuenta_origen) - monto
        ws.append_row([fecha, f"Inversión en {activo}", "Inversión", cuenta_origen, moneda, "", monto, round(saldo_cuenta, 2)])
        update_global_summary()
        sym = "$" if "UYU" in moneda else "U$S"
        return f"✅ *Inversión registrada*\n📈 Activo: {activo}\n💸 Monto: {sym} {monto:,.2f}\n🏦 Cuenta: {cuenta_origen}\n💰 Saldo nuevo: {sym} {saldo_cuenta:,.2f}"

    elif tipo == "eliminar":
        fila = action.get("fila")
        if fila:
            all_data = ws.get_all_values()
            fila_int = int(fila)
            if fila_int <= len(all_data):
                row_data = all_data[fila_int - 1]
                ws.delete_rows(fila_int)
                update_global_summary()
                return f"✅ *Movimiento eliminado*\n📝 {row_data[1] if len(row_data) > 1 else 'Movimiento'}"
        return "❌ No pude identificar qué eliminar. Decime más específico."

    elif tipo == "actualizar_saldo":
        cuenta = action["cuenta"]
        nuevo_saldo = float(action["saldo"])
        saldo_actual = get_balance(cuenta)
        diferencia = nuevo_saldo - saldo_actual
        moneda = "USD" if "USD" in cuenta else "UYU"
        if diferencia > 0:
            ws.append_row([fecha, "Ajuste de saldo", "Ajuste", cuenta, moneda, diferencia, "", nuevo_saldo])
        elif diferencia < 0:
            ws.append_row([fecha, "Ajuste de saldo", "Ajuste", cuenta, moneda, "", abs(diferencia), nuevo_saldo])
        update_global_summary()
        sym = "$" if "UYU" in cuenta else "U$S"
        return f"✅ *Saldo actualizado*\n🏦 {cuenta}\n💰 Nuevo saldo: {sym} {nuevo_saldo:,.2f}"

    elif tipo == "resumen":
        ctx = get_sheets_context()
        saldos = ctx["saldos"]
        usd_rate = ctx["usd_rate"]
        now = datetime.now(UYU_TZ)
        total_uyu = sum(v for k, v in saldos.items() if "UYU" in k)
        total_usd = sum(v for k, v in saldos.items() if "USD" in k)
        lines = ["📊 *RESUMEN GLOBAL*", f"📅 {now.strftime('%d/%m/%Y %H:%M')}", "", "💰 *Saldos:*"]
        for cuenta in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in cuenta else "U$S"
            lines.append(f"  • {cuenta}: {sym} {saldos[cuenta]:,.2f}")
        lines += [
            "", "📈 *Totales:*",
            f"  • Total UYU: $ {total_uyu:,.2f}",
            f"  • Total USD: U$S {total_usd:,.2f}",
            f"  • Todo en UYU: $ {total_uyu + total_usd * usd_rate:,.2f}",
            f"  • Todo en USD: U$S {total_uyu / usd_rate + total_usd:,.2f}",
            f"  • Cotización: 1 USD = $ {usd_rate:.2f}",
            "", f"📅 *Este mes:*",
            f"  • Ingresos: $ {ctx['ingresos_mes']:,.2f}",
            f"  • Egresos: $ {ctx['egresos_mes']:,.2f}",
            f"  • Balance: $ {ctx['balance_mes']:,.2f}"
        ]
        return "\n".join(lines)

    return "❌ No entendí la operación."

async def process_message(update: Update, user_message: str):
    """Procesa el mensaje con IA y contexto completo"""
    user_id = update.effective_user.id
    
    # Obtener contexto del Excel
    ctx = get_sheets_context()
    
    # Construir historial de conversación
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    # System prompt con contexto actual
    system_prompt = f"""Sos KkaynBot, el asistente financiero personal de Seba (Uruguay). 
Manejás su Google Sheets de gestión financiera mediante lenguaje natural en español rioplatense.

ESTADO ACTUAL DEL EXCEL:
Saldos: {json.dumps(ctx.get('saldos', {}), ensure_ascii=False)}
Últimos movimientos: {json.dumps(ctx.get('ultimos_movimientos', []), ensure_ascii=False)}
Inversiones: {json.dumps(ctx.get('inversiones', []), ensure_ascii=False)}
Cotización USD/UYU: {ctx.get('usd_rate', 40)}
Ingresos este mes: {ctx.get('ingresos_mes', 0)}
Egresos este mes: {ctx.get('egresos_mes', 0)}

CUENTAS DISPONIBLES: {', '.join(CUENTAS_VALIDAS)}

Tu tarea es interpretar el mensaje y responder con UN JSON que contenga:
1. "accion": la operación a ejecutar (o null si es solo consulta)
2. "respuesta": tu respuesta en lenguaje natural

Para "accion" usá esta estructura según el tipo:
- Gasto: {{"tipo":"gasto","cuenta":"BBVA UYU","monto":500,"moneda":"UYU","descripcion":"supermercado","categoria":"Alimentación"}}
- Ingreso: {{"tipo":"ingreso","cuenta":"BBVA UYU","monto":8000,"moneda":"UYU","descripcion":"sueldo","categoria":"Sueldo"}}
- Transferencia: {{"tipo":"transferencia","cuenta_origen":"BBVA UYU","cuenta_destino":"Itaú UYU","monto":4000,"moneda":"UYU"}}
- Inversión: {{"tipo":"inversion","activo":"BTC","cuenta":"Itaú USD","monto":200,"moneda":"USD"}}
- Eliminar: {{"tipo":"eliminar","fila":NUMERO_FILA}} (usá los datos de últimos_movimientos para saber qué fila)
- Actualizar saldo: {{"tipo":"actualizar_saldo","cuenta":"BBVA UYU","saldo":5000}}
- Resumen: {{"tipo":"resumen"}}
- Solo consulta/pregunta: null

IMPORTANTE:
- Si el usuario dice "el último", "ese", "lo que pusiste" → usá los últimos_movimientos para identificarlo
- Si falta info crítica (como la cuenta), PREGUNTÁ antes de ejecutar
- Para eliminar, buscá en últimos_movimientos el que más coincida con la descripción
- Respondé siempre en español rioplatense
- Sé conciso en las respuestas
- Si es una consulta/análisis, respondé directamente sin acción

Respondé SOLO con JSON válido, sin texto adicional:
{{"accion": {{...}} o null, "respuesta": "tu mensaje"}}"""

    # Agregar mensaje del usuario al historial
    conversation_history[user_id].append({"role": "user", "content": user_message})
    
    # Mantener solo los últimos 10 mensajes en el historial
    if len(conversation_history[user_id]) > 10:
        conversation_history[user_id] = conversation_history[user_id][-10:]
    
    messages = [{"role": "system", "content": system_prompt}] + conversation_history[user_id]
    
    response_text = call_groq(messages)
    
    # Parsear respuesta JSON
    response_text = re.sub(r'```json\s*', '', response_text)
    response_text = re.sub(r'```\s*', '', response_text)
    
    parsed = json.loads(response_text)
    accion = parsed.get("accion")
    respuesta = parsed.get("respuesta", "")
    
    # Agregar respuesta al historial
    conversation_history[user_id].append({"role": "assistant", "content": response_text})
    
    # Ejecutar acción si hay una
    if accion:
        resultado = execute_action(accion)
        if resultado:
            return resultado
    
    return respuesta

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ No tenés acceso a este bot.")
        return
    await update.message.reply_text(
        "👋 ¡Hola Seba! Soy *KkaynBot*, tu asistente financiero.\n\n"
        "Hablame natural, por ejemplo:\n"
        "• _cobré el sueldo, 8000 pesos en BBVA_\n"
        "• _gasté 500 en el súper con Itaú_\n"
        "• _pasé 4000 de BBVA a Itaú_\n"
        "• _puse 200 dólares en BTC desde Itaú_\n"
        "• _borrá el último movimiento_\n"
        "• _¿cuánto gasté esta semana?_\n"
        "• _¿cómo viene el mes?_\n\n"
        "Comandos:\n"
        "/resumen - Ver resumen global\n"
        "/saldo - Ver saldos\n"
        "/setup - Configurar hojas\n"
        "/limpiar - Borrar historial de chat",
        parse_mode="Markdown"
    )

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    await update.message.reply_text("⚙️ Configurando hojas...")
    try:
        result = setup_sheets()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    await update.message.reply_text("🔄 Calculando...")
    try:
        result = execute_action({"tipo": "resumen"})
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    try:
        ctx = get_sheets_context()
        saldos = ctx.get("saldos", {})
        usd_rate = ctx.get("usd_rate", 40)
        lines = ["💳 *SALDOS ACTUALES*\n"]
        for cuenta in CUENTAS_VALIDAS:
            sym = "$" if "UYU" in cuenta else "U$S"
            lines.append(f"• {cuenta}: {sym} {saldos.get(cuenta, 0):,.2f}")
        lines.append(f"\n💱 Cotización: 1 USD = $ {usd_rate:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("🧹 Historial limpiado. Empezamos de cero.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    
    text = update.message.text.strip()
    await update.message.reply_text("🤔 Procesando...")
    
    try:
        result = await process_message(update, text)
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def send_weekly_report(app):
    try:
        result = execute_action({"tipo": "resumen"})
        text = "📅 *REPORTE SEMANAL - LUNES*\n\n" + result
        await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error reporte semanal: {e}")

async def check_low_balance(app):
    try:
        ctx = get_sheets_context()
        saldos = ctx.get("saldos", {})
        MIN_UYU = float(os.environ.get("MIN_BALANCE_UYU", "500"))
        MIN_USD = float(os.environ.get("MIN_BALANCE_USD", "50"))
        alerts = []
        for cuenta, saldo in saldos.items():
            if "UYU" in cuenta and 0 < saldo < MIN_UYU:
                alerts.append(f"⚠️ {cuenta}: $ {saldo:,.2f} (mínimo $ {MIN_UYU:,.0f})")
            elif "USD" in cuenta and 0 < saldo < MIN_USD:
                alerts.append(f"⚠️ {cuenta}: U$S {saldo:,.2f} (mínimo U$S {MIN_USD:,.0f})")
        if alerts:
            await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text="🚨 *ALERTA SALDO BAJO*\n\n" + "\n".join(alerts), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error check balance: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("limpiar", cmd_limpiar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    scheduler = AsyncIOScheduler(timezone=UYU_TZ)
    scheduler.add_job(send_weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    scheduler.add_job(check_low_balance, "cron", hour=8, minute=0, args=[app])
    scheduler.start()
    
    logger.info("🤖 KkaynBot v2 iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
