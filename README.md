# KkaynBot - Gestión Financiera Personal

Bot de Telegram para gestión financiera personal conectado a Google Sheets.
Corre en una VM Ubuntu (Oracle Cloud Always Free) como servicio systemd, con
long polling (sin webhook, sin puerto público).

## Variables de entorno (definidas en `/etc/systemd/system/kkaynbot.service`)

| Variable | Valor |
|----------|-------|
| TELEGRAM_TOKEN | Token de @BotFather |
| GROQ_API_KEY | Clave de Groq (console.groq.com) |
| SPREADSHEET_ID | ID de la planilla de Google Sheets |
| AUTHORIZED_USER_ID | Tu ID de Telegram (obtenerlo con @userinfobot) |
| MIN_BALANCE_UYU | 500 (alerta si una cuenta UYU baja de este valor) |
| MIN_BALANCE_USD | 50 (alerta si una cuenta USD baja de este valor) |

Las credenciales de Google **no** van en una variable de entorno: se leen del
archivo `/home/ubuntu/kkaynbot/credentials.json` (podés cambiar la ruta con la
variable opcional `GOOGLE_CREDENTIALS_FILE`, o seguir usando
`GOOGLE_CREDENTIALS_JSON` con el contenido del JSON si preferís esa vía).

## Operación del servicio

```bash
sudo systemctl status kkaynbot
sudo systemctl restart kkaynbot
journalctl -u kkaynbot -f      # logs en vivo
```

## Comandos del bot

- `/start` — ayuda y ejemplos
- `/setup` — crea/actualiza la estructura de Sheets (solo hace falta la primera vez o tras un cambio de diseño)
- `/resumen` — resumen global con top 3 categorías del mes
- `/saldo` — saldos de todas las cuentas con variación vs. mes anterior
- `/mes` — detalle del mes por categoría, comparado con el mes anterior
- `/semana` — resumen de los últimos 7 días
- `/metas` — progreso de las metas de ahorro
- `/presupuesto [categoría] [monto]` — definir o listar presupuestos mensuales
- `/exportar [todo]` — CSV del mes (o del histórico completo) por Telegram
- `/limpiar` — borra el historial de conversación (pide confirmación)
- `/reiniciar` — borra TODOS los movimientos de la planilla (pide confirmación)

## Ejemplos de uso

- "gasté 1k5 en el súper con Itaú"
- "cobré sueldo 50k en BBVA"
- "pasé 10k de BBVA a Itaú"
- "compré 100 dólares a 41 con plata del BBVA"
- "tope de 15k por mes para Alimentación"
- "quiero ahorrar 500 USD para diciembre"
- "¿cuánto tengo en BBVA?"
