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

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
UYU_TZ = pytz.timezone("America/Montevideo")


# Google Sheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

def get_usd_rate():
    """Obtiene cotización USD/UYU"""
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        data = response.json()
        return data["rates"].get("UYU", 40.0)
    except:
        return 40.0  # fallback

def get_spreadsheet():
    client = get_sheets_client()
    return client.open_by_key(SPREADSHEET_ID)

# ─── SETUP INICIAL DE PESTAÑAS ───────────────────────────────────────────────

def setup_sheets():
    """Crea y formatea las 3 pestañas si no existen"""
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]

    # Pestaña 1: Global
    if "Global" not in existing:
        ws = ss.add_worksheet(title="Global", rows=50, cols=10)
    else:
        ws = ss.worksheet("Global")
    
    ws.clear()
    ws.update("A1", [
        ["GESTIÓN FINANCIERA - RESUMEN GLOBAL"],
        ["Actualizado:", "", "=NOW()"],
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

    # Pestaña 2: Cuentas
    if "Cuentas" not in existing:
        ws2 = ss.add_worksheet(title="Cuentas", rows=1000, cols=8)
    else:
        ws2 = ss.worksheet("Cuentas")
    
    ws2.clear()
    ws2.update("A1", [["REGISTRO DE MOVIMIENTOS - CUENTAS"]])
    ws2.update("A3", [["Fecha", "Descripción", "Categoría", "Cuenta", "Moneda", "Ingreso", "Egreso", "Saldo"]])

    # Pestaña 3: Inversiones
    if "Inversiones" not in existing:
        ws3 = ss.add_worksheet(title="Inversiones", rows=500, cols=8)
    else:
        ws3 = ss.worksheet("Inversiones")
    
    ws3.clear()
    ws3.update("A1", [["REGISTRO DE INVERSIONES"]])
    ws3.update("A3", [["Fecha", "Activo", "Monto Invertido", "Moneda", "Cuenta Origen", "Cotización Entrada", "Notas"]])

    # Eliminar hoja default si existe
    if "Sheet1" in existing or "Hoja 1" in existing:
        try:
            ss.del_worksheet(ss.worksheet("Sheet1"))
        except:
            pass
        try:
            ss.del_worksheet(ss.worksheet("Hoja 1"))
        except:
            pass

    return "✅ Hojas configuradas correctamente"

# ─── FUNCIONES DE REGISTRO ────────────────────────────────────────────────────

CUENTAS_VALIDAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]
CATEGORIAS = ["Alimentación", "Transporte", "Salud", "Entretenimiento", "Servicios", "Ropa", "Inversión", "Transferencia", "Sueldo", "Freelance", "Ahorro", "Otro"]

def parse_message_with_gemini(text):
    """Usa Gemini para interpretar el mensaje del usuario"""
    prompt = f"""Eres un asistente de finanzas personales. El usuario escribe en español rioplatense (Uruguay).
    
Analiza este mensaje y extrae la información financiera:
"{text}"

Las cuentas disponibles son: BBVA UYU, BBVA USD, Itaú UYU, Itaú USD, Efectivo UYU, Efectivo USD

Categorías disponibles: Alimentación, Transporte, Salud, Entretenimiento, Servicios, Ropa, Inversión, Transferencia, Sueldo, Freelance, Ahorro, Otro

Tipos de operación: gasto, ingreso, transferencia, inversion

Responde SOLO con un JSON válido con esta estructura:
{{
  "tipo": "gasto" | "ingreso" | "transferencia" | "inversion" | "consulta" | "desconocido",
  "monto": número o null,
  "moneda": "UYU" | "USD" | null,
  "cuenta_origen": "nombre exacto de cuenta" o null,
  "cuenta_destino": "nombre exacto de cuenta" o null (solo para transferencias),
  "categoria": "categoria" o null,
  "descripcion": "descripción corta del movimiento",
  "activo": "nombre del activo" o null (solo para inversiones, ej: BTC, ETH),
  "campos_faltantes": ["lista de campos que faltan para completar el registro"]
}}

Ejemplos:
- "gasté 500 pesos en supermercado con Itaú" → tipo: gasto, monto: 500, moneda: UYU, cuenta_origen: "Itaú UYU", categoria: "Alimentación"
- "cobré sueldo 8000 en BBVA" → tipo: ingreso, monto: 8000, moneda: UYU, cuenta_origen: "BBVA UYU"
- "pasé 4000 de BBVA a Itaú" → tipo: transferencia, monto: 4000, moneda: UYU, cuenta_origen: "BBVA UYU", cuenta_destino: "Itaú UYU"
- "puse 200 dólares en BTC desde Itaú" → tipo: inversion, monto: 200, moneda: USD, cuenta_origen: "Itaú USD", activo: "BTC"
- "gasté 40 en alfajor" → campos_faltantes: ["cuenta"] (falta la cuenta)
"""
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Groq API error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    text_response = data["choices"][0]["message"]["content"].strip()
    
    # Limpiar markdown si viene con backticks
    text_response = re.sub(r'```json\s*', '', text_response)
    text_response = re.sub(r'```\s*', '', text_response)
    
    return json.loads(text_response)

def get_account_balance(ws, cuenta):
    """Calcula el saldo actual de una cuenta"""
    try:
        all_data = ws.get_all_values()
        balance = 0.0
        for row in all_data[3:]:  # Skip headers
            if len(row) >= 8 and row[3] == cuenta:
                ingreso = float(row[5].replace(',', '.')) if row[5] else 0
                egreso = float(row[6].replace(',', '.')) if row[6] else 0
                balance += ingreso - egreso
        return balance
    except:
        return 0.0

def register_movement(cuenta, descripcion, categoria, monto, tipo, moneda):
    """Registra un movimiento en la pestaña Cuentas"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Cuentas")
    
    all_data = ws.get_all_values()
    next_row = len(all_data) + 1
    if next_row < 5:
        next_row = 5
    
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    
    ingreso = monto if tipo == "ingreso" else ""
    egreso = monto if tipo == "gasto" else ""
    
    saldo_actual = get_account_balance(ws, cuenta)
    if tipo == "ingreso":
        nuevo_saldo = saldo_actual + monto
    else:
        nuevo_saldo = saldo_actual - monto
    
    ws.append_row([fecha, descripcion, categoria, cuenta, moneda, ingreso, egreso, nuevo_saldo])
    return nuevo_saldo

def register_investment(activo, monto, moneda, cuenta_origen, notas=""):
    """Registra una inversión"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Inversiones")
    ws_cuentas = ss.worksheet("Cuentas")
    
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    usd_rate = get_usd_rate() if moneda == "USD" else 1
    
    # Registrar en inversiones
    ws.append_row([fecha, activo, monto, moneda, cuenta_origen, usd_rate, notas])
    
    # Registrar egreso en cuenta origen
    saldo_cuenta = get_account_balance(ws_cuentas, cuenta_origen)
    nuevo_saldo = saldo_cuenta - monto
    ws_cuentas.append_row([fecha, f"Inversión en {activo}", "Inversión", cuenta_origen, moneda, "", monto, nuevo_saldo])
    
    return nuevo_saldo

