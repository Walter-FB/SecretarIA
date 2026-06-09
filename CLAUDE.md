# SecretarIA Amanecer — CLAUDE.md

Documentación técnica completa para futuros agentes. Leé todo antes de tocar código.

---

## REGLAS PARA EL AGENTE — leer primero

- **`agents/secretaria_principal.py` — NO modificar el prompt ni la lógica sin que Walter lo pida explícitamente.** Si detectás algo mejorable, mencionalo como sugerencia y esperá aprobación. El prompt de Abby es responsabilidad de Walter.

- **Alembic — el agente se encarga de los deploys y de las migraciones. Flujo obligatorio antes de cada push:**
  1. Si se modificó `models.py`, generar la migración con `alembic revision --autogenerate -m "descripcion"` (nunca escribir el archivo a mano).
  2. Probar localmente con `alembic upgrade head` contra la BD local antes de pushear.
  3. Verificar que `alembic current` muestre la revisión esperada.
  4. Solo después hacer `git push`. Railway aplica `alembic upgrade head` automáticamente en el releaseCommand — si el upgrade local funcionó, el de Railway también funciona.
  - **Nunca crear columnas en `models.py` sin su migración correspondiente.**
  - **Nunca crear la migración a mano** — siempre `--autogenerate` para que Alembic detecte el diff real entre modelo y BD.

---

## Qué es esto

Bot de WhatsApp para **Clínica Abriness** (salud mental). Funciona como secretaria virtual: atiende pacientes, coordina turnos en Google Calendar y gestiona cobros por transferencia bancaria. Corre en Railway (Python/FastAPI). La interfaz con el usuario es 100% WhatsApp — no hay front-end web.

**Modelo de IA:** Claude Haiku 4-5 (`claude-haiku-4-5`) — se usa en todos los agentes. Elegido por velocidad y costo, no por capacidad.

---

## Stack

- **Python 3.11** + **FastAPI** + **Uvicorn**
- **PostgreSQL** (Railway, driver pg8000) en producción / SQLite local para desarrollo
- **SQLAlchemy 2.0** (ORM)
- **Alembic** — migraciones activas. `releaseCommand = "alembic upgrade head"` en `railway.toml`
- **APScheduler** — jobs automáticos de fondo
- **Google Calendar API** — Service Account (sin OAuth interactivo)
- **Anthropic SDK** — Claude Haiku para todos los agentes
- **Brevo API** — envío de emails de confirmación
- **Mercado Pago API** — verificación de pagos (módulo en espera)
- **WhatsApp Cloud API (Meta)** — canal principal

---

## Estructura de archivos

```
main.py                          FastAPI entry point + APScheduler + seeds de BD
database.py                      SQLAlchemy config (PostgreSQL / SQLite)
models.py                        Todas las tablas de la BD
init_db.py                       Seed inicial: seed_empresa_default(), seed_profesionales(), seed_abriness_multitenant()
reset_db.py                      Drop + recrear tablas (solo para testeo local SQLite)
empresa_scope.py                 Clase EmpresaScope — guardrail multi-tenant para queries
requirements.txt
Procfile                         uvicorn main:app (Railway)
railway.toml                     releaseCommand = "alembic upgrade head"

migrations/
  env.py                         Configuración Alembic
  versions/
    e085c6052f22_initial.py      Tablas originales + Profesional + profesional_id en clientes
    4faaa4399732_multi_tenant.py Campos multi-tenant en empresas, bot_activo en clientes, empresa_id en mensajes

tools/
  __init__.py
  registry.py                    Catálogo central: TOOL_CATALOG, get_tools_for_empresa(), get_tools_for_agendadora()
  registrar_paciente.py          DEFINITION + handler
  verificar_paciente_existente.py DEFINITION + handler
  iniciar_agendamiento.py        DEFINITION + handler
  iniciar_cobranzas.py           DEFINITION (agendadora) + DEFINITION_PRECIO (principal) + dos handlers
  notificar_walter_urgente.py    DEFINITION + handler
  consultar_calendar.py          DEFINITION + handler
  volver_secretaria_principal.py DEFINITION + handler

routes/
  whatsapp.py                    Webhook de Meta + router de estados + comandos Walter
  admin.py                       Panel de administración — autenticado por Bearer token

static/
  admin.html                     Panel web single-page (vanilla JS, sin frameworks)

agents/
  __init__.py
  herramientas_secretarias.py    Helpers WPP (enviar/marcar leído), client_claude, NUMERO_WALTER
  secretaria_principal.py        Agente Abby — primer contacto, loop de tools vía catálogo
  agendadora.py                  Agente de agenda (Google Calendar), loop de tools vía catálogo
  analista.py                    Extrae resumen_situacion al transferir agentes
  analista_nocturno.py           Job 21:00 ARG — clasifica conversaciones

services/
  cobranza.py                    Lógica de cobro + email, lee alias/CVU de Empresa
  profesionales.py               Helpers CRUD para tabla Profesional (scopeados por empresa_id)
  mail_confirmacion.py           Envío de email HTML vía Brevo
  seguimiento.py                 Job cada hora — envía remarketing

prueba/
  main.py                        Test de Mercado Pago SDK
  app.py                         Webhook listener para pruebas locales
```

