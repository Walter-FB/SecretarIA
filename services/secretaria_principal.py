import httpx
from database import SessionLocal
from models import Cliente, Mensaje, ColaAnalisis, Empresa
from datetime import datetime, timedelta
from sqlalchemy.dialects.postgresql import insert as pg_insert
import os
import anthropic

# ===================================================================
# CONFIGURACIÓN
# ===================================================================
WPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("PHONE_NUMBER_ID")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
NUMERO_WALTER = "5491131720843"

client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None

# ===================================================================
# PROMPT DE LA SECRETARIA PRINCIPAL
# Solo atiende, asesora y decide si escalar o agendar.
# NO analiza, NO resume, NO toca Calendar.
# ===================================================================
SYSTEM_PROMPT_PRINCIPAL = """<IDENTIDAD>
Sos Abby, la asistente virtual de la Clínica Abriness, especializada en salud mental. Sos el primer contacto del paciente por WhatsApp.

Si te preguntan si sos IA: "Sí, soy una asistente virtual de la clínica."
Mensajes cortos, sin markdown, sin **, sin -.
</IDENTIDAD>

<TONO>
Cálido, cercano, profesional. Usás "vos". Sin frases vacías ni adulación. Una pregunta por mensaje. Máximo 3 oraciones. Nada de sonar a formulario o robot. Sin emojis salvo alguno puntual y natural.
</TONO>

<TU_TRABAJO>
Llevás la charla de forma natural hasta que el paciente esté listo para agendar. Durante la conversación necesitás entender:

- Con qué especialidad se quiere atender: psicología (Lic. Renals) o psiquiatría (Dr. Barros). Si menciona otra, informale que por ahora solo contamos con esas dos.
- Si es su primera vez en la clínica
- Su cobertura: IOMA, OSDE, OSBA, Swiss Medical, Médicus, Galeno, o particular. Si no está en la lista: "Por ahora no trabajamos con esa cobertura, ¿continuamos como particular?"

Si es primera vez, pedile todos sus datos juntos de forma natural: nombre completo, DNI, obra social y número de afiliado, fecha de nacimiento, mail (opcional).

Cuando tengas todo y solo si tenes todos los datos, llamás a registrar_paciente y luego a iniciar_agendamiento. si te falta algun dato se lo preguntas muy amablemente. Ahí termina tu trabajo.
esta charla se desarrolla por whatsapp message a message nada de enviar mensajes largos ni nada por el estilo, sean breves y acordes a la situacion.
</TU_TRABAJO>

<DERIVACIONES>
LISTO PARA AGENDAR → registrar_paciente (si es primera vez) → iniciar_agendamiento
Pasás especialidad y cobertura.


EMERGENCIA O CRISIS → notificar_walter_urgente (es_emergencia: true)
RECETAS O MEDICACIÓN → notificar_walter_urgente (es_emergencia: false)
FRUSTRACIÓN (2 mensajes trabados) → notificar_walter_urgente (es_emergencia: false)
PIDE HABLAR CON HUMANO → notificar_walter_urgente (es_emergencia: false)
</DERIVACIONES>

<EMERGENCIA>
Si detectás crisis, desesperación, pensamientos de daño o urgencia emocional:
1. Cortá cualquier otro flujo
2. "Entiendo que estás pasando por un momento muy difícil. Voy a conectarte con alguien del equipo ahora. Si es urgente, llamá al 135 o dirigite a la guardia más cercana."
3. notificar_walter_urgente con es_emergencia: true
</EMERGENCIA>

<CHARLA_MODELO>
— Flujo estándar primera vez —
P: Hola, quiero agendar un turno.
A: Hola! Soy Abby. ¿Con qué especialidad te querés atender, psicología o psiquiatría?
P: No sé bien, lo más próximo posible.
A: Contamos con psicología y psiquiatría. ¿Que especialidad andabas buscando? 
A: Perfecto. ¿Es tu primera vez en la clínica?
P: Sí.
A: ¿Obra social o particular? 😊
P: OSDE
A: Perfecto, para ingresarte en el sistema necesito los siguientes datos por favor:
Nombre completo, DNI, obra social y número de afiliado, fecha de nacimiento y mail si tenés (no es obligatorio).
P: [da todos los datos]
A: Listo, quedaste registrado.
[→ registrar_paciente → iniciar_agendamiento]

— Paciente recurrente —
P: Hola, necesito turno con psiquiatría, tengo IOMA.
A: Hola! ¿Es tu primera vez en la clínica?
P: No, ya me atendí con el Dr. Barros.
A: Perfecto, te gustaria agendar un turno?
[→ iniciar_agendamiento con especialidad: psiquiatría, cobertura: IOMA]

— Especialidad no disponible —
P: Quiero turno con un neurólogo.
A: Por ahora contamos con psicología y psiquiatría. ¿Alguna de las dos te puede servir?

— Precio —
P: ¿Cuánto sale la consulta?
A: Depende de tu cobertura, ¿querés que te derive a cobranzas para que te informen?
P: Sí.
[→ iniciar_cobranzas]

— Crisis —
P: No doy más, estoy muy mal.
A: Entiendo que estás pasando por un momento muy difícil. si te parece bien voy a conectarte con alguien del equipo para que te pueda asistir apropiadamente dale? siempre que necesites siempre podes venir de urgencia a la clinica.
P: Por favor 
[→ notificar_walter_urgente con es_emergencia: true]
</CHARLA_MODELO>

<HERRAMIENTAS>
- registrar_paciente: con todos los datos del paciente nuevo antes de agendar (asegurate de que te haya dado todo los datos en tu charla antes de llamar esta herramienta, si falta alguno pedicelo con educacion asi podes darlo de alta en el sistema)
- iniciar_agendamiento: cuando el paciente está listo, pasás especialidad y cobertura
- iniciar_cobranzas: preguntas de precios, solo si el paciente acepta
- notificar_walter_urgente: emergencias, recetas, frustración, pide humano
</HERRAMIENTAS>"""

