# SecretarIA Atardecer — CLAUDE.md

---

## REGLAS — leer primero

- **`agents/abby.py` — NO modificar el prompt sin que Walter lo pida explícitamente.** Si detectás algo mejorable, mencionalo y esperá aprobación.
- **Walter autoriza de forma implícita.** No pedir confirmación antes de implementar. Actuar y reportar.
- **Alembic — flujo obligatorio si se modificó `models.py`:**
  1. `alembic revision --autogenerate -m "descripcion"` — nunca a mano
  2. `alembic upgrade head` local
  3. `alembic current` para verificar
  4. Recién entonces `git push` — Railway corre `alembic upgrade head` automáticamente
  - Nunca columna nueva en `models.py` sin migración. Nunca migración manual.

---

## Qué es esto

Bot de WhatsApp para **Clínica Abriness** (salud mental). Secretaria virtual: atiende pacientes, coordina turnos, gestiona confirmación de turno y pago (en persona o por transferencia). Corre en Railway (Python/FastAPI). Sin front-end.

**Modelo:** `claude-sonnet-4-6` en Abby (agente único)

---

## Stack

Python 3.11 · FastAPI · PostgreSQL (Railway/pg8000, SQLite local) · SQLAlchemy 2.0 · Alembic · Google Calendar API (Service Account) · Anthropic SDK · Brevo · WhatsApp Cloud API (Meta)

---

## Estructura de archivos

```
main.py                   FastAPI entry + seeds
database.py               SQLAlchemy config
models.py                 Todas las tablas
init_db.py                Seeds: seed_empresa_default, seed_profesionales, seed_abriness_multitenant
reset_db.py               Drop+recrear (solo SQLite local)
empresa_scope.py          EmpresaScope — guardrail multi-tenant

migrations/versions/
  e085c6052f22_initial.py
  4faaa4399732_multi_tenant.py
  b1c2d3e4f5a6_drop_cola_analisis.py

tools/
  registry.py             get_tools_for_abby() — catálogo único
  registrar_paciente.py
  verificar_paciente_existente.py
  consultar_calendar.py
  iniciar_cobranzas.py    Reserva turno + cobro + email
  consultar_precio.py     Solo lectura — devuelve precio directo al paciente
  omitir_respuesta.py     Sin params — retorna _skip_
  notificar_walter_urgente.py
  silenciar_seguimiento.py

  # CÓDIGO MUERTO (no se usan, se conservan como backup):
  iniciar_agendamiento.py     ← era la derivación a agendadora
  volver_secretaria_principal.py  ← era el retorno desde agendadora

routes/
  whatsapp.py             Webhook Meta + router de estados + comandos Walter + locks por cliente
  admin.py                Panel admin — Bearer token

agents/
  herramientas_secretarias.py  Helpers WPP, client_claude, NUMERO_WALTER
  abby.py                      Abby — agente ÚNICO, loop máx 5 iter, sonnet-4-6, temp 0.7
                               Maneja TODO: datos, turnos, precios, emergencias
  seguimiento.py               Timer 2.5min por charla — llama a abby

  # CÓDIGO MUERTO (no se usan, se conservan como backup):
  secretaria_principal.py      ← reemplazado por abby.py
  agendadora.py                ← reemplazado por abby.py

services/
  cobranza.py             Precio + alias + template cierre + email (pago en persona, transferencia opcional)
  profesionales.py        CRUD Profesional scopeado por empresa_id
  mail_confirmacion.py    Email HTML vía Brevo
```

---

## Base de datos

### `empresas`
| Campo | Descripción |
|---|---|
| `id` | UUID (default: `secretaria-enterprise`) |
| `phone_number_id` | **UNIQUE** — identifica el tenant en cada webhook |
| `bot_activo` | False → ignora todos los mensajes |
| `numero_walter` | Admin: recibe notificaciones y comandos |
| `prompt_personalidad` | Si len > 200, reemplaza el prompt hardcodeado de Abby |
| `tools_habilitadas` | JSON list o null (= todas) |
| `calendar_id` | Null = usa env var `CALENDAR_ID` |
| `alias_pago` / `cvu_pago` | Datos bancarios. Si faltan → fallback a Walter con log `[⚠️ PAGO]` |

### `clientes`
| Campo | Descripción |
|---|---|
| `empresa_id` | FK multi-tenant |
| `estado_agente` | El enrutador — ver tabla abajo |
| `bot_activo` | False → mensajes se guardan pero no se procesan |
| `datos_extraidos` | JSON: `resumen_situacion` (legacy), `ultimo_turno`, `especialidad_turno`, `intentos_email` |
| `profesional_id` | FK → profesionales |