def register_transfer(cuenta_origen, cuenta_destino, monto, moneda, descripcion):
    """Registra una transferencia entre cuentas"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Cuentas")
    
    fecha = datetime.now(UYU_TZ).strftime("%d/%m/%Y %H:%M")
    
    # Egreso de cuenta origen
    saldo_origen = get_account_balance(ws, cuenta_origen)
    nuevo_saldo_origen = saldo_origen - monto
    ws.append_row([fecha, f"Transferencia a {cuenta_destino}: {descripcion}", "Transferencia", cuenta_origen, moneda, "", monto, nuevo_saldo_origen])
    
    # Ingreso en cuenta destino
    saldo_destino = get_account_balance(ws, cuenta_destino)
    nuevo_saldo_destino = saldo_destino + monto
    ws.append_row([fecha, f"Transferencia desde {cuenta_origen}: {descripcion}", "Transferencia", cuenta_destino, moneda, monto, "", nuevo_saldo_destino])
    
    return nuevo_saldo_origen, nuevo_saldo_destino

def update_global_summary():
    """Actualiza el resumen global"""
    ss = get_spreadsheet()
    ws_global = ss.worksheet("Global")
    ws_cuentas = ss.worksheet("Cuentas")
    
    usd_rate = get_usd_rate()
    
    # Calcular saldos por cuenta
    saldos = {}
    for cuenta in CUENTAS_VALIDAS:
        saldos[cuenta] = get_account_balance(ws_cuentas, cuenta)
    
    total_uyu = sum(v for k, v in saldos.items() if "UYU" in k)
    total_usd = sum(v for k, v in saldos.items() if "USD" in k)
    total_en_uyu = total_uyu + (total_usd * usd_rate)
    total_en_usd = (total_uyu / usd_rate) + total_usd
    
    # Movimientos del mes
    now = datetime.now(UYU_TZ)
    all_data = ws_cuentas.get_all_values()
    ingresos_mes = 0
    egresos_mes = 0
    
    for row in all_data[3:]:
        if len(row) >= 7:
            try:
                fecha_str = row[0].split(" ")[0]
                fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
                if fecha.month == now.month and fecha.year == now.year:
                    if row[5]:
                        ingresos_mes += float(row[5].replace(',', '.'))
                    if row[6]:
                        egresos_mes += float(row[6].replace(',', '.'))
            except:
                pass
    
    ws_global.update("C5", [[round(total_uyu, 2)]])
    ws_global.update("C6", [[round(total_usd, 2)]])
    ws_global.update("C9", [[round(usd_rate, 2)]])
    ws_global.update("C10", [[round(total_en_uyu, 2)]])
    ws_global.update("C11", [[round(total_en_usd, 2)]])
    ws_global.update("C14", [[round(ingresos_mes, 2)]])
    ws_global.update("C15", [[round(egresos_mes, 2)]])
    ws_global.update("C16", [[round(ingresos_mes - egresos_mes, 2)]])

def get_global_summary_text():
    """Genera texto del resumen global"""
    ss = get_spreadsheet()
    ws_cuentas = ss.worksheet("Cuentas")
    usd_rate = get_usd_rate()
    
    saldos = {}
    for cuenta in CUENTAS_VALIDAS:
        saldos[cuenta] = get_account_balance(ws_cuentas, cuenta)
    
    total_uyu = sum(v for k, v in saldos.items() if "UYU" in k)
    total_usd = sum(v for k, v in saldos.items() if "USD" in k)
    total_en_uyu = total_uyu + (total_usd * usd_rate)
    total_en_usd = (total_uyu / usd_rate) + total_usd
    
    now = datetime.now(UYU_TZ)
    all_data = ws_cuentas.get_all_values()
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
    
    lines = [
        "📊 *RESUMEN GLOBAL*",
        f"📅 {now.strftime('%d/%m/%Y %H:%M')}",
        "",
        "💰 *Saldos por cuenta:*",
    ]
    for cuenta in CUENTAS_VALIDAS:
        moneda_sym = "$" if "UYU" in cuenta else "U$S"
        lines.append(f"  • {cuenta}: {moneda_sym} {saldos[cuenta]:,.2f}")
    
    lines += [
        "",
        "📈 *Totales:*",
        f"  • Total UYU: $ {total_uyu:,.2f}",
        f"  • Total USD: U$S {total_usd:,.2f}",
        f"  • Todo en UYU: $ {total_en_uyu:,.2f}",
        f"  • Todo en USD: U$S {total_en_usd:,.2f}",
        f"  • Cotización: 1 USD = $ {usd_rate:.2f}",
        "",
        f"📅 *Este mes ({now.strftime('%B')}):*",
        f"  • Ingresos: $ {ingresos_mes:,.2f}",
        f"  • Egresos: $ {egresos_mes:,.2f}",
        f"  • Balance: $ {ingresos_mes - egresos_mes:,.2f}",
    ]
    
    return "\n".join(lines)

# ─── HANDLERS DE TELEGRAM ─────────────────────────────────────────────────────

# Estado de conversación pendiente
pending_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔ No tenés acceso a este bot.")
        return
    
    await update.message.reply_text(
        "👋 ¡Hola Seba! Soy *KkaynBot*, tu asistente financiero.\n\n"
        "Podés decirme cosas como:\n"
        "• _gasté 500 pesos en supermercado con Itaú_\n"
        "• _cobré sueldo 8000 en BBVA_\n"
        "• _pasé 4000 de BBVA a Itaú_\n"
        "• _puse 200 dólares en BTC desde Itaú_\n\n"
        "Comandos:\n"
        "/resumen - Ver resumen global\n"
        "/saldo - Ver saldos de cuentas\n"
        "/setup - Configurar hojas (primera vez)\n",
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
        update_global_summary()
        text = get_global_summary_text()
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet("Cuentas")
        usd_rate = get_usd_rate()
        lines = ["💳 *SALDOS ACTUALES*\n"]
        for cuenta in CUENTAS_VALIDAS:
            saldo = get_account_balance(ws, cuenta)
            sym = "$" if "UYU" in cuenta else "U$S"
            lines.append(f"• {cuenta}: {sym} {saldo:,.2f}")
        lines.append(f"\n💱 Cotización: 1 USD = $ {usd_rate:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # Si hay una operación pendiente esperando confirmación
    if user_id in pending_data:
        pending = pending_data[user_id]
        
        # El usuario está respondiendo una pregunta pendiente
        if pending.get("esperando") == "cuenta":
            # Buscar cuenta en la respuesta
            cuenta_encontrada = None
            for c in CUENTAS_VALIDAS:
                if c.lower() in text.lower() or c.split()[0].lower() in text.lower():
                    # Inferir moneda si no está explícita
                    if "USD" in text.upper() or "dólar" in text.lower() or "dolar" in text.lower():
                        cuenta_encontrada = c.split()[0] + " USD"
                    else:
                        cuenta_encontrada = c.split()[0] + " UYU"
                    if cuenta_encontrada in CUENTAS_VALIDAS:
                        break
                    cuenta_encontrada = None
            
            if not cuenta_encontrada:
                # Intentar con Gemini
                resp = parse_message_with_gemini(f"La cuenta es: {text}. Las opciones son: {', '.join(CUENTAS_VALIDAS)}")
                cuenta_encontrada = resp.get("cuenta_origen")
            
            if cuenta_encontrada and cuenta_encontrada in CUENTAS_VALIDAS:
                pending["cuenta_origen"] = cuenta_encontrada
                pending.pop("esperando", None)
                await process_confirmed_operation(update, pending)
                del pending_data[user_id]
            else:
                await update.message.reply_text(
                    f"No entendí la cuenta. Las opciones son:\n" +
                    "\n".join([f"• {c}" for c in CUENTAS_VALIDAS])
                )
            return
    
    # Procesar nuevo mensaje
    await update.message.reply_text("🤔 Procesando...")
    
    try:
        parsed = parse_message_with_gemini(text)
        
        campos_faltantes = parsed.get("campos_faltantes", [])
        
        # Si falta la cuenta, preguntar
        if "cuenta" in campos_faltantes or (not parsed.get("cuenta_origen") and parsed.get("tipo") in ["gasto", "ingreso", "inversion"]):
            pending_data[user_id] = parsed
            pending_data[user_id]["esperando"] = "cuenta"
            await update.message.reply_text(
                f"Entendido: *{parsed.get('descripcion', text)}* por *{parsed.get('moneda', 'UYU')} {parsed.get('monto', '?')}*\n\n"
                f"¿Desde qué cuenta?\n" +
                "\n".join([f"• {c}" for c in CUENTAS_VALIDAS]),
                parse_mode="Markdown"
            )
            return
        
        await process_confirmed_operation(update, parsed)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ No pude procesar eso. Intentá ser más específico.\nEjemplo: _gasté 500 pesos en supermercado con Itaú_", parse_mode="Markdown")

async def process_confirmed_operation(update, data):
    """Procesa y registra la operación confirmada"""
    tipo = data.get("tipo")
    monto = data.get("monto")
    moneda = data.get("moneda", "UYU")
    cuenta_origen = data.get("cuenta_origen")
    cuenta_destino = data.get("cuenta_destino")
    categoria = data.get("categoria", "Otro")
    descripcion = data.get("descripcion", "Sin descripción")
    activo = data.get("activo")
    
    try:
        if tipo == "gasto":
            nuevo_saldo = register_movement(cuenta_origen, descripcion, categoria, monto, "gasto", moneda)
            update_global_summary()
            sym = "$" if moneda == "UYU" else "U$S"
            await update.message.reply_text(
                f"✅ *Gasto registrado*\n"
                f"📝 {descripcion}\n"
                f"💸 {sym} {monto:,.2f} | {categoria}\n"
                f"🏦 {cuenta_origen}\n"
                f"💰 Saldo nuevo: {sym} {nuevo_saldo:,.2f}",
                parse_mode="Markdown"
            )
        
        elif tipo == "ingreso":
            nuevo_saldo = register_movement(cuenta_origen, descripcion, categoria or "Sueldo", monto, "ingreso", moneda)
            update_global_summary()
            sym = "$" if moneda == "UYU" else "U$S"
            await update.message.reply_text(
                f"✅ *Ingreso registrado*\n"
                f"📝 {descripcion}\n"
                f"💚 {sym} {monto:,.2f} | {categoria or 'Ingreso'}\n"
                f"🏦 {cuenta_origen}\n"
                f"💰 Saldo nuevo: {sym} {nuevo_saldo:,.2f}",
                parse_mode="Markdown"
            )
        
        elif tipo == "transferencia":
            saldo_origen, saldo_destino = register_transfer(cuenta_origen, cuenta_destino, monto, moneda, descripcion)
            update_global_summary()
            sym = "$" if moneda == "UYU" else "U$S"
            await update.message.reply_text(
                f"✅ *Transferencia registrada*\n"
                f"📤 {cuenta_origen}: {sym} {saldo_origen:,.2f}\n"
                f"📥 {cuenta_destino}: {sym} {saldo_destino:,.2f}\n"
                f"💱 Monto: {sym} {monto:,.2f}",
                parse_mode="Markdown"
            )
        
        elif tipo == "inversion":
            nuevo_saldo = register_investment(activo, monto, moneda, cuenta_origen, descripcion)
            update_global_summary()
            sym = "$" if moneda == "UYU" else "U$S"
            await update.message.reply_text(
                f"✅ *Inversión registrada*\n"
                f"📈 Activo: {activo}\n"
                f"💸 Monto: {sym} {monto:,.2f}\n"
                f"🏦 Cuenta origen: {cuenta_origen}\n"
                f"💰 Nuevo saldo {cuenta_origen}: {sym} {nuevo_saldo:,.2f}",
                parse_mode="Markdown"
            )
        
        elif tipo == "consulta":
            text = get_global_summary_text()
            await update.message.reply_text(text, parse_mode="Markdown")
        
        else:
            await update.message.reply_text(
                "🤷 No entendí bien el tipo de operación. Probá con:\n"
                "• _gasté X en algo con cuenta_\n"
                "• _cobré X en cuenta_\n"
                "• _pasé X de cuenta a cuenta_\n"
                "• _puse X en BTC desde cuenta_",
                parse_mode="Markdown"
            )
    
    except Exception as e:
        logger.error(f"Error registrando: {e}")
        await update.message.reply_text(f"❌ Error al registrar: {e}")

# ─── REPORTE SEMANAL ──────────────────────────────────────────────────────────

async def send_weekly_report(app):
    """Envía reporte semanal automático los lunes"""
    try:
        update_global_summary()
        text = "📅 *REPORTE SEMANAL - LUNES*\n\n" + get_global_summary_text()
        await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error en reporte semanal: {e}")

async def check_low_balance(app):
    """Alerta si alguna cuenta está baja"""
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet("Cuentas")
        MIN_UYU = float(os.environ.get("MIN_BALANCE_UYU", "500"))
        MIN_USD = float(os.environ.get("MIN_BALANCE_USD", "50"))
        
        alerts = []
        for cuenta in CUENTAS_VALIDAS:
            saldo = get_account_balance(ws, cuenta)
            if "UYU" in cuenta and saldo < MIN_UYU and saldo > 0:
                alerts.append(f"⚠️ {cuenta}: $ {saldo:,.2f} (mínimo: $ {MIN_UYU:,.0f})")
            elif "USD" in cuenta and saldo < MIN_USD and saldo > 0:
                alerts.append(f"⚠️ {cuenta}: U$S {saldo:,.2f} (mínimo: U$S {MIN_USD:,.0f})")
        
        if alerts:
            msg = "🚨 *ALERTA DE SALDO BAJO*\n\n" + "\n".join(alerts)
            await app.bot.send_message(chat_id=AUTHORIZED_USER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error en check balance: {e}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Scheduler para tareas automáticas
    scheduler = AsyncIOScheduler(timezone=UYU_TZ)
    # Reporte semanal los lunes a las 9am
    scheduler.add_job(send_weekly_report, "cron", day_of_week="mon", hour=9, minute=0, args=[app])
    # Chequeo de saldo bajo cada día a las 8am
    scheduler.add_job(check_low_balance, "cron", hour=8, minute=0, args=[app])
    scheduler.start()
    
    logger.info("🤖 KkaynBot iniciado!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
