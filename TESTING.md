# TESTING — KkaynBot v6

Guía de pruebas manuales por Telegram. Orden recomendado: de arriba hacia abajo.
Antes de todo, deployá en Railway y mirá los logs del arranque.

## 0. Arranque y migración

| Prueba | Esperado |
|---|---|
| Deploy con todas las env vars | Log `🤖 KkaynBot v6!` y `Webhook configurado exitosamente` |
| Deploy borrando `GROQ_API_KEY` (probar y restaurar) | El container muere al toque con `❌ Configuración incompleta... GROQ_API_KEY` |
| `/setup` | Crea las pestañas **Config** y **Cotización** sin tocar las 4 existentes; responde qué creó |
| `/start` | Muestra la ayuda con los comandos nuevos |

## 1. Registro con montos raros (parsing)

| Mensaje | Esperado |
|---|---|
| `gasté 1k5 en el súper con itau` | Gasto $ 1.500 en Itaú UYU, categoría Alimentación |
| `pagué 1.000,50 de UTE con BBVA` | Gasto $ 1.000,50 |
| `gasté $300 en farmacia en efectivo` | Gasto $ 300 en Efectivo UYU |
| `gasté trescientos en taxi con bbva` | Gasto $ 300 |
| `cobré 2,5k en efectivo` | Ingreso $ 2.500 |
| `gasté -500 con bbva` | Lo registra como gasto de 500 o pide aclaración — nunca escribe un negativo |
| `gasté asdfgh pesos` | Pregunta el monto, no registra nada |

## 2. Normalización de cuentas

| Mensaje | Esperado |
|---|---|
| `gasté 100 con bbva pesos` | BBVA UYU |
| `gasté 20 dólares con itau` | Itaú USD |
| `gasté 50 en efectivo` / `...en cash` / `...de la billetera` | Efectivo UYU |
| `gasté 10 verdes de la billetera` | Efectivo USD |
| `gasté 100 con efectvo` (typo) | Efectivo UYU (fuzzy) |
| `gasté 100 con brou` | Error claro listando las cuentas válidas; NO escribe en la planilla |

## 3. Consulta vs acción

| Mensaje | Esperado |
|---|---|
| `¿cuánto tengo en bbva?` | Responde el saldo, NO registra nada |
| `¿cuánto gasté este mes en comida?` | Responde con datos, NO registra |
| `en bbva tengo 5000` | `actualizar_saldo` con ajuste a $ 5.000 |
| `el sueldo fueron 48k no 50k` | Edita el movimiento del sueldo (verificar que el saldo se recalcula en toda la columna H) |
| `borrá el último` | Elimina y los saldos de las filas siguientes quedan bien (¡bug corregido!) |

## 4. Transferencias

| Mensaje | Esperado |
|---|---|
| `pasé 10k de BBVA a Itaú` | Dos filas (egreso + ingreso), saldos correctos |
| `pasá 100 de bbva a bbva` | Error: misma cuenta |
| `pasá 100 de BBVA UYU a BBVA USD` | Error explicando que son monedas distintas + cómo hacerlo |
| `compré 100 dólares a 41 con plata del bbva` | DOS acciones: gasto $ 4.100 en BBVA UYU + ingreso U$S 100 en BBVA USD |

## 5. Presupuestos

```
/presupuesto                      → mensaje "no tenés presupuestos"
tope de 2000 por mes para Ocio    → confirma presupuesto
/presupuesto Alimentación 15000   → confirma
/presupuesto                      → lista con barras de progreso
gasté 1700 en el cine con bbva    → registra y avisa ⚠️ 85% del presupuesto de Ocio
gasté 500 en juegos con bbva      → registra y avisa 🚨 que te pasaste
/presupuesto borrar Ocio          → lo elimina
```
Verificar en Sheets: pestaña Config, columnas A-B.

## 6. Metas

```
quiero ahorrar 500 dólares para diciembre  → crea la meta (base = tu total USD actual)
/metas                                     → barra en 0%
cobré 100 dólares en bbva                  → (simula ahorro)
/metas                                     → ~20% de progreso
borrá la meta de ahorro                    → la elimina
```
Verificar en Sheets: pestaña Config, columnas D-I.

## 7. Reportes

| Comando | Verificar |
|---|---|
| `/saldo` | Saldos + línea `Total UYU ... 📈 +x% vs fin del mes pasado` |
| `/resumen` | Incluye `🏷 Top gastos del mes` con 🥇🥈🥉 |
| `/mes` | Categorías con "(mes pasado: $X)", ritmo diario y proyección |
| `/semana` | Totales 7 días + top categorías + lista de movimientos |
| `/exportar` | Llega un `.csv` del mes; abrirlo en Excel: tildes OK |
| `/exportar todo` | CSV con el histórico completo |

## 8. Confirmaciones

| Comando | Esperado |
|---|---|
| `/limpiar` → Cancelar | "Cancelado", el historial sigue |
| `/limpiar` → Sí | Historial de conversación limpio (la planilla intacta) |
| `/reiniciar` → Cancelar | No borra nada |
| `/reiniciar` → Sí, borrar TODO | Borra los registros (⚠️ probar al final o en una copia de la planilla) |

Nota: tras `/reiniciar`, las metas quedan con BASE vieja — redefinilas.

## 9. Robustez (simulacros)

| Prueba | Esperado |
|---|---|
| Mandar 16+ mensajes en un minuto | A partir del 16: "⏳ Pará un toque..." sin llamar a Groq |
| Poner una `GROQ_API_KEY` inválida y mandar un mensaje (probar y restaurar) | "El cerebro del bot (Groq) no está respondiendo..." — sin traceback |
| Quitarle permisos al service account en la planilla (probar y restaurar) | Mensajes de error amigables; al restaurar, se reconecta solo |
| Mandar un mensaje con `*` y `_` sueltos | Responde igual (fallback a texto plano si el Markdown rompe) |
| Escribir desde otra cuenta de Telegram | Silencio total + `Acceso denegado` en los logs |
| Dos gastos seguidos rápido (< 20 s) | El segundo saldo sale bien (lee estado fresco, no caché) |

## 10. Scheduler (esperar o adelantar los cron en main.py para probar)

| Job | Verificar |
|---|---|
| Lunes 9:00 | Reporte con resumen de la semana + metas + global |
| Diario 8:00 | Fila nueva en la pestaña Cotización; si algún saldo < mínimo, alerta con el faltante exacto |

Para probarlos sin esperar: cambiar temporalmente los `add_job` de `main.py`
a `sch.add_job(weekly_report, "interval", minutes=2, args=[app])`, deployar,
verificar y revertir.

## 11. Seguridad del webhook

Con el bot corriendo en Railway:
```bash
curl -s -o /dev/null -w "%{http_code}" -X POST https://TU_APP.railway.app/TU_TOKEN \
  -H "Content-Type: application/json" -d '{"update_id":1}'
```
Esperado: **403** (sin el header `X-Telegram-Bot-Api-Secret-Token` correcto, se rechaza).
Los mensajes reales por Telegram tienen que seguir andando.