### `profesionales` (seed Abriness)
- **Lic. Renals** — psicologo — particular: $30.000 — obra social: $19.000
- **Dr. Barros** — psiquiatra — particular: $80.000 — obra social: $45.000

### `turnos`
Turnos reservados. Motor local (tabla BD), sin Google Calendar.
`profesional_id`, `cliente_id`, `empresa_id`, `fecha_hora_inicio`, `fecha_hora_fin`, `estado` (reservado/cancelado)

### `mensajes`
`cliente_id`, `empresa_id`, `rol` (usuario/asistente), `agente` (abby/cobranzas/sistema/null), `texto`, `fecha_creacion` (UTC)

**`agente` en mensajes — valores:**
- `"abby"` → mensajes del agente unificado
- `"principal"` / `"agendadora"` → legacy, tratados como propios de Abby en el historial
- `"sistema"` → mensaje sintético interno, NUNCA entra al historial
- `"cobranzas"` → template de cierre enviado por el servicio de cobro
- `null` (legacy) → tratado como propio por Abby

---

## Enrutador — `estado_agente`

| Estado | Handler | Descripción |
|---|---|---|
| `principal` | `abby.py` | Flujo completo: datos, turnos, precios, todo |
| `agendadora` | `abby.py` | Backward compat — se trata igual que principal |
| `esperando_mail` | `cobranza.py → handler_esperando_mail` | Espera email del paciente |
| `manual` | — | Walter atiende directamente |

Cualquier otro valor → warning + mensaje ignorado.

---

## Catálogo de tools (Abby)

Contrato de cada handler: `async def handler(tool_input, cliente, session, empresa, scope=None) → (str, estado | None)`

**Sentinel `_skip_`:** si el handler devuelve `"_skip_"` como estado, el loop rompe sin llamar a Claude de nuevo ni cambiar `estado_agente`. Lo usan `consultar_precio` y `omitir_respuesta`.

**Tools terminales** (`_TERMINAL_TOOLS`): `iniciar_cobranzas`, `omitir_respuesta`. Si alguna de estas aparece en un turno, el texto previo del mismo turno se descarta.

**`iniciar_cobranzas`** — recibe `dia/hora/profesional/iso_datetime`, reserva turno (tabla local o Calendar), envía precio + datos transferencia (pago en persona, transferencia opcional), email de confirmación, notifica Walter.

**`consultar_precio`** — lee BD, calcula tarifa, manda el mensaje directo al paciente, retorna `_skip_`.

**`omitir_respuesta`** — no envía nada, retorna `_skip_`. Solo usar cuando llegan varios mensajes seguidos del paciente.

### `tools/registry.py`
- `get_tools_for_abby(empresa)` → todas las tools activas para Abby
- `get_tools_for_empresa()` / `get_tools_for_agendadora()` → DEPRECATED, redirigen a `get_tools_for_abby()`

Las tools **no** hacen commit de `estado_agente`. Lo hace el loop de Abby:
```python
resultado, derivar = await handler_fn(...)
if derivar and derivar != "_skip_":
    cliente.estado_agente = derivar
    db.commit()
```

---

## Historial — 4 reglas (aplicadas en abby.py)

1. `agente == "sistema"` → nunca entra al historial
2. `rol == "usuario"` → entra siempre como `{"role": "user"}`
3. `rol == "asistente"` + agente propio (abby/principal/agendadora/null) → entra como `{"role": "assistant"}`
4. `rol == "asistente"` + agente ajeno (cobranzas) → entra como `{"role": "user"}` con prefijo

Después se colapsan mensajes consecutivos del mismo rol.

---

## Concurrencia

`routes/whatsapp.py` mantiene un dict `_locks` (empresa_id:telefono → asyncio.Lock). Abby corre serializada por cliente vía `_run_locked`. Pacientes distintos corren en paralelo.

---

## Multi-tenant

El webhook extrae `phone_number_id` → busca empresa → fallback a `EMPRESA_DEFAULT_ID`.

`EmpresaScope` en `empresa_scope.py`: queries scopeadas por `empresa_id`.

**Para agregar empresa:** insertar en `empresas` con `phone_number_id`, `bot_activo=True`, `numero_walter`, `alias_pago`, `cvu_pago`.

### Comandos Walter (solo desde `empresa.numero_walter`)

| Comando | Efecto |
|---|---|
| `/mute <num>` | `cliente.bot_activo = False` |
| `/unmute <num>` | `cliente.bot_activo = True` |
| `/estado <num>` | Devuelve estado_agente, bot_activo, nombre |
| `/ayuda` | Lista comandos |
| `/borrarChat` | Elimina cliente de BD (cualquier número, para testing) |

---

## Flujo de un paciente nuevo

