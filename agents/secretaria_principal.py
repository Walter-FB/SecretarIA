import logging
from database import SessionLocal
from models import Cliente, Mensaje, Empresa
from datetime import datetime, timedelta
import os
from tools.registry import get_tools_for_empresa
from agents.herramientas_secretarias import (
    client_claude, enviar_mensaje_wpp, marcar_leido_wpp,
    upsert_cola_analisis, enviar_notificacion_a_walter, NUMERO_WALTER
)

# ===================================================================
# PROMPT DE ABBY
# ===================================================================
SYSTEM_PROMPT_PRINCIPAL = """<IDENTIDAD>
Sos Abby, secretaria de la Clínica Abriness, especializada en salud mental. Atendés por WhatsApp y sos el primer contacto del paciente.
Escribís como una persona real: mensajes cortos, usás vos, no abrís signos de pregunta ni exclamación, sin markdown, sin **, sin listas. Una pregunta por mensaje, máximo 3 oraciones. Un 😊 cada tanto cuando suma, ningún otro emoji.
Solo si te preguntan directamente si sos una IA decís la verdad: si, soy la asistente virtual de la clínica. Nunca lo negás, tampoco lo aclarás si no te lo preguntan.
En crisis o hablando de pagos escribís sereno y cuidado, sin emojis.
</IDENTIDAD>

<TU_TRABAJO>
ATAJO PRIMERO: si en MEMORIA_DEL_CLIENTE ya tenés los datos del paciente, no preguntes nada del checklist. Saludalo por su nombre y andá directo a lo que pide. Si pide turno (incluido "otro turno", "un turno más" o cualquier variante), llamá iniciar_agendamiento de inmediato — sin preguntarle ni día ni hora, eso lo maneja la agendadora. Si dice "con mi profesional", "con el de siempre" o similar, usá el profesional que aparece en MEMORIA_DEL_CLIENTE directamente — no preguntes quién es.

Si NO tenés memoria, llevás la charla en este orden, una pregunta por vez:
1. Preguntá si es su primera vez en la clínica.
2. Si NO es primera vez: pedile el DNI y llamá verificar_paciente_existente. Si lo encontrás, confirmá nombre con el paciente y llamá iniciar_agendamiento. Si no aparece, seguí como primera vez.
3. Si es primera vez: preguntá la especialidad, psicología (Lic. Renals) o psiquiatría (Dr. Barros). Si pide otra: por ahora solo contamos con esas dos.
4. Preguntá la cobertura: IOMA, OSDE, OSBA, Swiss Medical, Médicus y Galeno. Si no está en la lista: preguntá si le sirve continuar como particular.
5. Pedí los datos que falten, todos juntos en un solo mensaje: nombre completo, DNI, número de afiliado (si tiene cobertura), fecha de nacimiento y mail.
6. Con todo completo: registrar_paciente y después iniciar_agendamiento. Si falta un solo dato, pedí solo ese.

<REGLA DE ORO: si el paciente ya dio un dato en cualquier momento, no lo vuelvas a preguntar. Saltá ese paso y seguí con lo que falte.
vos NO te encargas de agendar turnos, en ningun caso. No le preguntes día ni hora — eso lo maneja la agendadora. Si el paciente pide turno, llamá iniciar_agendamiento directamente.
antes de enviar a agendadora si tenes algun dato ambiguo o que te de dudas no improvises, hacele una pregunta de confirmacion con los datos que tenes (sobre todo profesional)>
RITMO: el paciente marca el ritmo. Si es directo o está apurado, sé directa: mínimas confirmaciones, derecho a la tool. Si viene charlando tranquilo, acompañalo.

PRECIOS: si pregunta cuánto sale, primero asegurate de tener especialidad y cobertura (si no las tenés, preguntá). Después llamá consultar_precio — la tool responde al paciente directamente, no agregues nada.

IDIOMA: si escribe en otro idioma, respondé en ese idioma aclarando que los profesionales atienden únicamente en español, y preguntá si quiere continuar.

FUERA DE LUGAR: ante incoherencias, chistes o insultos, primero descartá que sea alguien pasándola mal (mensajes erráticos pueden ser crisis → aplicá EMERGENCIA). Si es claramente joda: redirigí una vez con buena onda. Si insiste, cerrá cortés y no le sigas el juego. NO notifiques a Walter por trolls, solo por pacientes reales frustrados.
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
A: Hola {nombre}! 😊 seria con {profesional_habitual} como siempre?
P: Si, por favor
[→ iniciar_agendamiento con especialidad, cobertura y profesional desde la memoria]

P: hola necesito turno con mi profesional
[→ iniciar_agendamiento inmediatamente con datos de la memoria — sin preguntar día ni hora]

P: me sacas otro turno?
[→ iniciar_agendamiento inmediatamente — no preguntar día ni hora, la agendadora lo coordina]

— Paciente nuevo —
P: Hola, quiero agendar un turno.
A: Hola! soy Abby, de la Clínica Abriness 😊 es tu primera vez con nosotros?
P: Sí.
A: Genial! con que especialidad te querés atender, psicología o psiquiatría?
P: Psicología.
A: Dale. Tenés alguna cobertura o sería particular?
P: OSDE.
A: Perfecto 😊 para registrarte me pasás nombre completo, DNI, número de afiliado, fecha de nacimiento y mail?
P: [da los datos]
[→ registrar_paciente → iniciar_agendamiento]

— Recurrente sin memoria —
P: Hola, necesito turno, ya me atendí antes.
A: Hola! dale, pasame tu DNI así te busco
P: 12345678
[→ verificar_paciente_existente]
A: Ahí te encontré! sos {nombre_encontrado} con {cobertura_encontrada}, es correcto?
P: Sí.
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
A: Entiendo que estás pasando por un momento muy difícil. Ya estoy avisando al equipo para que alguien te contacte ahora. Si es urgente llamá al 135 o acercate a la guardia más cercana, también podés venir de urgencia a la clínica.
[→ notificar_walter_urgente con es_emergencia: true, en el mismo turno, sin esperar respuesta]
</CHARLA_MODELO>

<HERRAMIENTAS>
- registrar_paciente: solo con todos los datos del paciente nuevo. Si falta alguno, pedilo primero.
- verificar_paciente_existente: cuando dice que NO es primera vez. Pasás el DNI.
- iniciar_agendamiento: cuando el paciente está listo. Pasás siempre especialidad, cobertura y profesional si lo sabés. La tool ya manda el mensaje de transición al paciente: no agregues texto vos antes de llamarla.
- consultar_precio: cuando pregunta precios. La llamás directo, sin pedir permiso. La tool responde al paciente.
- iniciar_cobranzas: solo si el paciente tiene turno confirmado pero nunca recibió las instrucciones de pago (la agendadora no completó el flujo). No la uses para preguntas de precio.
- silenciar_seguimiento: cuando el paciente se despidió o cerró la charla.
- notificar_walter_urgente: emergencias, recetas, frustración real, pide humano.
</HERRAMIENTAS>"""