---

## Base de datos — tablas y campos clave

### `empresas`
Cada empresa es un tenant independiente.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | String | UUID (default: `secretaria-enterprise`) |
| `nombre` | String | Nombre de la clínica |
| `telefono_bot` | String | Número del bot en WhatsApp |
| `phone_number_id` | String | **UNIQUE** — ID de Meta que llega en cada webhook. Usado para identificar el tenant |
| `bot_activo` | Boolean | Si False, el bot ignora todos los mensajes |
| `numero_walter` | String | Número de WhatsApp del admin para notificaciones y comandos /mute |
| `prompt_personalidad` | String | System prompt de Abby. Si len > 200, reemplaza al default hardcodeado |
| `tools_habilitadas` | JSON | Lista de tools permitidas (null = todas). Ej: `["registrar_paciente", "iniciar_agendamiento"]` |
| `calendar_id` | String | Google Calendar ID. Null = usa `CALENDAR_ID` env var |
| `alias_pago` | String | Alias bancario para transferencias |
| `cvu_pago` | String | CVU para transferencias |
| `webhook_verify_token` | String | Token de verificación del webhook Meta (null = usa env var) |

### `clientes`
El registro central de cada paciente/contacto.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | Primary key |
| `empresa_id` | FK → empresas | Multi-tenant |
| `telefono` | String | Número WhatsApp |
| `estado_agente` | String | **El enrutador** — ver sección abajo |
| `bot_activo` | Boolean | Si False, el bot ignora mensajes de este número |
| `mensajes_enviados` | Int | Contador anti-spam (límite: 20) |
| `datos_extraidos` | JSON | Memoria del analista (`resumen_situacion`, `estado_charla`) |
| `nombre_completo` | String | Nombre del paciente |
| `dni` | String | DNI |
| `obra_social` | String | Nombre de obra social o "particular" |
| `numero_afiliado` | String | Número de afiliado |
| `fecha_nacimiento` | String | Fecha de nacimiento |
| `mail` | String | Email para confirmación |
| `profesional_id` | FK → profesionales | Profesional asignado/habitual |

### `profesionales`
Profesionales de la clínica. Creados por `seed_profesionales()` en startup.

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | UUID | Primary key |
| `empresa_id` | FK → empresas | Pertenece a una empresa |
| `nombre` | String | Ej: "Lic. Renals", "Dr. Barros" |
| `especialidad` | String | `"psicologo"` o `"psiquiatra"` |
| `tarifa_particular` | Integer | Ej: 30000 |
| `tarifa_obra_social` | Integer | Ej: 19000 |
| `calendar_id` | String | Null = usa el calendar_id de la empresa |
| `activo` | Boolean | Si False, no se agenda |

Seed actual (Abriness):
- **Lic. Renals** — psicologo — particular: 30000 — obra social: 19000
- **Dr. Barros** — psiquiatra — particular: 80000 — obra social: 45000

