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

    if resumen: lineas_memoria.append(f"- Contexto previo: {resumen}")

    if not lineas_memoria:
        return base

    bloque = (
        "\n\n<MEMORIA_DEL_CLIENTE>\n"
        + "\n".join(lineas_memoria)
        + "\n</MEMORIA_DEL_CLIENTE>\n\n"
        "REGLA: Usá esta memoria para no repetir preguntas. Sé natural, no parezcas un robot leyendo un formulario."
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
                model="claude-haiku-4-5",
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
                if derivar_tool:
                    derivar = derivar_tool
                    cliente.estado_agente = derivar_tool
                    db.commit()

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

