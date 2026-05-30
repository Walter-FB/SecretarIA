import httpx
import logging
from database import SessionLocal
from models import Cliente, Mensaje, ColaAnalisis, Empresa
from datetime import datetime, timedelta
from sqlalchemy.dialects.postgresql import insert as pg_insert
import os
import anthropic

# ===================================================================
# CONFIGURACIÓN
# ===================================================================
WPP_TOKEN    = os.getenv("WHATSAPP_TOKEN")
PHONE_ID     = os.getenv("PHONE_NUMBER_ID")
CLAUDE_KEY   = os.getenv("CLAUDE_API_KEY")
NUMERO_WALTER = "5491131720843"

client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None

# ===================================================================
# PROMPT DE ABBY
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

Si NO es primera vez, pedile el DNI para buscarlo en el sistema y llamá a verificar_paciente_existente. Si encontramos sus datos confirmás el nombre con el paciente y procedés. Si no los encontramos, pedile todos los datos como si fuera primera vez.

Cuando tengas todo y solo si tenes todos los datos, llamás a registrar_paciente y luego a iniciar_agendamiento. si te falta algun dato se lo preguntas muy amablemente. Ahí termina tu trabajo.
esta charla se desarrolla por whatsapp message a message nada de enviar mensajes largos ni nada por el estilo, sean breves y acordes a la situacion.
</TU_TRABAJO>

<DERIVACIONES>
LISTO PARA AGENDAR → registrar_paciente (si es primera vez) → iniciar_agendamiento
NO ES PRIMERA VEZ → verificar_paciente_existente (DNI) → iniciar_agendamiento
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

