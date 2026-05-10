# SecretarIA v2 — Documentación

Chatbot de IA para WhatsApp. Atiende clientes, asesora, agenda reuniones y hace seguimiento automático.

> [!WARNING]
> **🚨 AVISO GIGANTE: MÓDULO DE PAGOS 🚨**
> Todo el flujo relacionado con cobros, señas, integraciones con Mercado Pago, el estado `agendar_y_pagar`, la tabla `Pago` y `confirmadora_pagos.py` **queda relegado exclusivamente para el SPRINT 2**. Actualmente **NO ES PRIORIDAD** y el foco está 100% en atención, agendamiento y seguimiento.

> [!CAUTION]
> **⚠️ EL MENSAJE DE NOTIFICACIÓN A WALTER ES SAGRADO, NUNCA SE TOCA ⚠️**
> Bajo NINGUNA circunstancia se debe alterar la redacción de la función `enviar_notificacion_a_walter` en `secretaria_principal.py`. Es intocable:
> `mensaje_walter = f"Cliente interezado!\nHola Walter! 🥰 Te informo que el numero {{{numero_cliente}}} a nombre de {{{nombre_cliente}}} estaría interesado en contactarte. Háblale, suerte y saludos! 👋"`

---

## Arquitectura en 30 segundos

```
Cliente manda WhatsApp
       ↓
   whatsapp.py (switch)
       ↓
  ¿estado_agente?
   ┌────┼────────┐
principal  agendadora  manual
   ↓         ↓          ↓
Atiende    Agenda    Walter
y asesora  reunión   maneja
   ↓         ↓
cola_analisis ←──────┘
       ↓
  Job 21:00hs
  (clasifica)
       ↓
  seguimientos
       ↓
  Job cada 1hr
  (manda wpp)
```

---

## Módulos

### `main.py` — El arranque
Inicia FastAPI, crea las tablas si no existen, conecta las rutas y programa los 2 jobs automáticos (analista nocturno + seguimiento).

### `database.py` — Conexión a PostgreSQL
Configura SQLAlchemy. Lee `DATABASE_URL` del `.env`. Si no hay, usa SQLite local como fallback.

### `models.py` — Las 5 tablas
| Tabla | Para qué |
|-------|----------|
| `Empresa` | Multi-tenant futuro. Por ahora no se usa activamente. |
| `Cliente` | Teléfono, datos extraídos, contador de mensajes, **estado_agente** (el switch). |
| `Mensaje` | Historial completo. Cliente_id + rol (usuario/asistente) + texto + fecha. |
| `ColaAnalisis` | Lista de "charlas pendientes de analizar". El nocturno la barre y la limpia. |
| `Seguimiento` | Remarketing. Estado (pendiente/enviado) + fecha programada. |

### `init_db.py` — Creador de tablas manual
Script que corrés una vez para crear las tablas en PostgreSQL. `main.py` también lo hace automáticamente.

---

### `routes/whatsapp.py` — El enrutador
Recibe los webhooks de Meta (WhatsApp) y decide qué hacer según `estado_agente` del cliente:

| Estado | Acción |
|--------|--------|
| `principal` | Manda el mensaje a `secretaria_principal()` |
| `agendadora` | Manda el mensaje a `secretaria_agendadora()` |
| `manual` | No hace nada. Walter maneja directo. |

También tiene el anti-spam (límite de 20 mensajes) y el endpoint `/ver_clientes`.

---

### `services/secretaria_principal.py` — Secretaria Principal
**Misión**: Atender al cliente, asesorar sobre chatbots/desarrollo, y decidir si escalar o agendar.

- Lee historial de las últimas 6 horas
- Inyecta `datos_extraidos` como memoria anti-amnesia (~20 tokens)
- Tiene solo 2 herramientas:
  - `iniciar_agendamiento` → transfiere a la Agendadora
  - `notificar_walter_urgente` → escala a Walter
- Antes de escalar/transferir, corre `secretaria_resumen()` para generar contexto
- Hace upsert en `cola_analisis` con cada mensaje
- Modelo: `claude-haiku-4-5`

También contiene los helpers compartidos: `enviar_mensaje_wpp()` y `marcar_leido_wpp()`.

---

### `services/analista.py` — Secretaria de Resumen
**Misión**: Generar un resumen de 3 líneas antes de escalar a Walter o transferir a la Agendadora. Walter nunca recibe un ping sin contexto.

- Lee historial de sesión (6hs)
- Extrae/actualiza: nombre, rubro, necesidad, resumen
- Guarda en `datos_extraidos` del cliente
- Modelo: `claude-3-haiku-20240307` (ultra barato)

---

### `services/agendadora.py` — Secretaria Agendadora
**Misión**: Coordinar día y hora de reunión con el cliente. Contexto limpio, sin historial largo.