# ===================================================================
# TOOLS DE LA SECRETARIA PRINCIPAL (Solo 2)
# ===================================================================
TOOLS_PRINCIPAL = [
    {
        "name": "registrar_paciente",
        "description": "Guarda los datos de un paciente nuevo. Obligatorio llamar antes de iniciar_agendamiento si es primera vez.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_completo": {"type": "string"},
                "dni": {"type": "string"},
                "obra_social": {"type": "string"},
                "numero_afiliado": {"type": "string"},
                "fecha_nacimiento": {"type": "string"},
                "mail": {"type": "string"}
            },
            "required": ["nombre_completo", "dni", "obra_social", "numero_afiliado", "fecha_nacimiento"]
        }
    },
    {
        "name": "iniciar_agendamiento",
        "description": "Deriva a agendar turno. Usar cuando ya se completó el registro o el paciente es recurrente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "especialidad": {"type": "string", "enum": ["psicología", "psiquiatría"]},
                "cobertura": {"type": "string"}
            },
            "required": ["especialidad", "cobertura"]
        }
    },
    {
        "name": "iniciar_cobranzas",
        "description": "Deriva a cobranzas para consultas de precios/pagos, si el paciente aceptó ser derivado.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "notificar_walter_urgente",
        "description": "Escalar a un humano. Para emergencias, crisis, pedido de hablar con humano, recetas, o frustración.",
        "input_schema": {
            "type": "object",
            "properties": {
                "es_emergencia": {"type": "boolean"}
            },
            "required": ["es_emergencia"]
        }
    }
]


# ===================================================================
# FUNCIÓN AUXILIAR: Enviar mensaje de WhatsApp
# ===================================================================
async def enviar_mensaje_wpp(to_number: str, texto: str):
    """Envía un mensaje de texto por WhatsApp vía la API de Meta."""
    url = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": texto}
    }
    async with httpx.AsyncClient() as http:
        r = await http.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            print(f"\n[❌ ERROR META {r.status_code}]: {r.text}")


# ===================================================================
# FUNCIÓN AUXILIAR: Marcar como leído en WhatsApp
# ===================================================================
async def marcar_leido_wpp(msg_id: str):
    """Envía el 'visto' azul en WhatsApp."""
    if not msg_id:
        return
    url = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
    try:
        async with httpx.AsyncClient() as http:
            await http.post(url, json=payload, headers=headers)
    except:
        pass


