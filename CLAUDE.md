# SecretarIA Amanecer — CLAUDE.md

Documentación técnica completa para futuros agentes. Leé todo antes de tocar código.

---

## Qué es esto

Bot de WhatsApp para **Clínica Abriness** (salud mental). Funciona como secretaria virtual: atiende pacientes, coordina turnos en Google Calendar y gestiona cobros por transferencia bancaria. Corre en Railway (Python/FastAPI). La interfaz con el usuario es 100% WhatsApp — no hay front-end web.

**Modelo de IA:** Claude Haiku 4-5 (`claude-haiku-4-5`) — se usa en todos los agentes. Elegido por velocidad y costo, no por capacidad.

---

## Stack

- **Python 3.11** + **FastAPI** + **Uvicorn**
- **PostgreSQL** (Railway) en producción / SQLite local para desarrollo
- **SQLAlchemy 2.0** (ORM, sin Alembic — migraciones manuales)
- **APScheduler** — jobs automáticos de fondo
- **Google Calendar API** — Service Account (sin OAuth interactivo)
- **Anthropic SDK** — Claude Haiku para todos los agentes
- **Brevo API** — envío de emails de confirmación
- **Mercado Pago API** — verificación de pagos (módulo en espera)
- **WhatsApp Cloud API (Meta)** — canal principal

---

## Estructura de archivos

```
main.py                          FastAPI entry point + APScheduler
database.py                      SQLAlchemy config (PostgreSQL / SQLite)
models.py                        Todas las tablas de la BD
init_db.py                       Seed inicial (empresa default, tablas)
reset_db.py                      Drop + recrear tablas (solo para testeo local)
requirements.txt
Procfile                         uvicorn main:app (Railway)

routes/
  whatsapp.py                    Webhook de Meta + router de estados

services/
  secretaria_principal.py        Agente Abby — primer contacto
  agendadora.py                  Agente de agenda (Google Calendar)
  cobranza.py                    Lógica de cobro + email
  mail_confirmacion.py           Envío de email HTML vía Brevo
  analista.py                    Extrae datos del paciente (llamado interno)
  analista_nocturno.py           Job 21:00 ARG — clasifica conversaciones
  seguimiento.py                 Job cada hora — envía remarketing
  confirmadora_pagos.py          Verifica pagos contra MP (módulo pausado)
  agendar_y_pagar.py             Flujo combinado turno+pago (módulo pausado)

prueba/
  main.py                        Test de Mercado Pago SDK
  app.py                         Webhook listener para pruebas locales
```

---

## Base de datos — tablas y campos clave

### `clientes`
El registro central de cada paciente/contacto.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | Primary key |
| `empresa_id` | FK | Multi-tenant (hoy siempre la empresa default) |
| `telefono` | String | Número WhatsApp (índice) |
| `estado_agente` | String | **El enrutador** — ver sección abajo |
| `mensajes_enviados` | Int | Contador anti-spam (límite: 20) |
| `datos_extraidos` | JSON | Memoria consolidada por el Analista |
| `nombre_completo` | String | Nombre del paciente |
| `dni` | String | DNI |
| `obra_social` | String | Nombre de obra social o "particular" |
| `numero_afiliado` | String | Número de afiliado |
| `fecha_nacimiento` | String | Fecha de nacimiento |
| `mail` | String | Email para confirmación |

### `mensajes`
Historial completo de cada conversación.

| Campo | Tipo | Descripción |
|---|---|---|
| `cliente_id` | FK | Pertenece a qué cliente |
| `rol` | String | `"usuario"` o `"asistente"` |
| `texto` | String | Contenido del mensaje |
| `fecha_creacion` | DateTime | UTC |

### `seguimientos`
Cola de mensajes de remarketing pendientes.

### `pagos`
Registro de pagos (monto, estado, id MP, detalle turno).

### `cola_analisis`
Set de clientes que el job nocturno debe procesar.

---

## El enrutador — `estado_agente`

Todo el sistema pivota alrededor de este campo en `clientes`. El webhook lee el estado y despacha el mensaje al agente correcto.

| Estado | Quién lo maneja | Qué hace |
|---|---|---|
| `principal` | `secretaria_principal.py` | Primer contacto, recolecta datos |
| `agendadora` | `agendadora.py` | Coordina turno en Google Calendar |
| `esperando_mail` | `cobranza.py` → `handler_esperando_mail` | Espera que el paciente mande su email |
| `manual` | Nadie (ignorado) | Walter atiende directamente |

Si `estado_agente` no matchea ninguno de estos → warning en consola, mensaje ignorado.

---

## Agentes activos

### Abby — `secretaria_principal.py`
- Primer contacto con el paciente
- Recolecta nombre, DNI, obra social, especialidad deseada
- Inyecta `datos_extraidos` en el prompt para que no olvide nada entre mensajes
- Herramientas disponibles: `registrar_paciente`, `iniciar_agendamiento`, `iniciar_cobranzas`, `notificar_walter_urgente`
- Historial: últimas 6 horas, máx 20 mensajes
- Llama al Analista antes de transferir para que el siguiente agente tenga contexto

