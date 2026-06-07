# KkaynBot - Gestión Financiera Personal

Bot de Telegram para gestión financiera conectado a Google Sheets.

## Variables de entorno necesarias en Render:

| Variable | Valor |
|----------|-------|
| TELEGRAM_TOKEN | Token de @BotFather |
| GEMINI_API_KEY | Clave de Google AI Studio |
| SPREADSHEET_ID | 1hH7zTGUZuJ1m9xCkkE23KMCN_2lVBrGf43qBxZYjq6k |
| AUTHORIZED_USER_ID | Tu ID de Telegram (obtenerlo con @userinfobot) |
| GOOGLE_CREDENTIALS_JSON | Contenido del archivo JSON de credenciales |
| MIN_BALANCE_UYU | 500 (alerta si cuenta UYU baja de este valor) |
| MIN_BALANCE_USD | 50 (alerta si cuenta USD baja de este valor) |

## Comandos del bot:

- /start - Iniciar el bot
- /setup - Configurar hojas de Google Sheets (solo la primera vez)
- /resumen - Ver resumen global
- /saldo - Ver saldos de todas las cuentas

## Ejemplos de uso:

- "gasté 500 pesos en supermercado con Itaú"
- "cobré sueldo 8000 en BBVA"
- "pasé 4000 de BBVA a Itaú"
- "puse 200 dólares en BTC desde Itaú USD"
- "gasté 40 en alfajor" (el bot preguntará la cuenta)