### `mensajes`
| Campo | Tipo | Descripción |
|---|---|---|
| `cliente_id` | FK → clientes | |
| `empresa_id` | FK → empresas | Para queries multi-tenant |
| `rol` | String | `"usuario"` o `"asistente"` |
| `texto` | String | Contenido |
| `fecha_creacion` | DateTime | UTC |

### `seguimientos`, `pagos`, `cola_analisis`
Sin cambios respecto a la versión original.

---

## El enrutador — `estado_agente`

| Estado | Quién lo maneja | Qué hace |
|---|---|---|
| `principal` | `secretaria_principal.py` | Primer contacto, recolecta datos |
| `agendadora` | `agendadora.py` | Coordina turno en Google Calendar |
| `esperando_mail` | `cobranza.py → handler_esperando_mail` | Espera que el paciente mande su email |
| `manual` | Nadie (ignorado) | Walter atiende directamente |

Si `estado_agente` no matchea ninguno de estos → warning en consola, mensaje ignorado.

---

## Catálogo de tools — `tools/`

Cada tool es un módulo independiente con dos exports:
- `DEFINITION` — dict con `name`, `description`, `input_schema` para pasarle a Claude
- `async def handler(tool_input, cliente, session, empresa, scope=None) → (str, str | None)` — ejecuta la tool, devuelve `(resultado, nuevo_estado_agente | None)`

`iniciar_cobranzas` es la excepción: tiene dos variantes:
- `DEFINITION` + `handler` — contexto agendadora (tiene `dia`, `hora`, `profesional`, `iso_datetime`; crea el evento en Calendar + llama cobranza)
- `DEFINITION_PRECIO` + `handler_precio` — contexto principal (solo `especialidad` + `cobertura`; solo llama cobranza sin tocar Calendar)

### `tools/registry.py`

```python
get_tools_for_empresa(empresa)    # Secretaria principal — usa DEFINITION_PRECIO para iniciar_cobranzas
get_tools_for_agendadora(empresa) # Subconjunto fijo: consultar_calendar, iniciar_cobranzas, volver_secretaria_principal, notificar_walter_urgente
```

Ambas devuelven `(definitions, handlers)`:
- `definitions` → lista de dicts para el parámetro `tools` de Claude
- `handlers` → dict `nombre → función async`

**Las tools NO modifican `cliente.estado_agente`.** Solo retornan el nuevo estado. El service hace el commit:
```python
resultado, derivar = await handler_fn(tool.input, cliente, db, empresa)
if derivar:
    cliente.estado_agente = derivar
    db.commit()
```

---

## Multi-tenant — activo

El webhook extrae `phone_number_id` del payload de Meta y busca la empresa correspondiente:

```python
phone_number_id = entry.get("metadata", {}).get("phone_number_id", "")
empresa = EmpresaScope.empresa_por_phone_number_id(phone_number_id, db)
# Si no encuentra → fallback a EMPRESA_DEFAULT_ID
```

`EmpresaScope` (en `empresa_scope.py`, raíz del proyecto) provee queries scopeadas a `empresa_id`:
- `scope.clientes()` — todos los clientes del tenant
- `scope.cliente_por_telefono(tel)` — busca en el scope correcto
- `scope.mensajes_de_cliente(cliente_id)` — mensajes del tenant

Todos los services reciben `empresa_id: str` y lo usan para filtrar clientes y mensajes.

### Comandos de WhatsApp para Walter

Enviados **desde el número `empresa.numero_walter`** (cualquier otro número los ignora):

| Comando | Efecto |
|---|---|
| `/mute <numero>` | `cliente.bot_activo = False` — bot silenciado, mensajes se guardan en historial pero no se procesan |
| `/unmute <numero>` | `cliente.bot_activo = True` — bot reactivado |
| `/estado <numero>` | Responde con `estado_agente`, `bot_activo`, `nombre_completo` del cliente |
| `/ayuda` | Lista los comandos disponibles |
| `/cualquier_otra_cosa` | Responde "Comando no reconocido. Mandá /ayuda para ver los disponibles." |

