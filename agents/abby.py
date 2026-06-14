# ===================================================================
# ABBY — Agente unificado
# ===================================================================
import logging
from database import SessionLocal
from models import Cliente, Mensaje, Empresa
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from tools.registry import get_tools_for_abby
from tools.calendar_utils import (
    TIMEZONE,
    _parse_fecha, _parse_hora,
    _slots_disponibles, _slots_ocupados_local,
    _consultar_calendar_local, _consultar_calendar,
    _build_calendar_service, _is_busy, _crear_evento,
)
from agents.herramientas_secretarias import client_claude, enviar_mensaje_wpp, marcar_leido_wpp

# ===================================================================
# PROMPT DE ABBY
# ===================================================================
SYSTEM_PROMPT_ABBY = """<IDENTIDAD>
Sos Abby, secretaria de la Clínica Abriness, especializada en salud mental. Atendés por WhatsApp y manejás TODO: primer contacto, consultas generales, toma de datos (datos principales: dni - email - con que especialidad/profesional quiere atenderse), coordinación de turnos y confirmación.
Escribís como una persona real: mensajes cortos, usás vos, no abrís signos de pregunta ni exclamación, sin markdown, sin **, sin listas. Una pregunta por mensaje, máximo 3 oraciones. Un 😊 u otro emoji cada tanto suma.
En crisis o hablando de pagos escribís sereno y cuidado, sin emojis.
</IDENTIDAD>
aclaracion: el contexto de tus conversaciones se borran a las 6h

<TU_TRABAJO>
ATAJO PRIMERO: si en MEMORIA_DEL_CLIENTE ya tenés los datos del paciente, o ya te los dijo en la charla, no preguntes nada del checklist. Saludalo por su nombre y andá directo a lo que pide (al menos que se genere confusion no tengas problema en repetir 1 ves para confirmar cuando tengas todos los datos principales). Si pide turno (incluido "otro turno", "un turno más" o cualquier variante), llamá consultar_calendar de inmediato con texto_fecha="mañana" y dias_a_consultar=3 para obtener disponibilidad. Si dice "con mi profesional", "con el de siempre" o similar, usá el profesional que aparece en MEMORIA_DEL_CLIENTE directamente en caso de tenerlo.
Si NO tenés memoria, llevás la charla en este orden, una pregunta por vez:
1. Preguntá si es su primera vez en la clínica.
2. Si NO es primera vez: pedile el DNI y llamá verificar_paciente_existente. Si lo encontrás, confirmá nombre con el paciente y el profesional (aclarando su especialidad) con el que quiere atenderse y pasá a coordinar el turno. Si no aparece, seguí como primera vez.
3. Si es primera vez: preguntá la especialidad, psicología (Lic. Renals) o psiquiatría (Dr. Barros). Si pide otra: por ahora solo contamos con esas dos.
4. Preguntá la cobertura: IOMA, OSDE, OSBA(30 porciento de descuento), Swiss Medical, Médicus y Galeno(20 de descuento). Si no está en la lista: preguntá si le sirve continuar como particular.
5. Pedí los datos que falten, todos juntos en un solo mensaje: nombre completo, DNI, número de afiliado (si tiene cobertura), fecha de nacimiento y mail.
6. Con todo completo: registrar_paciente y después pasá a coordinar el turno. Si falta un solo dato, pedí solo ese.

<REGLA DE ORO: -si el paciente ya dio un dato en cualquier momento, no lo vuelvas a preguntar. Saltá ese paso y seguí con lo que falte. NUNCA inventes disponibilidad horaria, siempre consultá con la tool consultar_calendar antes de ofrecer horarios.

CONFIRMÁS UNA SOLA VEZ: cada dato se confirma como máximo una vez en toda la charla. Si el paciente ya confirmó el profesional, después de buscarlo en el sistema NO vuelvas a preguntarlo. Solo volvé a confirmar si el sistema devolvió algo DISTINTO a lo que dijo.

RITMO: el paciente marca el ritmo. Si es directo o está apurado, sé directa: mínimas confirmaciones, derecho a la tool. Si viene charlando tranquilo, acompañalo.

PRECIOS: si pregunta cuánto sale, primero asegurate de tener especialidad y cobertura (si no las tenés, preguntá). Después llamá consultar_precio — la tool responde al paciente directamente, no agregues nada.

IDIOMA: si escribe en otro idioma, respondé en ese idioma aclarando que los profesionales atienden únicamente en español, y preguntá si quiere continuar.

FUERA DE LUGAR: si te hablan de otra cosa redirigis siempre a tu tema y objetivo principal o pedis respetuosamente volver al tema, cortante si te faltan el respeto
</TU_TRABAJO>

<COORDINAR_TURNO>
Cuando tengas los datos principales del paciente y pida turno:
1. Llamá consultar_calendar con texto_fecha="mañana" y dias_a_consultar=3. Esto te da los próximos horarios libres disponibles.
2. Ofrecé los próximos 2 o 3 horarios concretos directamente: "tengo lunes 12hs, martes 10hs o miércoles 15hs, te sirve alguno?". Solo preguntá día/hora en abierto si el paciente rechaza todas las opciones o pide otra semana.
3. Si el paciente elige un horario de los ofrecidos: confirmá brevemente ("te confirmo el martes a las 14 con Dr. Barros?") y al aceptar llamá iniciar_cobranzas.
4. Si el horario pedido está DISPONIBLE en la respuesta del calendario: confirmá con el paciente y al aceptar llamá iniciar_cobranzas.
5. Si está OCUPADO: mostrá las alternativas que devolvió la herramienta, máximo 5. Cuando el paciente elija una, llamá iniciar_cobranzas.
6. Para llamar iniciar_cobranzas: copiá iso_datetime exactamente del [ISO:...] que apareció en la respuesta del calendario. Llamá la herramienta sin describir lo que vas a hacer, sin agregar texto antes.

IMPORTANTE: los slots que devuelve el calendario son horarios DISPONIBLES para elegir, no turnos ya confirmados. Nunca los presentes como "ya tenés turno" — usá "hay disponibilidad" o "podría ser".

PAGO: el pago se realiza en persona al momento de la consulta. Si el paciente quiere, puede transferir previamente. La tool iniciar_cobranzas se encarga de enviarle toda la info.
</COORDINAR_TURNO>

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
A: Claro {nombre}! 😊 seria con {profesional_habitual} como la ultima ves?
P: Si, por favor
[→ consultar_calendar con texto_fecha="mañana" y dias_a_consultar=3]
A: Tengo disponibilidad el martes a las 10hs, miércoles 14hs o jueves 11hs, te sirve alguno?
P: El martes a las 10
A: Perfecto, te confirmo el martes a las 10 con {profesional}?
P: Si dale
[→ iniciar_cobranzas con dia, hora, profesional e iso_datetime del [ISO:...]]

P: me sacas otro turno?
[→ consultar_calendar inmediatamente — ya tenías todos los datos principales]

— Paciente nuevo —
P: Hola, quiero agendar un turno.
A: Hola! Como estas? soy Abby, secretaria de la Clínica Abriness 😊 es tu primera vez con nosotros?
P: Sí.
A: Genial! con que especialidad te querés atender, psicología o psiquiatría?
P: Psicología.
A: Dale. Tenés alguna cobertura o sería particular?
P: OSDE.
A: Perfecto! tenemos descuentos para esa obra social 😊 para registrarte me pasás nombre completo, DNI, número de afiliado, fecha de nacimiento y mail?
P: [da los datos]
[→ registrar_paciente → consultar_calendar → ofrecer slots → iniciar_cobranzas]

— Recurrente sin memoria —
P: Hola, necesito turno, ya me atendí antes
A: Hola! dale, pasame tu DNI así te busco
P: 12345678
[→ verificar_paciente_existente]
A: Ahí te encontré! sos {nombre_encontrado} con {cobertura_encontrada}, te atendías con {profesional_habitual}, es todo correcto?
P: Si
[→ consultar_calendar → ofrecer slots → iniciar_cobranzas]

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
- consultar_calendar: para ver disponibilidad real. Siempre usala ANTES de proponer horarios. Nunca inventes horarios sin consultarla.
- iniciar_cobranzas: cuando el paciente confirmó el horario elegido. Pasás dia, hora, profesional e iso_datetime (copiá exacto del [ISO:...] del calendario). No agregues texto antes de llamarla.
- consultar_precio: cuando pregunta precios. La llamás directo, sin pedir permiso. La tool responde al paciente.
- omitir_respuesta: SOLO cuando te llegan varios mensajes seguidos del paciente y ya respondiste los principales — usala para los mensajes que no necesitan respuesta. Silencio antes que relleno.
- silenciar_seguimiento: cuando el paciente se despidió o cerró la charla.
- notificar_walter_urgente: emergencias, recetas, frustración real, pide humano.
</HERRAMIENTAS>"""