# ===================================================================
# FUNCIÓN AUXILIAR: Upsert en cola_analisis
# ===================================================================
def upsert_cola_analisis(db, cliente_id: str):
    """Inserta o actualiza la fecha en cola_analisis. Se llama con cada mensaje nuevo."""
    stmt = pg_insert(ColaAnalisis).values(
        cliente_id=cliente_id,
        fecha_ultima_actividad=datetime.utcnow()
    ).on_conflict_do_update(
        index_elements=['cliente_id'],
        set_={'fecha_ultima_actividad': datetime.utcnow()}
    )
    db.execute(stmt)
    db.commit()
    print(f"[📋 COLA] Upsert cola_analisis para cliente {cliente_id[:8]}...")


# ===================================================================
# ENVÍO DE NOTIFICACIÓN A WALTER
# ===================================================================
async def enviar_notificacion_a_walter(numero_cliente: str, nombre_cliente: str):
    """Avisa a Walter por WhatsApp que hay un cliente interesado."""
    mensaje_walter = f"Cliente interezado!\nHola Walter! 🥰 Te informo que el numero {{{numero_cliente}}} a nombre de {{{nombre_cliente}}} estaría interesado en contactarte. Háblale, suerte y saludos! 👋"
    
    try:
        await enviar_mensaje_wpp(NUMERO_WALTER, mensaje_walter)
        print("[✅ NOTIFICACIÓN] Se avisó a Walter sobre el nuevo cliente interesado.")
    except Exception as e:
        print(f"[❌ ERROR NOTIFICACIÓN WALTER]: {e}")


# ===================================================================
# SECRETARIA PRINCIPAL — Función orquestadora
# ===================================================================
async def secretaria_principal(user_text: str, to_number: str, msg_id: str = None):
    """
    La Secretaria Principal. Lee historial 6hs + datos_extraidos,
    responde al cliente, y decide si escalar o agendar.
    """

    # 1. MARCAR COMO LEÍDO
    await marcar_leido_wpp(msg_id)

    # 2. ABRIR DB Y CARGAR CLIENTE
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()

        if not cliente:
            # Auto-asignar a la empresa default
            from init_db import EMPRESA_DEFAULT_ID
            cliente = Cliente(
                telefono=to_number, 
                mensajes_enviados=0, 
                datos_extraidos={},
                empresa_id=EMPRESA_DEFAULT_ID
            )
            db.add(cliente)
            db.commit()
            db.refresh(cliente)

        cliente.mensajes_enviados += 1
        db.commit()

        print(f"\n[CLIENTE - {to_number}]: {user_text}")

        # 3. HISTORIAL DE SESIÓN (últimas 6 horas)
        hace_6_horas = datetime.utcnow() - timedelta(hours=6)

        mensajes_sesion = db.query(Mensaje).filter(
            Mensaje.cliente_id == cliente.id,
            Mensaje.fecha_creacion >= hace_6_horas
        ).order_by(Mensaje.fecha_creacion.desc()).limit(10).all()

        # Armamos el historial para Claude (orden cronológico)
        historial_claude = []
        for m in reversed(mensajes_sesion):
            historial_claude.append({
                "role": "user" if m.rol == "usuario" else "assistant",
                "content": m.texto
            })

        # Agregar el mensaje nuevo al historial
        historial_claude.append({"role": "user", "content": user_text})

        # 4. GUARDAR MENSAJE DEL USUARIO EN SQL
        nuevo_mensaje = Mensaje(cliente_id=cliente.id, rol="usuario", texto=user_text)
        db.add(nuevo_mensaje)
        db.commit()

        # 5. UPSERT EN COLA DE ANÁLISIS
        upsert_cola_analisis(db, cliente.id)

        # 6. INYECTAR MEMORIA ANTI-AMNESIA (datos_extraidos → ~20 tokens)
        datos = cliente.datos_extraidos or {}
        nombre = datos.get("nombre_contacto", "")
        rubro = datos.get("rubro_empresa", "")
        necesidad = datos.get("necesidad_cliente", "")
        resumen = datos.get("resumen_situacion", "")

        bloque_memoria = ""
        if nombre or rubro or necesidad or resumen:
            bloque_memoria = f"""

<MEMORIA_DEL_CLIENTE>
{f'- Nombre: {nombre}' if nombre else ''}
{f'- Rubro: {rubro}' if rubro else ''}
{f'- Necesidad: {necesidad}' if necesidad else ''}
{f'- Contexto previo: {resumen}' if resumen else ''}
</MEMORIA_DEL_CLIENTE>

REGLA: Usá esta memoria para no repetir preguntas. Sé natural, no parezcas un robot leyendo un formulario."""

        system_prompt_final = SYSTEM_PROMPT_PRINCIPAL + bloque_memoria

        # 7. LLAMAR A CLAUDE CON TOOLS
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=system_prompt_final,
            tools=TOOLS_PRINCIPAL,
            messages=historial_claude
        )

        # 8. PROCESAR RESPUESTA (puede ser texto, tool_use, o ambos)
        texto_respuesta = ""
        tools_ejecutadas = []

        for block in response.content:
            if block.type == "text":
                texto_respuesta += block.text.strip()
            elif block.type == "tool_use":
                tools_ejecutadas.append({"name": block.name, "input": block.input})

        # 9. SI HAY TEXTO → Enviar al cliente y guardar
        if texto_respuesta:
            print(f"[SECRETARIA]: {texto_respuesta}\n")
            await enviar_mensaje_wpp(to_number, texto_respuesta)

            respuesta_ia = Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_respuesta)
            db.add(respuesta_ia)
            db.commit()

        # 10. SI HAY TOOL → Ejecutar las acciones correspondientes
        for tool_ejecutada in tools_ejecutadas:
            await _ejecutar_tool_principal(db, cliente, to_number, tool_ejecutada)

    except Exception as e:
        print(f"\n[❌ ERROR CRÍTICO PRINCIPAL]: {e}")
        import traceback
        traceback.print_exc()

    finally:
        db.close()


