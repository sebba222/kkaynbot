import os
import pytz

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY")
SPREADSHEET_ID     = os.environ.get("SPREADSHEET_ID")
AUTHORIZED_USER_ID = int(os.environ.get("AUTHORIZED_USER_ID", "0"))
MIN_BALANCE_UYU    = float(os.environ.get("MIN_BALANCE_UYU", "500"))
MIN_BALANCE_USD    = float(os.environ.get("MIN_BALANCE_USD", "50"))
WEBHOOK_URL        = os.environ.get("WEBHOOK_URL", "").strip().rstrip("/")  # ej: https://kkaynbot.railway.app
PORT               = int(os.environ.get("PORT", "8443"))

UYU_TZ  = pytz.timezone("America/Montevideo")
SCOPES  = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]
BANCOS  = [("BBVA", "BBVA UYU", "BBVA USD"), ("ITAÚ", "Itaú UYU", "Itaú USD"), ("EFECTIVO", "Efectivo UYU", "Efectivo USD")]

conversation_history = {}
