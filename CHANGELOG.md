# CHANGELOG — KkaynBot v6

Mejora integral del bot (julio 2026). Sin dependencias nuevas; la estructura de las
4 pestañas originales (Global, Por Cuenta, Inversiones, Cuentas) **no cambió**.
Se agregan 2 pestañas nuevas (Config y Cotización) que se crean solas con `/setup`
o automáticamente la primera vez que se necesitan.

---

## Migración a Oracle Cloud (posterior a v6)

- `main.py` quedó solo en modo **polling** (se sacó toda la lógica de webhook/PORT de Railway).
- Las credenciales de Google se leen de `credentials.json` si no está la variable de entorno
  (configurable con `GOOGLE_CREDENTIALS_FILE`).
- Shim `run_blocking` (`kkaynbot/utils/aio.py`) para compatibilidad con Python 3.8 (Ubuntu 20.04).
- Se borraron `railway.toml`, `render.yaml`, `runtime.txt`; mensajes de error apuntan a
  `journalctl -u kkaynbot`.

## Rediseño de la pestaña Inversiones (posterior a v6)

La pestaña **Inversiones** pasó de ser una lista plana a una **vista por plataforma**,
que se reconstruye sola desde un storage nuevo (**Inv Data**), igual que "Por Cuenta"
se arma desde "Cuentas". La migración es automática con `/setup` (los activos
reconocidos del formato viejo se pasan al nuevo).

- **Dos secciones verticales**: 🪙 BINANCE (Bitcoin, Ethereum, Solana) y 📊 XTB
  (SP500, QQQ, Oro, Nvidia).
- **Bloques horizontales** por activo (FECHA + MONTO) con **total invertido** acumulado.
  Al mandar *"invertí 100 en BTC"* se agrega la fila y sube el total del activo.
- **Descuento diferenciado por plataforma**:
  - XTB (compra directa con tarjeta) → descuenta de la cuenta USD que indiques.
  - Binance (compra por P2P) → NO toca tus cuentas; el USDT lo registrás aparte
    (ej: *"compré 200 USDT por P2P con Itaú"*).
- Alias entendidos: BTC/Bitcoin, ETH/Ethereum, SOL/Solana, SP500/S&P, QQQ/Nasdaq,
  Oro/Gold, Nvidia/NVDA (con tolerancia a typos).
- Archivos: nuevo `kkaynbot/sheets/inversiones.py`; cambios en `config.py`,
  `utils/normalize.py` (`resolve_activo`), `sheets/client.py`, `sheets/actions.py`,
  `sheets/setup.py`, `ai/prompt.py`.

---

## Fase 2 — Robustez y manejo de errores

### El bot ya no se congela (`asyncio.to_thread`)
**Problema**: `exe()` y las llamadas a Groq eran bloqueantes y corrían directo en el
event loop. Cada `time.sleep(1)` de las transferencias, los 15/30/45s del retry y
cada request HTTP congelaban el bot ENTERO (mensajes, comandos y scheduler).
**Solución**: toda operación bloqueante ahora corre en un thread con
`asyncio.to_thread(...)`. La lógica interna de los sleeps de transferencias/edición
quedó **idéntica** (regla respetada), pero ya no bloquea nada.
Archivos: `bot/handlers.py`, `ai/groq.py`, `bot/scheduler.py`.

### Groq a prueba de fallas (`ai/groq.py`)
- **JSON mode** (`response_format: json_object`): elimina en la práctica el JSON
  malformado. Si Groq devuelve `json_validate_failed`, reintenta en modo libre.
- **Reintentos con backoff** ante 429/5xx/errores de conexión (antes: fallaba a la primera).
- **Parser tolerante**: saca fences de markdown y extrae el primer objeto `{...}` si
  hay texto alrededor.
- Si Groq falla, el mensaje del usuario **se saca del historial** (antes quedaba
  colgado y ensuciaba el contexto siguiente).
- Errores con mensaje amigable (`GroqError`) en vez del traceback crudo.

### Errores amigables en todos los handlers (`bot/handlers.py`)
Antes: `reply_text(f"❌ {e}")` mostraba excepciones crudas (posible filtración de
detalles internos). Ahora: mensajes en criollo para el usuario + log completo con
stacktrace del lado del servidor. Si el Markdown de una respuesta rompe, cae
automáticamente a texto plano en vez de fallar.

### Caché y conexión a Sheets (`sheets/client.py`)
- Tras un error, el caché de contexto **se invalida** (antes podía servir datos
  viejos después de una falla).
- Si el error parece de autenticación (401/expired/invalid_grant), se **resetea la
  conexión** para reconectar en el próximo llamado.
- Las credenciales de Google **nunca se loggean**: el error de credencial inválida
  es un `RuntimeError` limpio sin contenido.

### Saldos siempre frescos (`sheets/actions.py`)
Antes cada operación calculaba el saldo corriente sobre el caché (hasta 20s viejo):
dos gastos rápidos seguidos podían escribir un saldo desfasado. Ahora toda escritura
lee el estado fresco primero (`_data_fresca()`).