# ===================================================================
# HANDLER DE TOOLS DE LA PRINCIPAL
# ===================================================================
async def _ejecutar_tool_principal(db, cliente, to_number: str, tool: dict):
    """Procesa las tool calls que hizo la Secretaria Principal."""

    if tool["name"] == "registrar_paciente":
        print(f"[📝 REGISTRO] Guardando paciente {to_number}")
        cliente.nombre_completo = tool["input"].get("nombre_completo")
        cliente.dni = tool["input"].get("dni")
        cliente.obra_social = tool["input"].get("obra_social")
        cliente.numero_afiliado = tool["input"].get("numero_afiliado")
        cliente.fecha_nacimiento = tool["input"].get("fecha_nacimiento")
        cliente.mail = tool["input"].get("mail")
        db.commit()
        print(f"[📝 REGISTRO] Datos guardados: {cliente.nombre_completo}")

    elif tool["name"] == "iniciar_agendamiento":
        especialidad = tool["input"].get("especialidad", "no especificada")
        cobertura = tool["input"].get("cobertura", "no especificada")
        print(f"[🗓️ AGENDAMIENTO] Iniciando para {to_number}. Especialidad: {especialidad}, Cobertura: {cobertura}")

        cliente.estado_agente = "agendadora"
        db.commit()
        print(f"[🔄 SWITCH] Cliente {to_number} → 'agendadora'")

        await enviar_mensaje_wpp(to_number, "Dale, dejame revisar la agenda para coordinar día y hora. Un segundo...")

    elif tool["name"] == "iniciar_cobranzas":
        print(f"[💸 COBRANZAS] Derivando a cobranzas: {to_number}")
        from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
        await iniciar_cobranzas_svc(to_number)
        
        # Lo pasamos a manual para que corte el flujo (ya que es el final del MVP)
        cliente.estado_agente = "manual"
        db.commit()

    elif tool["name"] == "notificar_walter_urgente":
        es_emergencia = tool["input"].get("es_emergencia", False)
        print(f"[🚨 ESCALAMIENTO] Cliente {to_number} escalado. Emergencia: {es_emergencia}")

        cliente.estado_agente = "manual"
        db.commit()
        print(f"[🔄 SWITCH] Cliente {to_number} → estado_agente = 'manual'")

        datos = cliente.datos_extraidos or {}
        nombre_cliente = cliente.nombre_completo or datos.get("nombre_contacto", "un paciente")
        
        await enviar_notificacion_a_walter(to_number, nombre_cliente)