### Agendadora — `agendadora.py`
- Solo coordina el turno, no atiende otras consultas
- Consulta disponibilidad real en Google Calendar antes de proponer horarios
- Loop de herramientas: máx 5 iteraciones por mensaje
- Crea el evento en Google Calendar cuando el paciente confirma
- Herramientas: `consultar_calendar`, `iniciar_cobranzas`, `volver_secretaria_principal`, `notificar_walter_urgente`

### Cobranza — `cobranza.py`
- No tiene IA propia, es lógica determinista
- Calcula tarifa según especialidad + cobertura
- Envía instrucciones de transferencia (alias + CVU)
- Notifica a Walter
- Si hay turno confirmado: busca email guardado o pide uno (`estado → esperando_mail`)

---

## Jobs automáticos

| Job | Horario | Qué hace |
|---|---|---|
| `analista_nocturno` | 21:00 ARG (00:00 UTC) | Clasifica conversaciones del día, crea seguimientos |
| `seguimiento` | Cada 1 hora | Envía mensajes de remarketing pendientes |

---

## Variables de entorno requeridas

```
DATABASE_URL                 PostgreSQL (Railway). Si falta, usa SQLite local.
WHATSAPP_TOKEN               Token de la API de WhatsApp Cloud (Meta)
PHONE_NUMBER_ID              ID del número de teléfono del bot en Meta
CLAUDE_API_KEY               API key de Anthropic
WEBHOOK_VERIFY_TOKEN         Token de verificación del webhook (default: "secretarIA")
GOOGLE_SERVICE_ACCOUNT       JSON completo de la Service Account de Google (como string)
CALENDAR_ID                  ID del Google Calendar a usar
BREVO_API_KEY                API key de Brevo para emails
MERCADOPAGO_ACCESS_TOKEN     Token de MP (módulo pausado, pero la variable debe existir)
```

---

## Flujo completo de un paciente nuevo

```
1. Paciente escribe a WhatsApp
   ↓
2. POST /webhook (Meta)
   ↓
3. Router lee estado_agente = "principal"
   ↓
4. secretaria_principal (Abby)
   - Saluda, pregunta motivo de consulta
   - Recolecta datos del paciente en turnos sucesivos
   - Guarda en datos_extraidos
   ↓
5. Paciente quiere turno → Abby llama iniciar_agendamiento
   - Analista extrae resumen de la conversación
   - estado_agente → "agendadora"
   ↓
6. secretaria_agendadora
   - Pregunta fecha/horario
   - Llama consultar_calendar → Google Calendar
   - Propone slots disponibles
   - Paciente elige → crea evento en Calendar
   - Llama iniciar_cobranzas
   ↓
7. iniciar_cobranzas (cobranza.py)
   - Calcula tarifa (especialidad + cobertura)
   - Envía mensaje con alias/CVU
   - Notifica a Walter
   - Si hay detalle_turno:
     · Si mail ya guardado → envía email de confirmación → estado = "manual"
     · Si no hay mail → pide email → estado = "esperando_mail"
   ↓
8a. estado = "esperando_mail"
   - Paciente manda email
   - Se valida con regex
   - Se guarda en BD
   - Se envía email HTML de confirmación
   - estado → "manual"
   ↓
8b. estado = "manual"
   - Walter atiende directamente
   - Bot ignora mensajes entrantes
```

---

## Tarifas hardcodeadas (cobranza.py)

```python
TARIFAS = {
    "psicologo":  {"particular": 30000, "obra social": 19000},
    "psiquiatra": {"particular": 80000, "obra social": 45000},
}
```

Datos de transferencia también hardcodeados en `PAGO_INFO` en `cobranza.py`. Cambiarlos ahí directamente.

---

## Google Calendar — cómo funciona

- Auth via **Service Account** (JSON en variable de entorno `GOOGLE_SERVICE_ACCOUNT`)
- El calendario tiene que tener permisos de escritura para el service account
- Slots disponibles: de 9 a 18 hs, bloques de 1 hora
- Máx 6 slots consultados, muestra los 4 primeros
- Formato de respuesta incluye `[ISO:fecha-iso]` para que Claude lo pase exacto a `iniciar_cobranzas`
- Si `CALENDAR_ID` no está definido, usa `"primary"` (el calendario principal de la cuenta)

---

## Emails — Brevo

- Servicio: Brevo (antes Sendinblue)
- Remitente: `abrinesclinica@gmail.com`
- Template HTML en `mail_confirmacion.py` — incluye desglose de pago, datos del paciente, detalle del turno
- Si el paciente tiene obra social, muestra precio de lista + descuento + total
- Si falla el envío, se loggea el error pero el flujo continúa (no bloquea)