si no te preguntan nada en concreto inicias la charla con un: Hola! Soy Abby, asistente de la Clínica Abriness, en que puedo ayudarte? (siempre de manera servicial)
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
A: Perfecto, para buscarte dame tu DNI.
P: 12345678
[→ verificar_paciente_existente → "Encontré tus datos, sos Juan Pérez con IOMA, ¿es correcto?" → iniciar_agendamiento con especialidad: psiquiatría, cobertura: IOMA]

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
- verificar_paciente_existente: cuando el paciente dice que NO es primera vez. Pasás el DNI que te dió.
- iniciar_agendamiento: cuando el paciente está listo, pasás especialidad y cobertura
- iniciar_cobranzas: preguntas de precios, solo si el paciente acepta
- notificar_walter_urgente: emergencias, recetas, frustración, pide humano
</HERRAMIENTAS>"""

# ===================================================================
# TOOLS
# ===================================================================
TOOLS_PRINCIPAL = [
    {
        "name": "registrar_paciente",
        "description": "Guarda los datos de un paciente nuevo. Llamar antes de iniciar_agendamiento si es primera vez o si verificar_paciente_existente no encontró resultados.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_completo":   {"type": "string"},
                "dni":               {"type": "string"},
                "obra_social":       {"type": "string"},
                "numero_afiliado":   {"type": "string"},
                "fecha_nacimiento":  {"type": "string"},
                "mail":              {"type": "string"}
            },
            "required": ["nombre_completo", "dni", "obra_social", "numero_afiliado", "fecha_nacimiento"]
        }
    },
    {
        "name": "verificar_paciente_existente",
        "description": "Busca un paciente ya registrado por DNI. Usar cuando el paciente dice que NO es primera vez en la clínica.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dni": {"type": "string", "description": "DNI del paciente a buscar."}
            },
            "required": ["dni"]
        }
    },
    {
        "name": "iniciar_agendamiento",
        "description": "Deriva a la agendadora para coordinar el turno. Usar cuando el paciente ya está registrado o verificado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "especialidad":  {"type": "string", "enum": ["psicología", "psiquiatría"]},
                "cobertura":     {"type": "string"},
                "profesional":   {"type": "string", "description": "Nombre del profesional elegido (ej: 'Lic. Renals', 'Dr. Barros')."}
            },
            "required": ["especialidad", "cobertura"]
        }
    },
    {
        "name": "iniciar_cobranzas",
        "description": "Deriva a cobranzas para consultas de precios. Solo si el paciente aceptó ser derivado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "especialidad": {"type": "string", "enum": ["psicología", "psiquiatría"]},
                "cobertura":    {"type": "string"}
            }
        }
    },
    {
        "name": "notificar_walter_urgente",
        "description": "Escalar a un humano. Para emergencias, crisis, pedido de hablar con humano, recetas o frustración.",
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
# HELPERS DE WHATSAPP
# ===================================================================
async def enviar_mensaje_wpp(to_number: str, texto: str):
    url     = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to":   to_number,
        "type": "text",
        "text": {"body": texto}
    }
    async with httpx.AsyncClient() as http:
        r = await http.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            logging.warning(f"[❌ META {r.status_code}] {r.text}")


async def marcar_leido_wpp(msg_id: str):
    if not msg_id:
        return
    url     = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
    try:
        async with httpx.AsyncClient() as http:
            await http.post(url, json=payload, headers=headers)
    except Exception:
        pass


def upsert_cola_analisis(db, cliente_id: str):
    stmt = pg_insert(ColaAnalisis).values(
        cliente_id=cliente_id,
        fecha_ultima_actividad=datetime.utcnow()
    ).on_conflict_do_update(
        index_elements=['cliente_id'],
        set_={'fecha_ultima_actividad': datetime.utcnow()}
    )
    db.execute(stmt)
    db.commit()
    logging.warning(f"[📋 COLA] Upsert cola_analisis para {cliente_id[:8]}...")


async def enviar_notificacion_a_walter(numero_cliente: str, nombre_cliente: str):
    mensaje_walter = (
        f"Cliente interezado!\nHola Walter! 🥰 Te informo que el numero {{{numero_cliente}}} "
        f"a nombre de {{{nombre_cliente}}} estaría interesado en contactarte. Háblale, suerte y saludos! 👋"
    )
    try:
        await enviar_mensaje_wpp(NUMERO_WALTER, mensaje_walter)
        logging.warning("[✅ NOTIFICACIÓN] Walter avisado.")
    except Exception as e:
        logging.warning(f"[❌ NOTIFICACIÓN WALTER]: {e}")


# ===================================================================
# CONSTRUCCIÓN DEL SYSTEM PROMPT CON MEMORIA
# ===================================================================
def _build_system_prompt(cliente: Cliente, db) -> str:
    datos = cliente.datos_extraidos or {}
    nombre  = cliente.nombre_completo or datos.get("nombre_contacto", "")
    resumen = datos.get("resumen_situacion", "")

    lineas_memoria = []
    if nombre:                   lineas_memoria.append(f"- Nombre: {nombre}")
    if cliente.dni:              lineas_memoria.append(f"- DNI: {cliente.dni}")
    if cliente.obra_social:      lineas_memoria.append(f"- Obra social: {cliente.obra_social}")
    if cliente.numero_afiliado:  lineas_memoria.append(f"- N° afiliado: {cliente.numero_afiliado}")
    if cliente.fecha_nacimiento: lineas_memoria.append(f"- Fecha de nacimiento: {cliente.fecha_nacimiento}")
    if cliente.mail:             lineas_memoria.append(f"- Mail: {cliente.mail}")

    # Profesional habitual si ya está asignado
    if cliente.profesional_id:
        from models import Profesional
        prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
        if prof:
            lineas_memoria.append(f"- Profesional habitual: {prof.nombre} ({prof.especialidad})")

    if resumen: lineas_memoria.append(f"- Contexto previo: {resumen}")

    if not lineas_memoria:
        return SYSTEM_PROMPT_PRINCIPAL

    bloque = (
        "\n\n<MEMORIA_DEL_CLIENTE>\n"
        + "\n".join(lineas_memoria)
        + "\n</MEMORIA_DEL_CLIENTE>\n\n"
        "REGLA: Usá esta memoria para no repetir preguntas. Sé natural, no parezcas un robot leyendo un formulario."
    )
    return SYSTEM_PROMPT_PRINCIPAL + bloque


# ===================================================================
# SECRETARIA PRINCIPAL — Función principal con loop de tools
# ===================================================================
async def secretaria_principal(user_text: str, to_number: str, msg_id: str = None):
    await marcar_leido_wpp(msg_id)

    db = SessionLocal()
    try:
        # ── Cargar / crear cliente ──────────────────────────────────
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
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
        logging.warning(f"[PRINCIPAL] Mensaje de {to_number}: {user_text}")

        # ── Historial últimas 6 horas (máx 40 mensajes) ────────────
        hace_6h = datetime.utcnow() - timedelta(hours=6)
        sesion  = (
            db.query(Mensaje)
            .filter(Mensaje.cliente_id == cliente.id, Mensaje.fecha_creacion >= hace_6h)
            .order_by(Mensaje.fecha_creacion.desc())
            .limit(40)
            .all()
        )
        historial = [
            {"role": "user" if m.rol == "usuario" else "assistant", "content": m.texto}
            for m in reversed(sesion)
        ]
        historial.append({"role": "user", "content": user_text})

        db.add(Mensaje(cliente_id=cliente.id, rol="usuario", texto=user_text))
        upsert_cola_analisis(db, cliente.id)
        db.commit()

        system_prompt = _build_system_prompt(cliente, db)

        # ── Loop de tools (máx 4 iteraciones) ──────────────────────
        MAX_ITER  = 4
        derivar   = None

        for iteracion in range(MAX_ITER):
            logging.warning(f"[PRINCIPAL] Iteración {iteracion + 1}/{MAX_ITER}")

            response = client_claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=700,
                system=system_prompt,
                tools=TOOLS_PRINCIPAL,
                messages=historial
            )

            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            # Respuesta final: texto sin tools → enviar y salir
            if texto_bloques and not tool_bloques:
                texto = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[PRINCIPAL] Respuesta final: {texto}")
                await enviar_mensaje_wpp(to_number, texto)
                db.add(Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto))
                db.commit()
                break

            # Texto previo junto con tools → enviar antes de procesar
            if texto_bloques:
                texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                await enviar_mensaje_wpp(to_number, texto_previo)
                db.add(Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_previo))
                db.commit()

            if not tool_bloques:
                break

            historial.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool in tool_bloques:
                resultado_str, derivar_tool = await _ejecutar_tool(db, cliente, to_number, tool)
                logging.warning(f"[TOOL] {tool.name} → {resultado_str[:80]}")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool.id,
                    "content":     resultado_str
                })
                if derivar_tool:
                    derivar = derivar_tool

            historial.append({"role": "user", "content": tool_results})

            # Tools terminales (cambian estado) → Claude da un mensaje de cierre y salimos
            if derivar:
                break

        logging.warning(f"[PRINCIPAL] Loop terminado. derivar={derivar}")

    except Exception as e:
        logging.warning(f"[❌ PRINCIPAL ERROR]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


# ===================================================================
# EJECUTOR DE TOOLS — devuelve (resultado_str, derivar_flag | None)
# ===================================================================
async def _ejecutar_tool(db, cliente, to_number: str, tool) -> tuple[str, str | None]:
    nombre = tool.name
    inp    = tool.input

    # ── registrar_paciente ─────────────────────────────────────────
    if nombre == "registrar_paciente":
        logging.warning(f"[📝 REGISTRO] Guardando paciente {to_number}")
        cliente.nombre_completo  = inp.get("nombre_completo")
        cliente.dni              = inp.get("dni")
        cliente.obra_social      = inp.get("obra_social")
        cliente.numero_afiliado  = inp.get("numero_afiliado")
        cliente.fecha_nacimiento = inp.get("fecha_nacimiento")
        if inp.get("mail"):
            cliente.mail = inp.get("mail")
        db.commit()
        logging.warning(f"[📝 REGISTRO] Guardado: {cliente.nombre_completo}")
        return (
            f"Paciente registrado correctamente: {cliente.nombre_completo} | "
            f"DNI: {cliente.dni} | Obra social: {cliente.obra_social}.",
            None
        )

    # ── verificar_paciente_existente ───────────────────────────────
    elif nombre == "verificar_paciente_existente":
        dni_buscado = inp.get("dni", "").strip()
        logging.warning(f"[🔍 VERIFICAR] Buscando DNI {dni_buscado} en BD")
        encontrado = (
            db.query(Cliente)
            .filter(Cliente.dni == dni_buscado)
            .first()
        )

        if not encontrado:
            logging.warning(f"[🔍 VERIFICAR] DNI {dni_buscado} no encontrado.")
            return (
                f"No se encontró ningún paciente con DNI {dni_buscado}. "
                "Pedile todos los datos para registrarlo como paciente nuevo.",
                None
            )

        if encontrado.id == cliente.id:
            # El mismo cliente desde el mismo teléfono
            resumen = f"{cliente.nombre_completo or 'Sin nombre'} | Obra social: {cliente.obra_social or 'No registrada'}"
            logging.warning(f"[🔍 VERIFICAR] Mismo cliente. Datos: {resumen}")
            return f"Datos ya cargados: {resumen}.", None

        # Copiar datos de otro registro al cliente actual
        logging.warning(f"[🔍 VERIFICAR] Encontrado en otro teléfono. Copiando datos...")
        cliente.nombre_completo  = encontrado.nombre_completo
        cliente.dni              = encontrado.dni
        cliente.obra_social      = encontrado.obra_social
        cliente.numero_afiliado  = encontrado.numero_afiliado
        cliente.fecha_nacimiento = encontrado.fecha_nacimiento
        if encontrado.mail:
            cliente.mail = encontrado.mail
        if encontrado.profesional_id:
            cliente.profesional_id = encontrado.profesional_id
        db.commit()

        prof_info = ""
        if cliente.profesional_id:
            from models import Profesional
            prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
            if prof:
                prof_info = f" | Profesional habitual: {prof.nombre}"

        resumen = (
            f"Paciente encontrado: {cliente.nombre_completo} | "
            f"Obra social: {cliente.obra_social or 'No registrada'}{prof_info}. "
            "Datos cargados correctamente."
        )
        logging.warning(f"[🔍 VERIFICAR] {resumen}")
        return resumen, None

    # ── iniciar_agendamiento ───────────────────────────────────────
    elif nombre == "iniciar_agendamiento":
        especialidad = inp.get("especialidad", "no especificada")
        cobertura    = inp.get("cobertura",    "no especificada")
        profesional  = inp.get("profesional",  "")
        logging.warning(f"[🗓️ AGENDAMIENTO] {to_number} | {especialidad} | {cobertura} | {profesional}")

        # Guardar profesional en el cliente si llegó por parámetro
        if profesional:
            from services.profesionales import get_profesional_by_nombre
            prof_obj = get_profesional_by_nombre(db, profesional)
            if prof_obj and not cliente.profesional_id:
                cliente.profesional_id = prof_obj.id
                db.commit()
                logging.warning(f"[🗓️ AGENDAMIENTO] Profesional asignado: {prof_obj.nombre}")

        cliente.estado_agente = "agendadora"
        db.commit()

        await enviar_mensaje_wpp(to_number, "Dale, dejame revisar la agenda para coordinar día y hora. Un segundo...")

        try:
            from services.agendadora import secretaria_agendadora
            await secretaria_agendadora(
                f"Iniciar agendamiento para especialidad {especialidad} con cobertura {cobertura}.",
                to_number,
                None
            )
        except Exception as e:
            logging.warning(f"[❌ AGENDAMIENTO ERROR]: {e}")

        return f"Derivado a agendadora. Especialidad: {especialidad}, Cobertura: {cobertura}.", "agendadora"

    # ── iniciar_cobranzas ──────────────────────────────────────────
    elif nombre == "iniciar_cobranzas":
        logging.warning(f"[💸 COBRANZAS] Derivando {to_number}")
        from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
        next_state = await iniciar_cobranzas_svc(
            to_number,
            especialidad=inp.get("especialidad"),
            cobertura=inp.get("cobertura"),
        )
        cliente.estado_agente = next_state
        db.commit()
        return "Derivado a cobranzas. Instrucciones de pago enviadas.", "cobranzas"

    # ── notificar_walter_urgente ───────────────────────────────────
    elif nombre == "notificar_walter_urgente":
        es_emergencia = inp.get("es_emergencia", False)
        logging.warning(f"[🚨 WALTER] Escalamiento {to_number} | emergencia={es_emergencia}")
        datos          = cliente.datos_extraidos or {}
        nombre_cliente = cliente.nombre_completo or datos.get("nombre_contacto", "un paciente")
        await enviar_notificacion_a_walter(to_number, nombre_cliente)
        # No cambia estado — Abby sigue respondiendo
        return f"Walter notificado. Emergencia: {es_emergencia}.", None

    # ── tool desconocida ───────────────────────────────────────────
    else:
        logging.warning(f"[⚠️ TOOL DESCONOCIDA]: {nombre}")
        return f"Tool '{nombre}' no reconocida.", None