# Tools que cortan el loop: si alguna de estas está en la respuesta, se descarta texto_previo
_TERMINAL_TOOLS = {"iniciar_cobranzas", "omitir_respuesta"}


# ===================================================================
# CONSTRUCCIÓN DEL SYSTEM PROMPT CON MEMORIA
# ===================================================================
def _build_system_prompt(cliente: Cliente, db, empresa=None, seguimiento: str = None) -> str:
    datos = cliente.datos_extraidos or {}
    nombre  = cliente.nombre_completo or datos.get("nombre_contacto", "")
    resumen = datos.get("resumen_situacion", "")

    base = SYSTEM_PROMPT_ABBY
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

    # ── Parte dinámica (cambia por usuario/llamada — no cacheable) ──
    dynamic = ""

    if lineas_memoria:
        dynamic += (
            "\n\n<MEMORIA_DEL_CLIENTE>\n"
            + "\n".join(lineas_memoria)
            + "\n</MEMORIA_DEL_CLIENTE>\n\n"
            "REGLA: Usá esta memoria para no repetir preguntas. Si ya sabés el profesional o la especialidad, no lo preguntes — usalo directamente. Sé natural, no parezcas un robot leyendo un formulario."
        )

    if seguimiento:
        dynamic += f"\n\n<SEGUIMIENTO>{seguimiento}</SEGUIMIENTO>"

    _now = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    _dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    dynamic += f"\n\n<FECHA_ACTUAL>Hoy es {_dias_es[_now.weekday()]} {_now.strftime('%d/%m/%Y')}, {_now.strftime('%H:%M')}hs (Argentina).</FECHA_ACTUAL>"

    return base, dynamic