- Lee solo los últimos 3 mensajes + nombre del cliente
- 3 herramientas (actualmente stubs, pendiente Google Calendar API):
  - `consultar_calendar` — lee disponibilidad
  - `crear_evento_calendar` — crea el evento
  - `notificar_walter_reunion` — avisa a Walter
- Cuando termina → devuelve `estado_agente` a `"principal"`
- Modelo: `claude-haiku-4-5`

> 🚧 **Pendiente**: Implementar la API real de Google Calendar. Los stubs están marcados con TODO.

---

### `services/analista_nocturno.py` — Job Analista Nocturno
Corre automático a las 21:00 ARG con APScheduler:

1. Lee toda la tabla `cola_analisis`
2. Para cada charla → llama a Haiku → clasifica: cerrada / en_progreso / fria / perdida
3. Guarda resumen en `datos_extraidos`
4. Si fría o perdida → crea un `seguimiento` para mañana a las 14:00
5. Borra la fila de `cola_analisis`

### `services/seguimiento.py` — Job de Seguimiento
Corre cada 1 hora con APScheduler:

1. Busca `seguimientos` donde estado = pendiente y fecha_programada ≤ ahora
2. Manda mensaje fijo de WhatsApp al cliente
3. Marca como enviado

---

## Variables de entorno (.env)

```
DATABASE_URL=postgresql://...         # PostgreSQL (Railway)
WHATSAPP_TOKEN=...                    # Token de la API de Meta
PHONE_NUMBER_ID=...                   # ID del número de WhatsApp Business
CLAUDE_API_KEY=...                    # API key de Anthropic
WEBHOOK_VERIFY_TOKEN=secretarIA       # Token de verificación del webhook
```

## Dependencias clave

```
fastapi
uvicorn
sqlalchemy
psycopg2-binary
anthropic
httpx
python-dotenv
apscheduler
```

---

## Pendientes

- [ ] **Google Calendar API** — Reemplazar stubs en `agendadora.py`
- [ ] **Notificación a Walter** — ~~Implementar~~ ✅ Implementado en `secretaria_principal.py`
- [ ] **Seguimiento v2** — Mensajes personalizados según resumen del analista
- [ ] **Alembic** — Para migraciones de BD cuando se agreguen columnas nuevas

---

## Tarea 2: Escalado Multi-Empresa (muy a futuro)

> Escalar el sistema para que múltiples números de WhatsApp funcionen como secretarias independientes, cada una con su propia personalidad y propósito.

### Lo que ya está listo

La tabla `Empresa` ya existe en `models.py`:

```python
class Empresa(Base):
    id = Column(String, primary_key=True)
    nombre = Column(String)              # "Pizzería Don Juan"
    telefono_bot = Column(String)        # El número de WhatsApp de ESA secretaria
    prompt_personalidad = Column(String)  # El prompt específico de ese negocio
    clientes = relationship("Cliente")   # Sus clientes
```

Y `Cliente` ya tiene `empresa_id` como foreign key. Cada cliente pertenece a una empresa.

### Lo que falta conectar (3 cambios)

**1. En `whatsapp.py`** — Meta manda en el webhook a qué número le escribieron. Hoy lo ignoramos. Habría que buscar qué empresa tiene ese número:
```python
phone_number_id = entry.get("metadata", {}).get("phone_number_id")
empresa = db.query(Empresa).filter(Empresa.telefono_bot == phone_number_id).first()
```

**2. En `secretaria_principal.py`** — En vez de usar `SYSTEM_PROMPT_PRINCIPAL` hardcoded, leer `empresa.prompt_personalidad`:
```python
# Hoy (mono-empresa):
system_prompt_final = SYSTEM_PROMPT_PRINCIPAL + bloque_memoria

# Multi-tenant:
system_prompt_final = empresa.prompt_personalidad + bloque_memoria
```

**3. En `secretaria_principal.py`** — La notificación iría al número del dueño de esa empresa (nuevo campo en tabla `Empresa`) en vez del `NUMERO_WALTER` hardcoded.

### Estado de cada componente

| Componente | Estado para multi-tenant |
|------------|--------------------------|
| Tabla Empresa | ✅ Ya existe |
| Cliente → empresa_id | ✅ Ya existe |
| Leer phone_number_id del webhook | 🔧 1 línea |
| Prompt dinámico por empresa | 🔧 2 líneas |
| Notificación al dueño correcto | 🔧 2 líneas |
| Agendadora por empresa (Calendar distinto) | 🔧 Más laburo |
| Analista Nocturno / Seguimiento | ✅ Ya funcionan por cliente_id, no les importa la empresa |

**El 80% del sistema ya es multi-tenant sin saberlo.** El analista nocturno, el seguimiento, el switch, la cola de análisis — todo opera por `cliente_id`. Lo único "mono-empresa" hoy es el prompt hardcoded, el número de Walter, y que no filtramos por `phone_number_id` en el webhook.
