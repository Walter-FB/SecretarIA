# SecretarIA Atardecer — CLAUDE.md

---

## REGLAS — leer primero

- **`agents/secretaria_principal.py` — NO modificar el prompt sin que Walter lo pida explícitamente.** Si detectás algo mejorable, mencionalo y esperá aprobación.
- **Walter autoriza de forma implícita.** No pedir confirmación antes de implementar. Actuar y reportar.
- **Alembic — flujo obligatorio si se modificó `models.py`:**
  1. `alembic revision --autogenerate -m "descripcion"` — nunca a mano
  2. `alembic upgrade head` local
  3. `alembic current` para verificar
  4. Recién entonces `git push` — Railway corre `alembic upgrade head` automáticamente
  - Nunca columna nueva en `models.py` sin migración. Nunca migración manual.

---

## Qué es esto

Bot de WhatsApp para **Clínica Abriness** (salud mental). Secretaria virtual: atiende pacientes, coordina turnos en Google Calendar, gestiona cobros por transferencia. Corre en Railway (Python/FastAPI). Sin front-end.

**Modelos:** `claude-sonnet-4-6` en secretaria_principal · `claude-haiku-4-5` en agendadora

---

## Stack

Python 3.11 · FastAPI · PostgreSQL (Railway/pg8000, SQLite local) · SQLAlchemy 2.0 · Alembic · Google Calendar API (Service Account) · Anthropic SDK · Brevo · WhatsApp Cloud API (Meta)

---

## Estructura de archivos

```
main.py                   FastAPI entry + seeds (sin APScheduler — seguimiento corre por timer)
database.py               SQLAlchemy config
models.py                 Todas las tablas
init_db.py                Seeds: seed_empresa_default, seed_profesionales, seed_abriness_multitenant
reset_db.py               Drop+recrear (solo SQLite local)
empresa_scope.py          EmpresaScope — guardrail multi-tenant

migrations/versions/
  e085c6052f22_initial.py
  4faaa4399732_multi_tenant.py
  b1c2d3e4f5a6_drop_cola_analisis.py   ← drop tabla cola_analisis

tools/
  registry.py             TOOL_CATALOG, get_tools_for_empresa(), get_tools_for_agendadora()
  registrar_paciente.py
  verificar_paciente_existente.py
  iniciar_agendamiento.py
  iniciar_cobranzas.py    DEFINITION+handler (agendadora) / DEFINITION_PRECIO+handler_precio (principal)
  consultar_precio.py     Solo lectura de BD — devuelve precio y lo manda directo al paciente
  omitir_respuesta.py     Sin params — retorna _skip_, no envía nada al paciente
  notificar_walter_urgente.py
  consultar_calendar.py
  volver_secretaria_principal.py
  silenciar_seguimiento.py

routes/
  whatsapp.py             Webhook Meta + router de estados + comandos Walter + locks por cliente
  admin.py                Panel admin — Bearer token

agents/
  herramientas_secretarias.py  Helpers WPP, client_claude, NUMERO_WALTER
  secretaria_principal.py      Abby — loop máx 4 iter, sonnet-4-6, temp 0.7
  agendadora.py                Agenda — loop máx 5 iter, haiku-4-5, oferta proactiva de slots
  seguimiento.py               Timer 2.5min por charla — sin APScheduler

services/
  cobranza.py             Precio + alias + template cierre + email
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

### `mensajes`
`cliente_id`, `empresa_id`, `rol` (usuario/asistente), `agente` (principal/agendadora/cobranzas/sistema/null), `texto`, `fecha_creacion` (UTC)

**`agente` en mensajes — valores especiales:**
- `"sistema"` → mensaje sintético interno, NUNCA entra al historial de ningún agente
- `"cobranzas"` → template de cierre enviado por el servicio de cobro
- `null` (legacy) → tratado como propio por cualquier agente

---

## Enrutador — `estado_agente`

| Estado | Handler | Descripción |
|---|---|---|
| `principal` | `secretaria_principal.py` | Primer contacto, recolecta datos |
| `agendadora` | `agendadora.py` | Coordina turno en Calendar |
| `esperando_mail` | `cobranza.py → handler_esperando_mail` | Espera email del paciente |
| `manual` | — | Walter atiende directamente |

Cualquier otro valor → warning + mensaje ignorado.

---

## Catálogo de tools

Contrato de cada handler: `async def handler(tool_input, cliente, session, empresa, scope=None) → (str, estado | None)`

**Sentinel `_skip_`:** si el handler devuelve `"_skip_"` como estado, el loop rompe sin llamar a Claude de nuevo ni cambiar `estado_agente`. Lo usan `consultar_precio` y `omitir_respuesta`.

**Tools terminales** (`_TERMINAL_TOOLS` en cada agente): `iniciar_agendamiento`, `iniciar_cobranzas`, `omitir_respuesta`. Si alguna de estas aparece en un turno, el texto previo del mismo turno se descarta (no se envía al paciente).

**`iniciar_cobranzas` tiene dos variantes:**
- `DEFINITION` + `handler` — agendadora: recibe `dia/hora/profesional/iso_datetime`, crea evento en Calendar, luego cobra.
- `DEFINITION_PRECIO` + `handler_precio` — principal (fallback): si la agendadora bugueó y el turno quedó en `datos_extraidos["ultimo_turno"]`, lo rescata y completa el cobro. Para preguntas de precio usar `consultar_precio`.

**`consultar_precio`** — lee BD, calcula tarifa, manda el mensaje directo al paciente, retorna `_skip_`. No notifica a Walter.

**`omitir_respuesta`** — no envía nada, retorna `_skip_`. Disponible en principal y agendadora.

### `tools/registry.py`
- `get_tools_for_empresa(empresa)` → principal: todas las tools incluyendo `omitir_respuesta`, usa `DEFINITION_PRECIO` para cobranzas
- `get_tools_for_agendadora(empresa)` → `consultar_calendar`, `iniciar_cobranzas`, `volver_secretaria_principal`, `notificar_walter_urgente`, `omitir_respuesta`

Las tools **no** hacen commit de `estado_agente`. Lo hace el loop del agente:
```python
resultado, derivar = await handler_fn(...)
if derivar and derivar != "_skip_":
    cliente.estado_agente = derivar
    db.commit()