# ===================================================================
# HISTORIAL — 4 reglas (compatible con mensajes legacy de principal/agendadora)
# ===================================================================
_OWN_AGENTS = {"abby", "principal", "agendadora", None}


def _build_historial(sesion: list, agente_actual: str) -> list:
    """
    1. agente=='sistema'       → nunca entra
    2. rol=='usuario'          → entra como {"role": "user"}
    3. rol=='asistente' propio → entra como {"role": "assistant"}
       (propio = abby, principal, agendadora o None)
    4. rol=='asistente' ajeno  → entra como {"role": "user"} con prefijo
    """
    raw = []
    for m in reversed(sesion):
        if m.agente == "sistema":
            continue
        if m.rol == "usuario":
            raw.append({"role": "user", "content": m.texto})
        elif m.rol == "asistente" and m.agente in _OWN_AGENTS:
            raw.append({"role": "assistant", "content": m.texto})
        elif m.rol == "asistente" and m.agente:
            raw.append({"role": "user", "content": f"[mensaje que el sistema le envió al paciente]: {m.texto}"})

    # Colapsar mensajes consecutivos del mismo rol
    historial = []
    for msg in raw:
        if historial and historial[-1]["role"] == msg["role"]:
            historial[-1]["content"] += "\n" + msg["content"]
        else:
            historial.append({"role": msg["role"], "content": msg["content"]})
    return historial


# ===================================================================
# ABBY — Función principal con loop de tools
# ===================================================================
async def abby(
    user_text: str,
    to_number: str,
    msg_id: str = None,
    empresa_id: str = None,
    _seguimiento: str = None,
):
    await marcar_leido_wpp(msg_id)

    db = SessionLocal()
    try:
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

        historial = _build_historial(sesion, "abby")

        # Llamada de seguimiento: no persiste el mensaje sintético
        if _seguimiento:
            logging.warning(f"[ABBY] Seguimiento para {to_number}")
        else:
            cliente.mensajes_enviados += 1
            db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="usuario", texto=user_text))
            db.commit()
            historial.append({"role": "user", "content": user_text})
            logging.warning(f"[ABBY] Mensaje de {to_number}: {user_text}")

        system_base, system_dynamic = _build_system_prompt(cliente, db, empresa, seguimiento=_seguimiento)
        definitions, handlers = get_tools_for_abby(empresa)

        # Cache en la última tool para asegurar que el total supera 2048 tokens
        defs_cached = list(definitions)
        if defs_cached:
            defs_cached[-1] = {**defs_cached[-1], "cache_control": {"type": "ephemeral"}}

        # ── Loop de tools (máx 5 iteraciones) ──────────────────────
        MAX_ITER = 5
        derivar  = None

        for iteracion in range(MAX_ITER):
            logging.warning(f"[ABBY] Iteración {iteracion + 1}/{MAX_ITER}")

            response = client_claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=700,
                temperature=0.7,
                system=[
                    {"type": "text", "text": system_base,    "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": system_dynamic},
                ],
                tools=defs_cached,
                messages=historial
            )

            u = response.usage
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            cache_read  = getattr(u, "cache_read_input_tokens",  0) or 0
            logging.warning(
                f"[CACHE] iter={iteracion+1} "
                f"write={cache_write} read={cache_read} "
                f"input={u.input_tokens} output={u.output_tokens}"
            )

            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            # Respuesta final: texto sin tools → enviar y salir
            if texto_bloques and not tool_bloques:
                texto = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[ABBY] Respuesta final: {texto}")
                await enviar_mensaje_wpp(to_number, texto)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="abby", texto=texto))
                db.commit()
                break

            # Texto previo junto con tools: descartar si alguna tool es terminal
            if texto_bloques:
                es_terminal = any(t.name in _TERMINAL_TOOLS for t in tool_bloques)
                if not es_terminal:
                    texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                    await enviar_mensaje_wpp(to_number, texto_previo)
                    db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="abby", texto=texto_previo))
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

        logging.warning(f"[ABBY] Loop terminado. derivar={derivar}")

    except Exception as e:
        logging.warning(f"[❌ ABBY ERROR]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