El número se normaliza antes de buscar (se ignoran espacios, guiones, código de país — solo dígitos). Implementado en `_buscar_cliente_normalizado()` en `routes/whatsapp.py`.

**Mute automático:** `cliente.bot_activo` también se pone en `False` automáticamente cuando:
- `notificar_walter_urgente` escala una emergencia (estado → `manual`)

**Email fallido (3 intentos):** el bot NO se silencia. Después de 3 intentos inválidos, el estado vuelve a `"principal"` (bot sigue activo), se envía un mensaje de cierre al paciente y se notifica a Walter.

Walter puede usar `/unmute` para reactivar el bot en cualquiera de esos casos.

### Para agregar una nueva empresa

1. Insertar en `empresas` con `phone_number_id` (el de Meta), `bot_activo=True`, `numero_walter`, `alias_pago`, `cvu_pago`
2. Eso es todo. El webhook la encuentra automáticamente.

---

## Agentes activos

### Abby — `secretaria_principal.py`
- Primer contacto con el paciente
- Tools cargadas desde `get_tools_for_empresa(empresa)` — respeta `empresa.tools_habilitadas`
- Loop de herramientas: máx 4 iteraciones por mensaje
- Inyecta `datos_extraidos` + columnas directas del cliente en el prompt
- Paciente recurrente: pide DNI → `verificar_paciente_existente` copia datos de otro registro

### Agendadora — `agendadora.py`
- Tools cargadas desde `get_tools_for_agendadora(empresa)` — subconjunto fijo
- Loop de herramientas: máx 5 iteraciones por mensaje
- `consultar_calendar` → Google Calendar → propone slots con `[ISO:...]`
- `iniciar_cobranzas` → crea evento en Calendar + pasa a cobranza
- Puede volver a principal (`volver_secretaria_principal`)

### Cobranza — `cobranza.py`
- No tiene IA propia, es lógica determinista
- Lee `alias_pago` y `cvu_pago` de la empresa (fallback a `PAGO_INFO` hardcodeado si la empresa no los tiene)
- Calcula tarifa desde la tabla `profesionales` (vía `services/profesionales.py`), scopeado por `empresa_id`
- Si hay turno: pide email si no lo tiene (`estado → esperando_mail`), envía email HTML si lo tiene (`estado → manual`)

**`handler_esperando_mail` — retry counter:**
- Email válido → guarda, envía email, `estado → principal`
- Email inválido → incrementa `datos_extraidos["intentos_email"]`
- Después de 3 intentos fallidos → `estado → principal` (bot sigue activo), notifica a Walter, mensaje de cierre al paciente

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
ADMIN_TOKEN                  Token para el panel web /admin (elegí uno largo, solo vos lo sabés)
MERCADOPAGO_ACCESS_TOKEN     Token de MP (módulo pausado, pero la variable debe existir)
```

---

## Flujo completo de un paciente nuevo

```
1. Paciente escribe a WhatsApp
   ↓
2. POST /webhook → extrae phone_number_id → busca empresa
   - Verifica empresa.bot_activo
   - Verifica cliente.bot_activo
   ↓
3. Router lee estado_agente = "principal"
   ↓
4. secretaria_principal (Abby)
   - Saluda, pregunta motivo de consulta
   - Recolecta datos del paciente en turnos sucesivos
   - Guarda con registrar_paciente (columnas directas en BD)
   ↓
5. Paciente quiere turno → Abby llama iniciar_agendamiento
   - Analista extrae resumen de la conversación
   - estado_agente → "agendadora"
   - secretaria_agendadora se invoca inmediatamente
   ↓
6. secretaria_agendadora
   - Pregunta fecha/horario
   - Llama consultar_calendar → Google Calendar
   - Propone slots disponibles con [ISO:...]
   - Paciente elige → iniciar_cobranzas → crea evento en Calendar
   ↓
7. iniciar_cobranzas (cobranza.py)
   - Calcula tarifa (especialidad + cobertura)
   - Envía mensaje con alias/CVU de la empresa
   - Notifica a Walter
   - Si hay detalle_turno:
     · Si mail ya guardado → envía email HTML → estado = "manual"
     · Si no hay mail → pide email → estado = "esperando_mail"
   ↓
