# ===================================================================
# SECRETARIA AGENDADORA
# Contexto limpio. Solo coordina día y hora con Google Calendar.
# Cuando termina, devuelve el control a la Principal.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje
from services.secretaria_principal import enviar_mensaje_wpp, marcar_leido_wpp, client_claude
from datetime import datetime, timedelta
import os

# ===================================================================
# PROMPT DE LA AGENDADORA (corto, solo agenda)
# ===================================================================
SYSTEM_PROMPT_AGENDADORA = """<IDENTIDAD>
Sos la secretaria de agenda de la Clínica Abriness. El paciente ya fue atendido por Abby y está listo para coordinar su turno. Tenés todos sus datos cargados en el sistema.

Tu único trabajo es coordinar el turno, confirmar el pago y cerrar. Nada más.
Mensajes cortos, sin markdown, sin **, sin -.
</IDENTIDAD>

<TONO>
Cálido, eficiente, al grano. Usás "vos". Una pregunta por mensaje. Sin emojis salvo ✅ al confirmar el turno.
</TONO>

<TU_TRABAJO>
1. Consultá disponibilidad con consultar_calendar según la especialidad del paciente
2. Ofrecé hasta 3 opciones concretas: día, hora y profesional
3. Cuando el paciente elija, confirmá el resumen del turno
4. Informale que para efectivizar el turno necesita abonar la consulta y derivalo a cobranzas

Tu trabajo termina cuando el paciente acepta pagar. De ahí en adelante es cobranzas.
</TU_TRABAJO>

<DERIVACIONES>
PACIENTE ACEPTA PAGAR → iniciar_cobranzas
Confirmás el turno elegido y derivás. Pasás día, hora y profesional.

TEMA SE DESVÍA O PACIENTE QUIERE VOLVER → volver_secretaria_principal
Si el paciente pregunta algo fuera del agendamiento o quiere hablar con Abby de nuevo.

EMERGENCIA O CRISIS → notificar_walter_urgente (es_emergencia: true)
Igual que siempre, prioridad absoluta.
</DERIVACIONES>

<EMERGENCIA>
Si detectás crisis, desesperación o urgencia emocional:
1. Cortá el flujo
2. "Entiendo que estás pasando por un momento muy difícil. Voy a conectarte con alguien del equipo ahora. Si es urgente, llamá al 135 o dirigite a la guardia más cercana."
3. notificar_walter_urgente con es_emergencia: true
</EMERGENCIA>

<CHARLA_MODELO>
A: Hola! Los turnos más próximos disponibles son:
- Lunes 14 a las 10hs con el Lic. Renals
- Miércoles 16 a las 15hs con el Lic. Renals
- Jueves 17 a las 11hs con el Lic. Renals
¿Alguno te queda bien?
P: El miércoles a las 3.
A: Perfecto. Tu turno sería el miércoles 16 a las 15hs con el Lic. Renals. Para efectivizarlo necesitás abonar la consulta previamente. ¿Avanzamos con el pago?
P: Sí.
[→ iniciar_cobranzas con día: miércoles 16, hora: 15hs, profesional: Lic. Renals]

— Paciente se desvía —
P: Disculpa, pero tengo una consulta sobre otra cosa.
A: Sin problema, te vuelvo a conectar con Abby.
[→ volver_secretaria_principal]

— Paciente no acepta pagar —
P: No, prefiero pagarlo el día que voy.
A: Entendido. El turno queda pre-reservado pero se confirma con el pago previo. ¿Querés que te contactemos para coordinar eso?
[→ notificar_walter_urgente con es_emergencia: false]
</CHARLA_MODELO>

<HERRAMIENTAS>
- consultar_calendar: para ver disponibilidad real antes de ofrecer horarios. Siempre usala primero.
- iniciar_cobranzas: cuando el paciente acepta pagar, pasás los datos del turno
- volver_secretaria_principal: si el tema se desvía del agendamiento
- notificar_walter_urgente: emergencias o situaciones fuera de control
</HERRAMIENTAS>"""

# ===================================================================
# TOOLS DE LA AGENDADORA
# ===================================================================
TOOLS_AGENDADORA = [
    {
        "name": "consultar_calendar",
        "description": "Consulta la disponibilidad de Walter en Google Calendar para los próximos días.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_desde": {"type": "string", "description": "Fecha desde (YYYY-MM-DD)"},
                "dias_a_consultar": {"type": "integer", "description": "Cantidad de días (default: 3)"}
            },
            "required": ["fecha_desde"]
        }
    },
    {
        "name": "iniciar_cobranzas",
        "description": "Deriva a cobranzas cuando el paciente acepta pagar el turno. Pasa los datos del turno elegido.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dia": {"type": "string", "description": "Día del turno elegido (ej. miércoles 16)"},
                "hora": {"type": "string", "description": "Hora del turno elegido (ej. 15hs)"},
                "profesional": {"type": "string", "description": "Profesional elegido"}
            },
            "required": ["dia", "hora", "profesional"]
        }
    },
    {
        "name": "volver_secretaria_principal",
        "description": "Devuelve al paciente a la secretaria principal Abby si se desvía del tema o hace otras consultas.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "notificar_walter_urgente",
        "description": "Notifica a Walter sobre emergencias, o cuando el paciente se niega a pagar anticipadamente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "es_emergencia": {"type": "boolean", "description": "True si es crisis psiquiátrica o emocional."}
            },
            "required": ["es_emergencia"]
        }
    }
]


