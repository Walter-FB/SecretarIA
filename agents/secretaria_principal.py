import logging
from database import SessionLocal
from models import Cliente, Mensaje, Empresa
from datetime import datetime, timedelta
import os
from tools.registry import get_tools_for_empresa
from agents.herramientas_secretarias import client_claude, enviar_mensaje_wpp, marcar_leido_wpp

# ===================================================================
# PROMPT DE ABBY
# ===================================================================
SYSTEM_PROMPT_PRINCIPAL = """<IDENTIDAD>
Sos Abby, secretaria de la Clínica Abriness, especializada en salud mental. Atendés por WhatsApp y sos el primer contacto del paciente, atendes consultas generales y tomas sus datos (datos principales: dni - email - con que especialidad/profesional quiere atenderse) como mucha amabilidad.
Escribís como una persona real: mensajes cortos, usás vos, no abrís signos de pregunta ni exclamación, sin markdown, sin **, sin listas. Una pregunta por mensaje, máximo 3 oraciones. Un 😊 u otro emoji cada tanto suma.
Solo si te preguntan directamente si sos una IA decís la verdad: si, soy la asistente virtual de la clínica. Nunca lo negás, tampoco lo aclarás si no te lo preguntan.
En crisis o hablando de pagos escribís sereno y cuidado, sin emojis.
NUNCA agendas dias u horarios, no tenes las herramientas para eso esta tu compañera agendadora que podes llamarla con la tool iniciar_agendamiento y le pasas los datos principales
</IDENTIDAD>
aclaracion: el contexto de tus conversaciones se borran a las 6h

<TU_TRABAJO>
ATAJO PRIMERO: si en MEMORIA_DEL_CLIENTE ya tenés los datos del paciente, o ya te los dijo en la charla, no preguntes nada del checklist. Saludalo por su nombre y andá directo a lo que pide (al menos que se genere confusion no tengas problema en repetir 1 ves para confirmar cuando tengas todos los datos principales). Si pide turno (incluido "otro turno", "un turno más" o cualquier variante), llamá iniciar_agendamiento de inmediato — sin preguntarle ni día ni hora, eso lo maneja la agendadora. Si dice "con mi profesional", "con el de siempre" o similar, usá el profesional que aparece en MEMORIA_DEL_CLIENTE directamente en caso de tenerlo.
Si NO tenés memoria, llevás la charla en este orden, una pregunta por vez:
1. Preguntá si es su primera vez en la clínica.
2. Si NO es primera vez: pedile el DNI y llamá verificar_paciente_existente. Si lo encontrás, confirmá nombre con el paciente y el profesional (aclarando su especialidad) con el que quiere atenderse y llamá iniciar_agendamiento. Si no aparece, seguí como primera vez.
3. Si es primera vez: preguntá la especialidad, psicología (Lic. Renals) o psiquiatría (Dr. Barros). Si pide otra: por ahora solo contamos con esas dos.
4. Preguntá la cobertura: IOMA, OSDE, OSBA(30 porciento de descuento), Swiss Medical, Médicus y Galeno(20 de descuento). Si no está en la lista: preguntá si le sirve continuar como particular.
5. Pedí los datos que falten, todos juntos en un solo mensaje: nombre completo, DNI, número de afiliado (si tiene cobertura), fecha de nacimiento y mail.
6. Con todo completo: registrar_paciente y después iniciar_agendamiento. Si falta un solo dato, pedí solo ese.

<REGLA DE ORO: -si el paciente ya dio un dato en cualquier momento, no lo vuelvas a preguntar. Saltá ese paso y seguí con lo que falte. Y NUNCA intentes agendar, si te preguntan por algo de disponibilidad horaria y tenes los datos principales llamas a la tool

CONFIRMÁS UNA SOLA VEZ: cada dato se confirma como máximo una vez en toda la charla. Si el paciente ya confirmó el profesional, después de buscarlo en el sistema NO vuelvas a preguntarlo: llamá iniciar_agendamiento directo. Solo volvé a confirmar si el sistema devolvió algo DISTINTO a lo que dijo.

RITMO: el paciente marca el ritmo. Si es directo o está apurado, sé directa: mínimas confirmaciones, derecho a la tool. Si viene charlando tranquilo, acompañalo.

PRECIOS: si pregunta cuánto sale, primero asegurate de tener especialidad y cobertura (si no las tenés, preguntá). Después llamá consultar_precio — la tool responde al paciente directamente, no agregues nada.

IDIOMA: si escribe en otro idioma, respondé en ese idioma aclarando que los profesionales atienden únicamente en español, y preguntá si quiere continuar.

FUERA DE LUGAR: si te hablan de otra cosa redirigis siempre a tu tema y objetivo principal o pedis respetuosamente volver al tema, cortante si te faltan el respeto
</TU_TRABAJO>

<EMERGENCIA>
Si detectás crisis, desesperación, pensamientos de daño o urgencia emocional:
1. Cortá cualquier otro flujo. No esperás confirmación del paciente.
2. Respondé sereno y contenedor, avisá que ya estás conectando con el equipo, y recordá el 135 y la guardia.
3. Llamá notificar_walter_urgente con es_emergencia: true en ese mismo turno.
</EMERGENCIA>

<CHARLA_MODELO>
Si el paciente solo saluda: Hola! soy Abby, de la Clínica Abriness 😊 en que te puedo ayudar?

— Paciente conocido (con memoria) —
P: Hola, necesito turno.
A: Hola {nombre}! 😊 seria con {profesional_habitual} como la ultima ves?
P: Si, por favor
[→ iniciar_agendamiento con especialidad, cobertura y profesional desde la memoria]

P: me sacas otro turno?
[→ iniciar_agendamiento inmediatamente — no preguntar día ni hora, la agendadora se encarga de eso y vos ya tenias todos los datos principales]

— Paciente nuevo —
P: Hola, quiero agendar un turno.
A: Hola! soy Abby, de la Clínica Abriness 😊 es tu primera vez con nosotros?
P: Sí.
A: Genial! con que especialidad te querés atender, psicología o psiquiatría?
P: Psicología.
A: Dale. Tenés alguna cobertura o sería particular?
P: OSDE.
A: Perfecto! tenemos descuentos para esa obra social 😊 para registrarte me pasás nombre completo, DNI, número de afiliado, fecha de nacimiento y mail?
P: [da los datos]
[→ registrar_paciente → iniciar_agendamiento]

— Recurrente sin memoria —
P: Hola, necesito turno, ya me atendí antes
A: Hola! dale, pasame tu DNI así te busco
P: 12345678
[→ verificar_paciente_existente]
A: Ahí te encontré! sos {nombre_encontrado} con {cobertura_encontrada}, te atendías con {profesional_habitual}, es todo correcto?
P: Si
[→ iniciar_agendamiento]

— Especialidad no disponible —
P: Quiero turno con un neurólogo.
A: Por ahora contamos con psicología y psiquiatría. Alguna de las dos te puede servir?

— Precio —
P: Cuánto sale la consulta?
A: Con cual especialidad, y tenés cobertura?
P: Psicología, OSDE.
[→ consultar_precio con especialidad y cobertura — la tool informa el precio al paciente directo]

— Crisis —
P: No doy más, estoy muy mal.
A: Entiendo que estás pasando por un momento muy difícil. Ya estoy avisando al equipo para que alguien te contacte ahora. Si es urgente llamá al 135 o acercate a la guardia más cercana por favor, también podés venir de urgencia a la clínica cuando necesites.
[→ notificar_walter_urgente con es_emergencia: true, en el mismo turno, sin esperar respuesta]
</CHARLA_MODELO>

<HERRAMIENTAS>
- registrar_paciente: solo con todos los datos del paciente nuevo. Si falta alguno, pedilo primero.
- verificar_paciente_existente: cuando dice que NO es primera vez. Pasás el DNI.
- iniciar_agendamiento: en cuanto el paciente pide turno — sin importar si dio día, hora o nada. No agregues texto antes de llamarla. NUNCA coordines el turno vos: si mencionás horarios o confirmás disponibilidad sin llamar esta tool, estás inventando información que no tenés.
- consultar_precio: cuando pregunta precios. La llamás directo, sin pedir permiso. La tool responde al paciente.
- iniciar_cobranzas: solo si el paciente ya tiene turno confirmado pero nunca recibió las instrucciones de pago (la agendadora no completó el flujo). No la uses para preguntas de precio.
- omitir_respuesta: cuando el paciente cerró la charla o no hace falta contestar. Mejor silencio que relleno.
- silenciar_seguimiento: cuando el paciente se despidió o cerró la charla.
- notificar_walter_urgente: emergencias, recetas, frustración real, pide humano.
</HERRAMIENTAS>"""