---

## Comandos especiales (WhatsApp)

- `/borrarChat` — elimina el cliente de la BD. Sirve para testear desde cero. Responde con confirmación.

---

## Endpoints HTTP

| Método | Path | Qué hace |
|---|---|---|
| GET | `/webhook` | Verificación inicial de Meta |
| POST | `/webhook` | Mensajes entrantes de WhatsApp |
| GET | `/ver_clientes` | Lista todos los clientes con estado y datos extraídos |

> **No existe endpoint para ver conversaciones completas.** Ver sección de proyecciones.

---

## Consideraciones importantes

- **El mensaje de notificación a Walter es sagrado.** Está en `enviar_notificacion_a_walter()` en `secretaria_principal.py`. No cambiar el texto sin consultarle a Walter.
- **Anti-spam:** límite de 20 mensajes por cliente. Hardcodeado como `LIMITE_MENSAJES = 20` en `whatsapp.py`.
- **El analista nocturno no escribe al cliente.** Solo clasifica, crea seguimientos, limpia la cola.
- **Los módulos `confirmadora_pagos.py` y `agendar_y_pagar.py` están pausados (Sprint 2).** No están integrados al router.
- **`services/models.py` es un duplicado exacto de `models.py`** en la raíz. Hay que unificarlos en algún momento para evitar confusión.
- El historial de conversación que se pasa a Claude está en **UTC**, pero la lógica de fechas del calendar usa **America/Argentina/Buenos_Aires**. No mezclar.

---

## Multi-tenant (futuro)

La tabla `Empresa` existe y los clientes tienen `empresa_id`, pero hoy todo apunta a la empresa default `EMPRESA_DEFAULT_ID` del `init_db.py`. Para activar multi-tenant: (1) leer la empresa por `telefono_bot`, (2) pasar su `prompt_personalidad` al agente, (3) usar su config de pago.

---

## Proyecciones y lo que falta

### Funcionalidades pausadas (Sprint 2)
- **Confirmación automática de pagos por Mercado Pago** — `confirmadora_pagos.py` está escrito pero no conectado al router. Requiere activar el estado `esperando_pago` y el webhook de MP.
- **Flujo `agendar_y_pagar`** — `agendar_y_pagar.py` tiene stubs del Calendar. Pensado para combinar agendamiento + seña en un solo flow.

### Calidad y deuda técnica
- **Sin migraciones de BD (Alembic).** Hoy se hace `reset_db.py` o SQL manual. Con Alembic se podrían agregar columnas sin perder datos.
- **`services/models.py` duplicado.** Hay que borrar uno y dejar solo el de la raíz.
- **Prompts hardcodeados.** Los system prompts de Abby y la Agendadora están en el código Python. Deberían venir de la tabla `Empresa.prompt_personalidad` para permitir personalización por cliente sin tocar código.
- **Tarifas y datos de pago hardcodeados.** Deberían venir de la BD (`Empresa.monto_sena`, `Empresa.alias_pago`, etc.) — la estructura ya existe.

### UX y operaciones
- **Panel de conversaciones.** No existe forma de leer una conversación completa sin acceder directamente a la BD. Falta un endpoint `GET /conversacion/{telefono}` que devuelva los mensajes ordenados.
- **Panel de administración básico.** Ver clientes, sus estados, sus datos y sus pagos sin tocar Postgres directamente.
- **Endpoint para cambiar `estado_agente` manualmente.** Hoy, si Walter quiere devolver un cliente de "manual" a "principal", tiene que hacerlo en la BD.
- **Dashboard de métricas.** Conversaciones iniciadas, turnos agendados, tasas de conversión, mensajes de seguimiento enviados.

### Robustez
- **Reintentos en envío de WhatsApp.** Hoy si `enviar_mensaje_wpp` falla, el mensaje se pierde sin aviso.
- **Rate limiting real.** El límite de 20 mensajes por cliente no se resetea nunca. Habría que agregar un campo `fecha_primer_mensaje` o resetear en ventanas de tiempo.
- **Logs estructurados.** Hoy se mezclan `logging.warning()` y `print()` indiscriminadamente. Unificar con un logger configurado y niveles correctos.
- **Tests.** No hay ningún test automatizado. Mínimo: test de `_parse_fecha_hora`, `_calcular_tarifa`, y el router de estados.

### Escalabilidad
- **Multi-tenant real activado.** El modelo de datos ya lo soporta, falta conectarlo (ver sección multi-tenant arriba).
- **Queue de mensajes.** Hoy se usa `BackgroundTasks` de FastAPI (en proceso). Con carga alta habría que mover a Celery o similar.
- **Seguimientos personalizados.** El job de seguimiento hoy manda "¿Quedó alguna duda?" a todos. Debería usar el resumen del analista para personalizar el mensaje según el contexto de cada conversación.