### Retry con backoff exponencial + jitter (`utils/helpers.py`)
`with_retry` pasó de 15/30/45s lineales a `min(10·2ⁿ + random(0,3), 60)`, que es lo
que recomienda Google para su API. También reintenta 500/502/503 y errores de
conexión, no solo 429.

### Cotización con memoria (`utils/helpers.py`)
`usd_rate()` cachea 1 hora y, si la API falla, devuelve la **última cotización
conocida** en vez de un 40.0 fijo que distorsionaba los totales en silencio.
El fallback duro solo aplica si nunca hubo una cotización real.

### Validación de configuración al arrancar (`config.py`, `main.py`)
`validate_config()` corre antes de armar la app: si falta `TELEGRAM_TOKEN`,
`GROQ_API_KEY`, `SPREADSHEET_ID`, `AUTHORIZED_USER_ID` o `GOOGLE_CREDENTIALS_JSON`
(o el JSON es inválido), el bot muere con un mensaje que dice exactamente qué falta.

### Constantes con nombre (`config.py`)
Se terminaron los magic numbers: `CACHE_TTL_SECONDS`, `HISTORY_LIMIT`,
`ULT_MOVS_LIMIT`, `GROQ_TIMEOUT`, `RETRY_BASE_SECONDS`, `RATE_LIMIT_MSGS`,
`USD_RATE_TTL`, `BUDGET_WARN_PCT`, etc. Todo configurable en un solo lugar.

### Type hints y docstrings
En todos los módulos tocados, en las funciones públicas y las complejas.

---

## Fase 3 — Mejoras de lo existente

### Prompt (`ai/prompt.py`)
- Sección MONTOS con ejemplos de "1k5", "1.000,50", "$300", "trescientos",
  "media luca", "un palo".
- Sección CONSULTA vs ACCIÓN explícita con verbos disparadores (evita registrar
  movimientos cuando el usuario solo pregunta).
- Ejemplos few-shot completos (gasto, consulta, múltiples pagos, cambio de divisa,
  meta, presupuesto).
- El contexto ahora incluye los últimos **20** movimientos (antes 10), más los
  presupuestos y metas vigentes.
- Regla de cambio de divisa: transferencias entre monedas distintas se modelan como
  dos acciones (gasto + ingreso) con ambos montos.

### Validación post-LLM (red de seguridad en `sheets/actions.py`)
Nunca se confía ciegamente en el JSON del modelo:
- `parse_amount()` valida todo monto: positivo, finito, entiende "1k5"/"1.000,50"/"$300".
  Montos negativos o basura → error claro, no se escribe nada.
- Cuentas: si después de normalizar no matchea una cuenta real, el bot lo dice y
  lista las válidas (antes escribía el nombre inventado en la planilla).
- La moneda la define **la cuenta** (BBVA USD siempre mueve USD), no lo que diga el LLM.
- Transferencias: origen ≠ destino y misma moneda (cambio de divisa → dos acciones).
- `eliminar`/`editar`: la fila tiene que ser un movimiento real (ya no se pueden
  borrar los encabezados con `fila: 2`).

### Normalización de cuentas (`utils/normalize.py`)
Reescrito: reconoce "bbva pesos", "itau dolares", "efectivo", "cash", "plata",
"billetera", "verdes", "u$s", "dls" y typos leves (matching difuso con `difflib`,
sin dependencias nuevas). Quita tildes y usa la moneda del mensaje como pista.

### Bug corregido: saldos rotos tras eliminar
`eliminar` borraba la fila pero **no recalculaba** la columna SALDO de las filas
posteriores (quedaban todas desfasadas). Ahora `eliminar` y `editar` comparten el
mismo recálculo completo.

### Comandos existentes
- `/saldo`: ahora muestra variación porcentual de los totales UYU y USD contra el
  cierre del mes anterior.
- `/resumen`: agrega el top 3 de categorías de gasto del mes (con 🥇🥈🥉, todo en UYU).
- `/limpiar`: pide confirmación con botones inline.
- `/reiniciar`: también pide confirmación (¡borraba todo sin preguntar!).

### Scheduler (`bot/scheduler.py`)
- Reporte del lunes: ahora arranca con el **resumen de la última semana**
  (ingresos/egresos, top de categorías, movimientos) + progreso de metas + el global.
- Alerta de saldo bajo: dice **cuánto te falta** para llegar al mínimo.
- Job diario de las 8:00: además registra la cotización del día (ver Fase 4).

---

## Fase 4 — Funcionalidades nuevas