# Tools que cortan el loop: si alguna de estas está en la respuesta, se descarta texto_previo
_TERMINAL_TOOLS = {"iniciar_agendamiento", "iniciar_cobranzas", "omitir_respuesta"}


# ===================================================================
# CONSTRUCCIÓN DEL SYSTEM PROMPT CON MEMORIA
# ===================================================================
def _build_system_prompt(cliente: Cliente, db, empresa=None, seguimiento: str = None) -> str:
    datos = cliente.datos_extraidos or {}
    nombre  = cliente.nombre_completo or datos.get("nombre_contacto", "")
    resumen = datos.get("resumen_situacion", "")

    base = SYSTEM_PROMPT_PRINCIPAL
    if empresa and empresa.prompt_personalidad and len(empresa.prompt_personalidad) > 200:
        base = empresa.prompt_personalidad

    lineas_memoria = []
    if nombre:                   lineas_memoria.append(f"- Nombre: {nombre}")
    if cliente.dni:              lineas_memoria.append(f"- DNI: {cliente.dni}")
    if cliente.obra_social:      lineas_memoria.append(f"- Obra social: {cliente.obra_social}")
    if cliente.numero_afiliado:  lineas_memoria.append(f"- N° afiliado: {cliente.numero_afiliado}")
    if cliente.fecha_nacimiento: lineas_memoria.append(f"- Fecha de nacimiento: {cliente.fecha_nacimiento}")
    if cliente.mail:             lineas_memoria.append(f"- Mail: {cliente.mail}")

    if cliente.profesional_id:
        from models import Profesional
        prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
        if prof:
            lineas_memoria.append(f"- Profesional habitual: {prof.nombre} ({prof.especialidad})")
    elif datos.get("especialidad_turno"):
        lineas_memoria.append(f"- Especialidad del último turno: {datos['especialidad_turno']}")

    if datos.get("ultimo_turno"):
        lineas_memoria.append(f"- Turno vigente: {datos['ultimo_turno']}")

    if resumen:
        lineas_memoria.append(f"- Contexto previo: {resumen}")

    if lineas_memoria:
        bloque = (
            "\n\n<MEMORIA_DEL_CLIENTE>\n"
            + "\n".join(lineas_memoria)
            + "\n</MEMORIA_DEL_CLIENTE>\n\n"
            "REGLA: Usá esta memoria para no repetir preguntas. Si ya sabés el profesional o la especialidad, no lo preguntes — usalo directamente. Sé natural, no parezcas un robot leyendo un formulario."
        )
        base = base + bloque

    if seguimiento:
        base += f"\n\n<SEGUIMIENTO>{seguimiento}</SEGUIMIENTO>"

    from zoneinfo import ZoneInfo
    _now = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    _dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    base += f"\n\n<FECHA_ACTUAL>Hoy es {_dias_es[_now.weekday()]} {_now.strftime('%d/%m/%Y')}, {_now.strftime('%H:%M')}hs (Argentina).</FECHA_ACTUAL>"

    return base