8a. estado = "esperando_mail"
   - Paciente manda email → se valida con regex → se guarda
   - Envío de email HTML de confirmación → estado → "manual"
   ↓
8b. estado = "manual" → Walter atiende directamente
```

---

## Google Calendar — cómo funciona

- Auth via **Service Account** (JSON en `GOOGLE_SERVICE_ACCOUNT`)
- Slots: de 9 a 18 hs, bloques de 1 hora, máx 4 mostrados
- Respuesta incluye `[ISO:fecha-iso]` → Claude lo copia exacto a `iniciar_cobranzas`
- `calendar_id`: primero `empresa.calendar_id`, si null `os.getenv("CALENDAR_ID", "primary")`

---

## Emails — Brevo

- Servicio: Brevo (antes Sendinblue)
- Remitente: `abrinesclinica@gmail.com`
- Template HTML en `mail_confirmacion.py` — desglose de pago, datos del paciente, detalle del turno
- Si falla, se loggea pero el flujo continúa (no bloquea)

---

## Comandos especiales (WhatsApp)

- `/borrarChat` — elimina el cliente de la BD. Para testear desde cero. Disponible para cualquier número.
- Comandos de Walter (solo desde `empresa.numero_walter`): `/mute`, `/unmute`, `/estado`, `/ayuda` — ver sección multi-tenant.

---

## Endpoints HTTP

| Método | Path | Auth | Qué hace |
|---|---|---|---|
| GET | `/webhook` | no | Verificación inicial de Meta |
| POST | `/webhook` | no | Mensajes entrantes de WhatsApp |
| GET | `/ver_clientes` | no | Lista todos los clientes (sin filtro empresa) |
| GET | `/conversacion/{telefono}` | no | Conversación completa de un cliente |
| GET | `/admin` | no | Panel web de administración (HTML) |
| GET | `/admin/empresas` | Bearer | Lista empresas con conteo de clientes |
| GET | `/admin/clientes?empresa_id=` | Bearer | Clientes de una empresa: estado, bot, último msg |
| GET | `/admin/conversacion/{tel}?empresa_id=` | Bearer | Mensajes de un cliente scopeados por empresa |
| POST | `/admin/mute` | Bearer | `{telefono, empresa_id}` → `bot_activo = False` |
| POST | `/admin/unmute` | Bearer | `{telefono, empresa_id}` → `bot_activo = True` |

### Panel de administración — `/admin`

Interfaz web single-page para administrar sin tocar la base de datos.

**Auth:** Variable de entorno `ADMIN_TOKEN`. El token se ingresa en el panel web al abrir `/admin` — se guarda en memoria JS (no en localStorage, se borra al cerrar la pestaña). Se valida con `secrets.compare_digest()` en cada request.

Si `ADMIN_TOKEN` no está configurado al arrancar, Railway loggeará:
```
[⚠️ ADMIN] ADMIN_TOKEN no configurado — el panel /admin no va a responder
```

**Configurar en Railway:** Variables de entorno → `ADMIN_TOKEN` → cualquier string largo (30+ chars). Solo vos lo sabés, no hace falta decirme cuál es.

---

## Consideraciones importantes

- **El mensaje de notificación a Walter es sagrado.** Está en `enviar_notificacion_a_walter()` en `agents/herramientas_secretarias.py`. No cambiar el texto sin consultarle a Walter.
- **Anti-spam:** límite de 20 mensajes por cliente. Hardcodeado como `LIMITE_MENSAJES = 20` en `whatsapp.py`.
- **El analista nocturno no escribe al cliente.** Solo clasifica, crea seguimientos, limpia la cola.
- **Los módulos `confirmadora_pagos.py` y `agendar_y_pagar.py` están pausados.** No conectados al router.
- **`services/models.py` es un duplicado de `models.py`** en la raíz. Pendiente unificar.
- El historial de conversación que se pasa a Claude está en **UTC**, pero la lógica del calendar usa **America/Argentina/Buenos_Aires**. No mezclar.
- **SQLite local no tiene las migraciones Alembic aplicadas.** Si se agrega una columna, hay que correr `alembic upgrade head` o hacer `reset_db.py`. En Railway se aplica automáticamente en cada deploy.
- **phone_number_id en Railway:** ejecutar `UPDATE empresas SET phone_number_id = 'TU_ID' WHERE id = 'secretaria-enterprise'` con el ID real de Meta. Hasta que se haga, el sistema funciona con el fallback a EMPRESA_DEFAULT_ID.

---

## Testing — cómo verificar cada parte

### 1. Sanity check de imports (local, sin Railway)

```bash
python -c "from tools.registry import get_tools_for_empresa, get_tools_for_agendadora; print('OK')"
python -c "import agents.secretaria_principal; import agents.agendadora; print('OK')"
python -c "from agents.herramientas_secretarias import enviar_mensaje_wpp, client_claude; print('OK')"
python -c "from empresa_scope import EmpresaScope; print('OK')"
```

### 2. Verificar el catálogo de tools

```python
from tools.registry import get_tools_for_empresa, get_tools_for_agendadora
from tools import iniciar_cobranzas

