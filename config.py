"""Configuración central de KkaynBot: variables de entorno, constantes y validación."""
import json
import os
from pathlib import Path

import pytz


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default) or default)
    except ValueError:
        return int(default)


def _float_env(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default) or default)
    except ValueError:
        return float(default)


# ── Variables de entorno ──
TELEGRAM_TOKEN          = os.environ.get("TELEGRAM_TOKEN", "").strip()
GROQ_API_KEY            = os.environ.get("GROQ_API_KEY", "").strip()
SPREADSHEET_ID          = os.environ.get("SPREADSHEET_ID", "").strip()
# Credenciales de Google: primero la variable de entorno; si no está, se lee el
# archivo GOOGLE_CREDENTIALS_FILE (default: credentials.json junto a este archivo).
# En Oracle Cloud el service file no define la variable y se usa el archivo.
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
GOOGLE_CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "").strip() or \
    str(Path(__file__).resolve().parent / "credentials.json")
if not GOOGLE_CREDENTIALS_JSON:
    try:
        GOOGLE_CREDENTIALS_JSON = Path(GOOGLE_CREDENTIALS_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        pass  # validate_config() lo reporta con mensaje claro
AUTHORIZED_USER_ID      = _int_env("AUTHORIZED_USER_ID", "0")
MIN_BALANCE_UYU         = _float_env("MIN_BALANCE_UYU", "500")
MIN_BALANCE_USD         = _float_env("MIN_BALANCE_USD", "50")

# ── Constantes de comportamiento ──
CACHE_TTL_SECONDS  = 20    # vida del caché del contexto de Sheets
CONFIG_TTL_SECONDS = 300   # vida del caché de presupuestos/metas
HISTORY_LIMIT      = 12    # mensajes de conversación que se mandan a Groq
ULT_MOVS_LIMIT     = 20    # últimos movimientos incluidos en el contexto del prompt
GROQ_TIMEOUT       = 30    # segundos por request a Groq
GROQ_MAX_RETRIES   = 2     # reintentos ante errores transitorios de Groq
SHEETS_MAX_RETRIES = 3     # reintentos ante rate limits de Sheets
RETRY_BASE_SECONDS = 10    # backoff exponencial: ~10s, ~20s, ~40s
RETRY_MAX_SECONDS  = 60    # tope del backoff
RATE_LIMIT_MSGS    = 15    # mensajes máximos por ventana (anti-spam)
RATE_LIMIT_WINDOW  = 60    # segundos de la ventana anti-spam
USD_RATE_TTL       = 3600  # caché de cotización USD/UYU (1 hora)
USD_RATE_FALLBACK  = 40.0  # solo si NUNCA se consiguió una cotización real
BUDGET_WARN_PCT    = 80    # % del presupuesto que dispara la advertencia

UYU_TZ  = pytz.timezone("America/Montevideo")
SCOPES  = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CUENTAS = ["BBVA UYU", "BBVA USD", "Itaú UYU", "Itaú USD", "Efectivo UYU", "Efectivo USD"]
BANCOS  = [("BBVA", "BBVA UYU", "BBVA USD"), ("ITAÚ", "Itaú UYU", "Itaú USD"), ("EFECTIVO", "Efectivo UYU", "Efectivo USD")]

# ── Inversiones ──
# "Inversiones" es la pestaña de VISTA (se rearma sola); "Inv Data" es el storage crudo.
INV_STORAGE_TAB = "Inv Data"
INV_DISPLAY_TAB = "Inversiones"
# Dos plataformas, cada una con sus activos. "descuenta": si al invertir se resta
# de una cuenta USD (XTB: compra directa con tarjeta) o no (Binance: se compra
# el USDT por P2P y ese gasto se registra aparte).
INVERSIONES = {
    "BINANCE": {"tipo": "Criptomonedas", "moneda": "USD", "descuenta": False,
                "activos": ["BITCOIN", "ETHEREUM", "SOLANA"]},
    "XTB":     {"tipo": "Acciones",      "moneda": "USD", "descuenta": True,
                "activos": ["SP500", "QQQ", "ORO", "NVIDIA"]},
}
# Cómo escribe Seba cada activo → nombre canónico
INV_ALIASES = {
    "btc": "BITCOIN", "bitcoin": "BITCOIN", "xbt": "BITCOIN",
    "eth": "ETHEREUM", "ethereum": "ETHEREUM", "etherium": "ETHEREUM", "ether": "ETHEREUM",
    "sol": "SOLANA", "solana": "SOLANA",
    "sp500": "SP500", "sp": "SP500", "spx": "SP500", "s&p": "SP500", "s&p500": "SP500", "sp 500": "SP500",
    "qqq": "QQQ", "nasdaq": "QQQ", "nasdaq100": "QQQ",
    "oro": "ORO", "gold": "ORO", "xau": "ORO",
    "nvidia": "NVIDIA", "nvda": "NVIDIA",
}
# activo canónico → plataforma
ACTIVO_PLATAFORMA = {a: p for p, c in INVERSIONES.items() for a in c["activos"]}

conversation_history: dict = {}


def validate_config() -> None:
    """Verifica la configuración crítica al arrancar. Falla rápido con un mensaje claro."""
    faltan = []
    if not TELEGRAM_TOKEN:
        faltan.append("TELEGRAM_TOKEN")
    if not GROQ_API_KEY:
        faltan.append("GROQ_API_KEY")
    if not SPREADSHEET_ID:
        faltan.append("SPREADSHEET_ID")
    if AUTHORIZED_USER_ID <= 0:
        faltan.append("AUTHORIZED_USER_ID")
    if not GOOGLE_CREDENTIALS_JSON:
        faltan.append(f"GOOGLE_CREDENTIALS_JSON (variable o archivo {GOOGLE_CREDENTIALS_FILE})")
    else:
        try:
            json.loads(GOOGLE_CREDENTIALS_JSON)
        except json.JSONDecodeError:
            faltan.append("GOOGLE_CREDENTIALS_JSON (no contiene JSON válido)")
    if faltan:
        raise SystemExit(
            "❌ Configuración incompleta. Revisá estas variables de entorno del servicio: "
            + ", ".join(faltan)
        )