```
WhatsApp → webhook → _run_locked → busca empresa → abby.py
  Abby recolecta datos → registrar_paciente
  → consultar_calendar (disponibilidad próximos 3 días)
  → ofrece slots → paciente elige → iniciar_cobranzas
  → reserva turno (tabla local) → envía precio + alias (pago en persona, transferencia opcional)
  → template de cierre → notifica Walter
  → tiene mail: envía email HTML → estado = principal
  → sin mail: pide email → estado = esperando_mail → email válido → estado = principal
```

---

## Cobranza — notas clave

- Tarifa desde tabla `profesionales`, scopeada por `empresa_id`.
- **Pago en persona** al momento de la consulta. Transferencia previa es opcional.
- `alias_pago`/`cvu_pago` de `empresa`. Si faltan → fallback a `walter.mate3` + log `[⚠️ PAGO]`.
- `titular` siempre es `empresa.nombre` (no el fallback).
- `datos_extraidos["ultimo_turno"]` guarda el detalle del turno para el email.
- Después del mensaje de precio+alias se envía template fijo de cierre. Persiste con `agente="cobranzas"`.
- **No hay tracking de pagos automático** — Walter lo gestiona.

---

## Google Calendar

- Auth: Service Account (JSON en `GOOGLE_SERVICE_ACCOUNT`)
- Motor híbrido: `profesional.calendar_id is None` → tabla `turnos` local; con `calendar_id` → Google Calendar
- Slots: lunes a viernes 9–18 hs, bloques de 1 hora, máx 12. Fines de semana filtrados.
- Respuesta incluye `[ISO:datetime]` → Claude lo copia exacto a `iniciar_cobranzas`.

---

## Seguimiento

Timer de 2.5 minutos por charla activa. Si el paciente no responde:
- Condiciones de NO disparo: estado=manual, ya se disparó uno esta sesión, fuera de 9–21hs ARG, flujo de cobranza iniciado.
- Si dispara: invoca a Abby con instrucción sintética en el system prompt (no se guarda en BD). Abby decide si mandar un toque o llamar `omitir_respuesta`.
- Sin APScheduler. Sin remarketing periódico.

---

## Variables de entorno

```
DATABASE_URL              PostgreSQL (falta → SQLite local)
WHATSAPP_TOKEN            API WhatsApp Cloud
PHONE_NUMBER_ID           ID número Meta
CLAUDE_API_KEY            Anthropic
WEBHOOK_VERIFY_TOKEN      default: "secretarIA"
GOOGLE_SERVICE_ACCOUNT    JSON completo Service Account
CALENDAR_ID               Google Calendar ID
BREVO_API_KEY             Emails
ADMIN_TOKEN               Panel /admin (30+ chars, solo Walter lo sabe)
```

---

## Panel /admin

Single-page en `/admin`. Auth por Bearer token (`ADMIN_TOKEN`). Token en memoria JS, no localStorage. `secrets.compare_digest()` en cada request. Ver `routes/admin.py` y `static/admin.html`.

---

## Consideraciones no obvias

- **Sanitización de texto** (`enviar_mensaje_wpp`): se eliminan `¿`, `¡` y `**` de todos los mensajes salientes.
- **Historial a Claude en UTC**, Calendar en `America/Argentina/Buenos_Aires`. No mezclar.
- **SQLite local:** no tiene migraciones aplicadas. Correr `alembic upgrade head` o `reset_db.py` al agregar columnas.
- **`phone_number_id` en Railway:** hasta configurar con `UPDATE empresas SET phone_number_id = 'ID'`, usa fallback a `EMPRESA_DEFAULT_ID`.
- **`agente="sistema"`** en mensajes: filtra mensajes sintéticos del historial. Nunca usar para mensajes reales.
- **Backward compat:** mensajes con `agente="principal"` o `"agendadora"` se tratan como propios de Abby en el historial.

---

## Sanity check rápido

```bash
python -c "
from tools.registry import get_tools_for_abby
defs, hdlrs = get_tools_for_abby(None)
assert 'consultar_calendar' in hdlrs
assert 'iniciar_cobranzas' in hdlrs
assert 'registrar_paciente' in hdlrs
assert 'omitir_respuesta' in hdlrs
print('OK')
"
```

---

## Deuda técnica

- **Calendarios por profesional:** `Profesional.calendar_id` existe pero no se usa activamente — default es tabla local `turnos`.
- **`phone_number_id`** en Railway no configurado — ver consideraciones.
- **Rate limiting:** límite de 35 no tiene ventana de tiempo.
- **Código muerto:** `secretaria_principal.py`, `agendadora.py`, `iniciar_agendamiento.py`, `volver_secretaria_principal.py` se conservan pero no se usan.