defs_p, hdlrs_p = get_tools_for_empresa(None)
defs_a, hdlrs_a = get_tools_for_agendadora()

# Principal: iniciar_cobranzas usa handler_precio
assert hdlrs_p['iniciar_cobranzas'] is iniciar_cobranzas.handler_precio
# Agendadora: iniciar_cobranzas usa handler (con Calendar)
assert hdlrs_a['iniciar_cobranzas'] is iniciar_cobranzas.handler
# Agendadora NO tiene iniciar_agendamiento
assert 'iniciar_agendamiento' not in hdlrs_a
print("Catálogo OK")
```

### 3. Verificar tools_habilitadas (filtrado por empresa)

```python
from tools.registry import get_tools_for_empresa

class FakeEmpresa:
    tools_habilitadas = ["registrar_paciente", "iniciar_agendamiento", "notificar_walter_urgente"]

defs, hdlrs = get_tools_for_empresa(FakeEmpresa())
assert len(defs) == 3
assert "iniciar_cobranzas" not in hdlrs  # fue filtrada
print("Filtrado por empresa OK")
```

### 4. Verificar parse de fechas del calendar

```bash
python -c "
from agents.agendadora import _parse_fecha_hora
start, end = _parse_fecha_hora('mañana a las 15')
print(start, end)
start2, end2 = _parse_fecha_hora('lunes 16 de junio 10hs')
print(start2, end2)
"
```

### 5. Test de flujo completo via WhatsApp real

Mandar desde el número de prueba (con `/borrarChat` para limpiar estado previo):

**Flujo paciente nuevo:**
1. Cualquier mensaje → Abby saluda
2. "Quiero turno de psicología" → pregunta si es primera vez
3. "Sí" → pide datos
4. [Dar nombre, DNI, OSDE, nro afiliado, fecha nac] → llama `registrar_paciente`
5. → llama `iniciar_agendamiento` → estado = "agendadora"
6. "Mañana a las 10" → llama `consultar_calendar` → propone slots
7. "El de las 10" → llama `iniciar_cobranzas` → crea evento + envía instrucciones de pago

**Flujo paciente recurrente:**
1. `/borrarChat` → limpiar
2. "Hola, quiero turno" → Abby saluda
3. "No es mi primera vez" → pide DNI
4. [DNI de un paciente ya registrado] → `verificar_paciente_existente` → confirma nombre
5. → `iniciar_agendamiento` sin necesidad de pedir datos de nuevo

**Emergencia:**
1. "Estoy muy mal, no puedo más" → `notificar_walter_urgente` → Walter recibe mensaje

**Comandos Walter (desde numero_walter):**
1. `/mute +549XXXXXXXX` → bot silenciado, mensajes siguientes del cliente se guardan en historial pero no procesan
2. `/unmute +549XXXXXXXX` → bot reactivado
3. `/estado +549XXXXXXXX` → bot responde estado del cliente
4. `/ayuda` → lista de comandos
5. `/cualquiercosa` → "Comando no reconocido"

**Flujo esperando_mail con errores:**
1. Llegar al estado `esperando_mail` (completar turno sin email previo)
2. Mandar 3 textos que no son emails → bot responde con aviso y mensaje final de derivación
3. Verificar que Walter recibió notificación y el cliente volvió a estado `principal`

### 6. Verificar multi-tenant en Railway

```sql
-- Ver empresa y su phone_number_id configurado:
SELECT id, nombre, phone_number_id, bot_activo, numero_walter FROM empresas;