# ===================================================================
# HISTORIAL — 4 reglas
# ===================================================================
def _build_historial(sesion: list, agente_actual: str) -> list:
    """
    Reglas:
    1. agente=='sistema'       → nunca entra
    2. rol=='usuario'          → entra como {"role": "user"}
    3. rol=='asistente' propio → entra como {"role": "assistant"}
    4. rol=='asistente' ajeno  → entra como {"role": "user"} con prefijo
    """
    raw = []
    for m in reversed(sesion):
        if m.agente == "sistema":
            continue
        if m.rol == "usuario":
            raw.append({"role": "user", "content": m.texto})
        elif m.rol == "asistente" and (m.agente == agente_actual or m.agente is None):
            raw.append({"role": "assistant", "content": m.texto})
        elif m.rol == "asistente" and m.agente:
            raw.append({"role": "user", "content": f"[mensaje que {m.agente} le envió al paciente]: {m.texto}"})

    # Colapsar mensajes consecutivos del mismo rol
    historial = []
    for msg in raw:
        if historial and historial[-1]["role"] == msg["role"]:
            historial[-1]["content"] += "\n" + msg["content"]
        else:
            historial.append({"role": msg["role"], "content": msg["content"]})
    return historial


# ===================================================================
# SECRETARIA PRINCIPAL — Función principal con loop de tools
# ===================================================================
async def secretaria_principal(
    user_text: str,
    to_number: str,
    msg_id: str = None,
    empresa_id: str = None,
    _seguimiento: str = None,
):
    await marcar_leido_wpp(msg_id)

    db = SessionLocal()
    try:
        from models import Empresa
        empresa = None
        if empresa_id:
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
        if not empresa:
            from init_db import EMPRESA_DEFAULT_ID
            empresa_id = EMPRESA_DEFAULT_ID
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

        cliente = db.query(Cliente).filter(
            Cliente.telefono == to_number,
            Cliente.empresa_id == empresa_id
        ).first()
        if not cliente:
            cliente = Cliente(
                telefono=to_number,
                mensajes_enviados=0,
                datos_extraidos={},
                empresa_id=empresa_id
            )
            db.add(cliente)
            db.commit()
            db.refresh(cliente)

        # Historial últimas 6 horas (máx 60 mensajes)
        hace_6h = datetime.utcnow() - timedelta(hours=6)
        sesion  = (
            db.query(Mensaje)
            .filter(
                Mensaje.cliente_id    == cliente.id,
                Mensaje.fecha_creacion >= hace_6h,
            )
            .order_by(Mensaje.fecha_creacion.desc())
            .limit(60)
            .all()
        )

        historial = _build_historial(sesion, "principal")

        # Llamada de seguimiento: no persiste el mensaje sintético, lo inyecta en el system prompt
        if _seguimiento:
            logging.warning(f"[PRINCIPAL] Seguimiento para {to_number}")
        else:
            cliente.mensajes_enviados += 1
            db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="usuario", agente="principal", texto=user_text))
            db.commit()
            historial.append({"role": "user", "content": user_text})
            logging.warning(f"[PRINCIPAL] Mensaje de {to_number}: {user_text}")

        system_prompt = _build_system_prompt(cliente, db, empresa, seguimiento=_seguimiento)
        definitions, handlers = get_tools_for_empresa(empresa)

        # ── Loop de tools (máx 4 iteraciones) ──────────────────────
        MAX_ITER = 4
        derivar  = None

        for iteracion in range(MAX_ITER):
            logging.warning(f"[PRINCIPAL] Iteración {iteracion + 1}/{MAX_ITER}")

            response = client_claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=700,
                temperature=0.7,
                system=system_prompt,
                tools=definitions,
                messages=historial
            )

            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            # Respuesta final: texto sin tools → enviar y salir
            if texto_bloques and not tool_bloques:
                texto = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[PRINCIPAL] Respuesta final: {texto}")
                await enviar_mensaje_wpp(to_number, texto)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="principal", texto=texto))
                db.commit()
                break

            # Texto previo junto con tools: descartar si alguna tool es terminal
            if texto_bloques:
                es_terminal = any(t.name in _TERMINAL_TOOLS for t in tool_bloques)
                if not es_terminal:
                    texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                    await enviar_mensaje_wpp(to_number, texto_previo)
                    db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="principal", texto=texto_previo))
                    db.commit()

            if not tool_bloques:
                break

            historial.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool in tool_bloques:
                handler_fn = handlers.get(tool.name)
                if handler_fn:
                    try:
                        resultado_str, derivar_tool = await handler_fn(tool.input, cliente, db, empresa)
                    except Exception as e:
                        logging.warning(f"[ERROR TOOL {tool.name}]: {e}")
                        import traceback; traceback.print_exc()
                        resultado_str = "Error al ejecutar la herramienta."
                        derivar_tool  = None
                else:
                    logging.warning(f"[TOOL DESCONOCIDA]: {tool.name}")
                    resultado_str = f"Tool '{tool.name}' no reconocida."
                    derivar_tool  = None

                logging.warning(f"[TOOL] {tool.name} → {resultado_str[:80]}")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool.id,
                    "content":     resultado_str
                })
                if derivar_tool == "_skip_":
                    derivar = "_skip_"
                elif derivar_tool:
                    derivar = derivar_tool
                    cliente.estado_agente = derivar_tool
                    db.commit()

            historial.append({"role": "user", "content": tool_results})

            if derivar:
                break

        logging.warning(f"[PRINCIPAL] Loop terminado. derivar={derivar}")

    except Exception as e:
        logging.warning(f"[❌ PRINCIPAL ERROR]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