```

---

## Historial — 4 reglas (aplicadas en ambos agentes)

1. `agente == "sistema"` → nunca entra al historial
2. `rol == "usuario"` → entra siempre como `{"role": "user"}`
3. `rol == "asistente"` + agente propio → entra como `{"role": "assistant"}`
4. `rol == "asistente"` + agente ajeno → entra como `{"role": "user"}` con prefijo `[mensaje que {agente} le envió al paciente]: {texto}`

Después se colapsan mensajes consecutivos del mismo rol.

---

## Concurrencia

`routes/whatsapp.py` mantiene un dict `_locks` (empresa_id:telefono → asyncio.Lock). Todos los agentes corren serializados por cliente vía `_run_locked`. Pacientes distintos corren en paralelo.

---

## Multi-tenant

El webhook extrae `phone_number_id` → busca empresa → fallback a `EMPRESA_DEFAULT_ID`.

`EmpresaScope` en `empresa_scope.py`: queries scopeadas por `empresa_id` (`clientes()`, `cliente_por_telefono()`, `mensajes_de_cliente()`).

**Para agregar empresa:** insertar en `empresas` con `phone_number_id`, `bot_activo=True`, `numero_walter`, `alias_pago`, `cvu_pago`. Eso es todo.

### Comandos Walter (solo desde `empresa.numero_walter`)

| Comando | Efecto |
|---|---|
| `/mute <num>` | `cliente.bot_activo = False` |
| `/unmute <num>` | `cliente.bot_activo = True` |
| `/estado <num>` | Devuelve estado_agente, bot_activo, nombre |
| `/ayuda` | Lista comandos |
| `/borrarChat` | Elimina cliente de BD (cualquier número, para testing) |

Número normalizado antes de buscar (solo dígitos). Ver `_buscar_cliente_normalizado()` en `routes/whatsapp.py`.

**Mute automático** cuando `notificar_walter_urgente` escala emergencia → `estado = manual`.
**Email fallido 3 veces** → `estado → principal` (bot activo), notifica Walter.

---

## Flujo de un paciente nuevo

```
WhatsApp → webhook → _run_locked → busca empresa → router por estado_agente
  principal: Abby recolecta datos → registrar_paciente → iniciar_agendamiento
    → iniciar_agendamiento guarda trigger como agente="sistema" → llama secretaria_agendadora
  agendadora: consultar_calendar proactivo (mañana, 3 días) → oferta de slots
    → paciente elige → iniciar_cobranzas
    → crea evento Calendar → envía precio + alias → template de cierre → notifica Walter
    → tiene mail: envía email HTML → estado = principal
    → sin mail: pide email → estado = esperando_mail → email válido → estado = principal