### Presupuestos mensuales por categoría
- Natural: _"tope de 15k por mes para Alimentación"_ — o `/presupuesto Alimentación 15000`.
- `/presupuesto` sin argumentos: estado de todos con barra de progreso.
- `/presupuesto borrar Alimentación` (o monto 0) para eliminarlo.
- Al registrar un gasto, el bot avisa al superar el **80%** y al **pasarse** del tope.
- Los gastos en USD cuentan convertidos a UYU con la cotización del día.
- Se guardan en la pestaña nueva **Config** (columnas A-B).

### Metas de ahorro
- Natural: _"quiero ahorrar 500 USD para diciembre"_.
- El progreso se mide desde el total que tenías en esa moneda al crear la meta
  (columna BASE), así los movimientos previos no cuentan.
- `/metas` muestra el avance con barra de progreso; el reporte semanal lo incluye.
- Se guardan en la pestaña **Config** (columnas D-I). _"borrá la meta Viaje"_ la elimina.

### Historial de cotización USD/UYU
El job diario de las 8:00 guarda la cotización en la pestaña nueva **Cotización**
(una fila por día). Con eso podés graficar la tendencia directo en Sheets.

### Comandos nuevos
- **/mes** — detalle del mes: ingresos/egresos/balance por moneda, gastos por
  categoría comparados con el mes anterior, ritmo de gasto diario y proyección
  simple del total del mes.
- **/semana** — últimos 7 días: totales, top de categorías y lista de movimientos.
- **/exportar** — CSV del mes por Telegram (con BOM para que Excel abra bien las
  tildes). `/exportar todo` exporta el histórico completo.
- **/metas** y **/presupuesto** — ver arriba.

### Etiquetas
Los `#hashtags` que escribas se conservan en la descripción del movimiento
(el prompt instruye al LLM a no perderlos), así podés filtrarlos en Sheets o en el CSV.

### Descartado a propósito
- **Gráficos con matplotlib**: ~60 MB extra de imagen para Railway free tier; la
  pestaña Cotización + el CSV cubren la necesidad sin costo.
- **Detección de gastos recurrentes**: complejidad alta vs. valor para un usuario
  único; se puede sumar más adelante.
- **Proyección de saldo**: cubierta en versión simple dentro de `/mes`.

---

## Fase 5 — Seguridad y producción

- **`AUTHORIZED_USER_ID` en TODOS los handlers** vía el decorador `@authorized`
  (imposible olvidarlo en un handler nuevo); los intentos ajenos quedan loggeados.
- **Secret token en el webhook**: Telegram manda un header secreto en cada POST y
  PTB rechaza con 403 cualquier request que no lo traiga. Antes, cualquiera que
  descubriera la URL podía inyectar updates falsos.
- **La URL del webhook (que contiene el token) ya no se loggea.**
- **Credenciales de Google**: nunca se loggean; error limpio si el JSON es inválido.
- **Rate limiting local**: máx. 15 mensajes/minuto (`RATE_LIMIT_MSGS`); pasado eso
  el bot pide calma en vez de encolar llamadas a Groq/Sheets.
- **Graceful shutdown**: `run_webhook()` de PTB ya maneja SIGTERM en el container
  Linux de Railway; `post_shutdown` apaga el scheduler. Verificado, sin cambios extra.

---

## Archivos

| Archivo | Cambio |
|---|---|
| `config.py` | Reescrito: validación + constantes |
| `main.py` | Secret token, comandos nuevos, CallbackQueryHandler, validate_config |
| `kkaynbot/utils/helpers.py` | Reescrito: parse_amount, backoff exponencial, cotización con caché |
| `kkaynbot/utils/normalize.py` | Reescrito: variantes + fuzzy matching |
| `kkaynbot/sheets/client.py` | Invalidación de caché, reconexión, 20 movimientos |
| `kkaynbot/sheets/actions.py` | Reescrito: validaciones, saldos frescos, falla parcial de transferencia, recálculo tras eliminar, presupuestos/metas |
| `kkaynbot/sheets/config_tab.py` | **Nuevo**: pestañas Config y Cotización |
| `kkaynbot/sheets/setup.py` | `/setup` migra las pestañas nuevas |
| `kkaynbot/ai/prompt.py` | Reescrito: few-shot, montos, consulta vs acción |
| `kkaynbot/ai/groq.py` | Reescrito: JSON mode, retries, threads |
| `kkaynbot/bot/handlers.py` | Reescrito: decorador auth, rate limit, confirmaciones, comandos nuevos |
| `kkaynbot/bot/scheduler.py` | Reescrito: reporte semanal real, faltante en alertas, log de cotización |
| `kkaynbot/bot/reports.py` | **Nuevo**: /saldo+variación, /mes, /semana, /exportar, metas, presupuestos |
| `requirements.txt` | **Sin cambios** — cero dependencias nuevas |

Notas:
- `bot_legacy.py` quedó como estaba (código muerto, no se importa); se puede borrar
  cuando quieras, está en el historial de git.
- Si Railway se cae en el medio de una transferencia, el bot ahora **te avisa
  exactamente qué mitad quedó registrada** y cómo completarla (antes fallaba en silencio).