-- Ver clientes con empresa_id:
SELECT telefono, estado_agente, empresa_id FROM clientes LIMIT 10;

-- Ver mensajes con empresa_id:
SELECT c.telefono, m.rol, m.texto, m.empresa_id FROM mensajes m
JOIN clientes c ON m.cliente_id = c.id
ORDER BY m.fecha_creacion DESC LIMIT 20;
```

### 7. Verificar Alembic en Railway

En los logs de Railway, el release step debe mostrar:
```
Running migrations...
alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade ... -> 4faaa4399732
```

Si hay error de "column already exists", la migración ya fue aplicada — es idempotente en el sentido de que no se puede re-aplicar, pero Alembic trackea cuáles ya corrieron.

### 8. Verificar panel de administración

1. Ir a `https://tu-app.railway.app/admin` — debe cargar el panel HTML sin pedir auth
2. Pegar el `ADMIN_TOKEN` en el campo → "Conectar" → debe aparecer la lista de empresas
3. Seleccionar Abriness → tabla de clientes con estado, bot y último mensaje
4. Click "Ver" en un cliente → panel lateral con los mensajes en formato chat
5. Click "Mute" → el bot deja de procesar mensajes del cliente (pero los guarda)
6. Click "Unmute" → vuelve a procesar
7. Verificar seguridad:
   ```bash
   # Sin token → 401
   curl https://tu-app.railway.app/admin/clientes?empresa_id=test
   # Con token incorrecto → 401
   curl -H "Authorization: Bearer token_malo" https://tu-app.railway.app/admin/empresas
   ```

### 9. Verificar seed de profesionales

```sql
SELECT nombre, especialidad, tarifa_particular, tarifa_obra_social FROM profesionales;
-- Debe mostrar: Lic. Renals (30000/19000) y Dr. Barros (80000/45000)
```

---

## Proyecciones y deuda técnica pendiente

### Funcionalidades pausadas (Sprint 2)
- **Confirmación automática de pagos por Mercado Pago** — los archivos `confirmadora_pagos.py` y `agendar_y_pagar.py` fueron eliminados (stubs sin conectar). Cuando se retome, hay que reimplementar contra la arquitectura multi-tenant actual.

### Deuda técnica activa
- **Calendarios por profesional.** `Profesional.calendar_id` existe pero no se usa. Pendiente: en `agendadora.py`, leer `get_calendar_id(profesional)` desde `services/profesionales.py`.
- **phone_number_id no configurado en Railway.** Hasta que se ejecute el UPDATE, el sistema usa el fallback a EMPRESA_DEFAULT_ID (funciona, pero no está "limpio" para multi-tenant real).
- **Prompts en código.** `SYSTEM_PROMPT_AGENDADORA` está hardcodeado en `agendadora.py`, no viene de BD. Si se quiere personalizar por empresa, habría que agregar `prompt_agendadora` a la tabla `Empresa`.

### UX y operaciones
- **Endpoint para cambiar `estado_agente` manualmente.** Hoy Walter tiene que ir directo a Postgres (o usar `/unmute` para solo el campo bot_activo).
- **Rate limiting real.** El límite de 20 mensajes no se resetea. Falta `fecha_primer_mensaje` con ventana de tiempo.

### Robustez
- **Reintentos en envío de WhatsApp.** Si `enviar_mensaje_wpp` falla, el mensaje se pierde.
- **Logs estructurados.** Mezcla de `logging.warning()` y `print()`. Pendiente unificar.
- **Tests automatizados.** Mínimo: `_parse_fecha_hora`, router de estados, filtrado de tools por empresa.