```

---

## Cobranza — notas clave

- Tarifa desde tabla `profesionales`, scopeada por `empresa_id`.
- `alias_pago`/`cvu_pago` de `empresa`. Si faltan → fallback a `walter.mate3` + log `[⚠️ PAGO]`.
- `titular` siempre es `empresa.nombre` (no el fallback).
- `datos_extraidos["ultimo_turno"]` guarda el detalle del turno para el email y para el fallback de cobranza desde principal.
- Después del mensaje de precio+alias se envía template fijo: `"Gracias {nombre}! quedaste agendado/a para el {info_turno}. Cualquier consulta estamos acá 😊"`. Persiste con `agente="cobranzas"`.
- **No hay tracking de pagos** (pago_estado/comprobantes): el paciente manda el comprobante directamente y Walter lo gestiona.

---

## Google Calendar

- Auth: Service Account (JSON en `GOOGLE_SERVICE_ACCOUNT`)
- Slots: lunes a viernes 9–18 hs, bloques de 1 hora, máx 12. Fines de semana filtrados en código.
- Respuesta incluye `[ISO:datetime]` → Claude lo copia exacto a `iniciar_cobranzas`.

---

## Seguimiento

Timer de 2.5 minutos por charla activa. Si el paciente no responde:
- Condiciones de NO disparo: estado=manual, ya se disparó uno esta sesión, fuera de 9–21hs ARG, flujo de cobranza iniciado (`pago_estado` en datos_extraidos).
- Si dispara: invoca al agente activo con instrucción sintética en el system prompt (no se guarda en BD). El agente decide si mandar un toque o llamar `omitir_respuesta`.
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
MERCADOPAGO_ACCESS_TOKEN  Módulo pausado, debe existir
```

---

## Panel /admin

Single-page en `/admin`. Auth por Bearer token (`ADMIN_TOKEN`). Token en memoria JS, no localStorage. `secrets.compare_digest()` en cada request. Ver `routes/admin.py` y `static/admin.html`.

---

## Consideraciones no obvias

- **Sanitización de texto** (`enviar_mensaje_wpp`): se eliminan `¿`, `¡` y `**` de todos los mensajes salientes.
- **Anti-spam:** `LIMITE_MENSAJES = 35` en `whatsapp.py`. No se resetea (deuda técnica).
- **Historial a Claude en UTC**, Calendar en `America/Argentina/Buenos_Aires`. No mezclar.
- **SQLite local:** no tiene migraciones aplicadas. Correr `alembic upgrade head` o `reset_db.py` al agregar columnas.
- **`phone_number_id` en Railway:** hasta configurar con `UPDATE empresas SET phone_number_id = 'ID'`, usa fallback a `EMPRESA_DEFAULT_ID`.
- **`agente="sistema"`** en mensajes: filtra mensajes sintéticos del historial (triggers de agendadora, instrucciones de seguimiento). Nunca usar para mensajes reales.

---

## Sanity check rápido

```bash
python -c "
from tools.registry import get_tools_for_empresa, get_tools_for_agendadora
from tools import iniciar_cobranzas, consultar_precio, omitir_respuesta
defs_p, hdlrs_p = get_tools_for_empresa(None)
defs_a, hdlrs_a = get_tools_for_agendadora()
assert hdlrs_p['iniciar_cobranzas'] is iniciar_cobranzas.handler_precio
assert hdlrs_a['iniciar_cobranzas'] is iniciar_cobranzas.handler
assert 'consultar_precio' in hdlrs_p
assert 'omitir_respuesta' in hdlrs_p
assert 'omitir_respuesta' in hdlrs_a
print('OK')
"
```

---

## Deuda técnica

- **Calendarios por profesional:** `Profesional.calendar_id` existe pero no se usa en `agendadora.py` — siempre usa el calendar de la empresa.
- **`phone_number_id`** en Railway no configurado — ver consideraciones.
- **`SYSTEM_PROMPT_AGENDADORA`** hardcodeado en `agendadora.py`, no viene de BD.
- **Endpoint para cambiar `estado_agente` manualmente** — hoy requiere SQL directo.
- **Rate limiting:** límite de 35 no tiene ventana de tiempo.
- **MP pausado:** reimplementar contra arquitectura multi-tenant cuando se retome.
- **`pago_estado="pagado"`** nunca se setea automáticamente — Walter lo hace vía SQL por ahora.