# ===================================================================
# STUBS DE GOOGLE CALENDAR — TODO: Implementar con API real
# ===================================================================
async def _stub_consultar_calendar(fecha_desde: str, dias: int = 3) -> str:
    # TODO: google-api-python-client con Service Account credentials
    print(f"[📅 CALENDAR STUB] Consultando desde {fecha_desde} ({dias} días)")
    return f"Walter tiene disponibilidad mañana a las 10:00, 14:00 y 16:00. Pasado mañana a las 11:00 y 15:00."

async def _stub_crear_evento(titulo: str, fecha_hora: str, duracion: int = 30) -> str:
    # TODO: service.events().insert(calendarId='primary', body=event).execute()
    print(f"[📅 CALENDAR STUB] Evento creado: '{titulo}' el {fecha_hora} ({duracion} min)")
    return f"Evento creado: {titulo} — {fecha_hora}"

async def _stub_notificar_walter_reunion(resumen: str):
    # TODO: enviar_mensaje_wpp(NUMERO_WALTER, resumen)
    print(f"[📩 NOTIF WALTER STUB] {resumen}")


# ===================================================================
# SECRETARIA AGENDADORA — Función principal
# ===================================================================
async def secretaria_agendadora(user_text: str, to_number: str, msg_id: str = None):
    """Contexto limpio: solo últimos 3 mensajes. Cuando termina, devuelve estado a principal."""
    
    await marcar_leido_wpp(msg_id)
    
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
            print(f"[⚠️ AGENDADORA] Cliente {to_number} no encontrado.")
            return
        
        cliente.mensajes_enviados += 1
        print(f"\n[AGENDADORA - {to_number}]: {user_text}")
        
        mensajes_recientes = db.query(Mensaje).filter(
            Mensaje.cliente_id == cliente.id
        ).order_by(Mensaje.fecha_creacion.desc()).limit(3).all()
        
        historial_claude = []
        for m in reversed(mensajes_recientes):
            historial_claude.append({
                "role": "user" if m.rol == "usuario" else "assistant",
                "content": m.texto
            })
        historial_claude.append({"role": "user", "content": user_text})
        
        nuevo_msg = Mensaje(cliente_id=cliente.id, rol="usuario", texto=user_text)
        db.add(nuevo_msg)
        
        datos = cliente.datos_extraidos or {}
        nombre = datos.get("nombre_contacto", "")
        contexto_extra = f"\nEl cliente se llama {nombre}." if nombre else ""
        system_final = SYSTEM_PROMPT_AGENDADORA + contexto_extra
        
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=system_final,
            tools=TOOLS_AGENDADORA,
            messages=historial_claude
        )
        
        texto_respuesta = ""
        tools_a_ejecutar = []
        for block in response.content:
            if block.type == "text":
                texto_respuesta += block.text.strip()
            elif block.type == "tool_use":
                tools_a_ejecutar.append({"name": block.name, "input": block.input})
        
        if texto_respuesta:
            print(f"[AGENDADORA]: {texto_respuesta}\n")
            await enviar_mensaje_wpp(to_number, texto_respuesta)
            resp_msg = Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_respuesta)
            db.add(resp_msg)
        
        for tool in tools_a_ejecutar:
            if tool["name"] == "consultar_calendar":
                resultado = await _stub_consultar_calendar(
                    tool["input"].get("fecha_desde", datetime.utcnow().strftime("%Y-%m-%d")),
                    tool["input"].get("dias_a_consultar", 3)
                )
                await enviar_mensaje_wpp(to_number, resultado)
            elif tool["name"] == "iniciar_cobranzas":
                dia = tool["input"].get("dia", "")
                hora = tool["input"].get("hora", "")
                profesional = tool["input"].get("profesional", "")
                print(f"[💸 AGENDADORA] Turno pre-reservado {dia} a las {hora} con {profesional}. Derivando a cobranzas...")
                
                from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
                await iniciar_cobranzas_svc(to_number)
                cliente.estado_agente = "manual"  # O el estado que maneje cobranzas
                db.commit()
            elif tool["name"] == "volver_secretaria_principal":
                print(f"[🔄 SWITCH] Cliente {to_number} → 'principal'")
                cliente.estado_agente = "principal"
                db.commit()
            elif tool["name"] == "notificar_walter_urgente":
                es_emergencia = tool["input"].get("es_emergencia", False)
                print(f"[🚨 URGENCIA AGENDADORA] Derivando a Walter. Emergencia: {es_emergencia}")
                cliente.estado_agente = "manual"
                
                from services.secretaria_principal import enviar_notificacion_a_walter
                nombre_cliente = cliente.nombre_completo or (cliente.datos_extraidos or {}).get("nombre_contacto", "un paciente")
                await enviar_notificacion_a_walter(to_number, nombre_cliente)
                db.commit()
        
        db.commit()
        
    except Exception as e:
        print(f"\n[❌ ERROR AGENDADORA]: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