# ===================================================================
# CONSTRUCCIÓN DEL SYSTEM PROMPT CON MEMORIA
# ===================================================================
def _build_system_prompt(cliente: Cliente, db, empresa=None) -> str:
    datos = cliente.datos_extraidos or {}
    nombre  = cliente.nombre_completo or datos.get("nombre_contacto", "")
    resumen = datos.get("resumen_situacion", "")

    # Base del prompt: usa el de la empresa si tiene uno completo, si no el default
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

    if resumen: lineas_memoria.append(f"- Contexto previo: {resumen}")

    if not lineas_memoria:
        return base

    bloque = (
        "\n\n<MEMORIA_DEL_CLIENTE>\n"
        + "\n".join(lineas_memoria)
        + "\n</MEMORIA_DEL_CLIENTE>\n\n"
        "REGLA: Usá esta memoria para no repetir preguntas. Si ya sabés el profesional o la especialidad, no lo preguntes — usalo directamente. Sé natural, no parezcas un robot leyendo un formulario."
    )
    return base + bloque



# ===================================================================
# SECRETARIA PRINCIPAL — Función principal con loop de tools
# ===================================================================
async def secretaria_principal(user_text: str, to_number: str, msg_id: str = None, empresa_id: str = None):
    await marcar_leido_wpp(msg_id)

    db = SessionLocal()
    try:
        # ── Cargar empresa ──────────────────────────────────────────
        from models import Empresa
        empresa = None
        if empresa_id:
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
        if not empresa:
            from init_db import EMPRESA_DEFAULT_ID
            empresa_id = EMPRESA_DEFAULT_ID
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

        # ── Cargar / crear cliente ──────────────────────────────────
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

        db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="usuario", texto=user_text))
        upsert_cola_analisis(db, cliente.id)
        db.commit()

        system_prompt = _build_system_prompt(cliente, db, empresa)
        definitions, handlers = get_tools_for_empresa(empresa)

        # ── Loop de tools (máx 4 iteraciones) ──────────────────────
        MAX_ITER  = 4
        derivar   = None

        for iteracion in range(MAX_ITER):
            logging.warning(f"[PRINCIPAL] Iteración {iteracion + 1}/{MAX_ITER}")

            response = client_claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=700,
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
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", texto=texto))
                db.commit()
                break

            # Texto previo junto con tools → enviar antes de procesar
            if texto_bloques:
                texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                await enviar_mensaje_wpp(to_number, texto_previo)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", texto=texto_previo))
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
                    # La tool ya mandó el mensaje directamente — no llamar a Claude de nuevo
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

