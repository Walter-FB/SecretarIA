# ===================================================================
# SECRETARIA AGENDADORA
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje
from agents.herramientas_secretarias import enviar_mensaje_wpp, marcar_leido_wpp, client_claude
from tools.registry import get_tools_for_agendadora
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import re
import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ===================================================================
# PROMPT
# ===================================================================
SYSTEM_PROMPT_AGENDADORA = """<IDENTIDAD>
Sos la secretaria de agenda de la Clínica Abriness. La conversación YA está en curso: tu compañera ya saludó y confirmó los datos del paciente. NUNCA saludes, NUNCA te presentes, NUNCA vuelvas a confirmar profesional ni cobertura.
Tu único trabajo es coordinar el turno, confirmar el pago y cerrar. Nada más.
Mensajes cortos, sin markdown, sin **, sin -.
</IDENTIDAD>

<TONO>
Cálido, eficiente, al grano. Usás "vos". Una pregunta por mensaje. Sin emojis salvo ✅ al confirmar el turno.
</TONO>

<TU_TRABAJO>
1. Tu primer acto es llamar a consultar_calendar con texto_fecha="mañana" y dias_a_consultar=3. Esto te da los próximos horarios libres disponibles.
2. Ofrecé los próximos 2 o 3 horarios concretos directamente: "tengo lunes 12hs, martes 10hs o miércoles 15hs, te sirve alguno?". Solo preguntá día/hora en abierto si el paciente rechaza todas las opciones o pide otra semana.
3. Si el paciente elige un horario de los ofrecidos: confirmá brevemente ("te confirmo el martes a las 14 con Dr. Barros?") y al aceptar llamá iniciar_cobranzas.
4. Si el horario pedido está DISPONIBLE: confirmá con el paciente y al aceptar llamá iniciar_cobranzas.
5. Si está OCUPADO: mostrá las alternativas que devolvió la herramienta, máximo 5. Cuando el paciente elija una, llamá iniciar_cobranzas.
6. Para llamar iniciar_cobranzas: copiá iso_datetime exactamente del [ISO:...] que apareció en la respuesta del calendario. Llamá la herramienta sin describir lo que vas a hacer.
Tu trabajo termina cuando llamás iniciar_cobranzas.

IMPORTANTE: los slots que devuelve el calendario son horarios DISPONIBLES para elegir, no turnos ya confirmados. Nunca los presentes como "ya tenés turno" — usá "hay disponibilidad" o "podría ser".
</TU_TRABAJO>

<DERIVACIONES>
PACIENTE CONFIRMA TURNO → llamá iniciar_cobranzas como herramienta (NO como texto). Pasás dia, hora, profesional e iso_datetime.
TEMA SE DESVÍA → volver_secretaria_principal
EMERGENCIA O CRISIS → notificar_walter_urgente (es_emergencia: true)
PACIENTE CERRÓ LA CHARLA → omitir_respuesta
</DERIVACIONES>

<EMERGENCIA>
Si detectás crisis o urgencia emocional:
1. Cortá el flujo.
2. "Entiendo que estás pasando por un momento muy difícil. Voy a conectarte con alguien ahora. Si es urgente, llamá al 135 o dirigite a la guardia más cercana."
3. Llamá notificar_walter_urgente con es_emergencia: true.
</EMERGENCIA>"""


# ===================================================================
# GOOGLE CALENDAR — Service Account
# ===================================================================
SCOPES        = ['https://www.googleapis.com/auth/calendar.events',
                 'https://www.googleapis.com/auth/calendar.readonly']
TIMEZONE      = ZoneInfo('America/Argentina/Buenos_Aires')
HORA_APERTURA = 9
HORA_CIERRE   = 18

# Tools que cortan el loop
_TERMINAL_TOOLS = {"iniciar_cobranzas", "omitir_respuesta"}


def _build_calendar_service():
    sa_json = os.getenv('GOOGLE_SERVICE_ACCOUNT')
    if not sa_json:
        raise EnvironmentError("Falta la variable de entorno GOOGLE_SERVICE_ACCOUNT.")
    try:
        info = json.loads(sa_json)
    except json.JSONDecodeError as e:
        logging.warning(f"❌ [CALENDAR ERROR]: JSON de Service Account inválido: {e}")
        raise
    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return build('calendar', 'v3', credentials=creds, cache_discovery=False)
    except Exception as e:
        logging.warning(f"❌ [CALENDAR ERROR]: Error al autenticar con Google: {e}")
        raise


# ===================================================================
# HELPERS DE FECHA / HORA
# ===================================================================
def _normalize(text: str) -> str:
    return re.sub(r'[^a-z0-9áéíóúüñ\s/\-:\.]', ' ', text.lower())


def _parse_fecha(texto: str):
    t = _normalize(
        texto.lower()
        .replace('pasado mañana', '__pm__')
        .replace(' de la mañana', '').replace(' de la tarde', '').replace(' de la noche', '')
        .replace(' a las ', ' ').replace('hs', '').replace('horas', '')
    )
    hoy = datetime.now(TIMEZONE).date()

    if '__pm__' in t or 'pasado manana' in t:
        return hoy + timedelta(days=2)
    if 'mañana' in t:
        return hoy + timedelta(days=1)
    if 'hoy' in t:
        return hoy

    dias_semana = {
        'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3,
        'viernes': 4, 'sabado': 5, 'domingo': 6,
    }
    for nombre, target in dias_semana.items():
        if nombre in t:
            delta = (target - hoy.weekday()) % 7 or 7
            return hoy + timedelta(days=delta)

    m = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        anio = int(m.group(3)) if m.group(3) else hoy.year
        if anio < 100: anio += 2000
        try: return datetime(anio, mes, dia).date()
        except ValueError: pass

    meses = {
        'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6,
        'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
    }
    for nombre, mes in meses.items():
        if nombre in t:
            dm = re.search(r'(\d{1,2})', t)
            if dm:
                try:
                    f = datetime(hoy.year, mes, int(dm.group(1))).date()
                    return f if f >= hoy else datetime(hoy.year + 1, mes, int(dm.group(1))).date()
                except ValueError: pass
            break

    return None


def _parse_hora(texto: str) -> int | None:
    t = texto.lower()

    m = re.search(r'\b(\d{1,2}):(\d{2})\b', t)
    if m:
        h = int(m.group(1))
        return h + 12 if 'pm' in t and h < 12 else h

    m = re.search(r'(?:a\s+las?|las?)\s+(\d{1,2})', t)
    if m:
        h = int(m.group(1))
        if 'pm' in t and h < 12: h += 12
        if 'am' in t and h == 12: h = 0
        return h

    m = re.search(r'\b(\d{1,2})\s*hs?\b', t)
    if m:
        h = int(m.group(1))
        if 'pm' in t and h < 12: h += 12
        return h

    m = re.search(r'\b(\d{1,2})\s*(am|pm)\b', t)
    if m:
        h = int(m.group(1))
        if m.group(2) == 'pm' and h < 12: h += 12
        if m.group(2) == 'am' and h == 12: h = 0
        return h

    return None


def _slots_disponibles(
    desde: datetime,
    hasta: datetime,
    busy_ranges: list,
    hora_pedida: int | None,
) -> list[datetime]:
    """
    Devuelve hasta 12 slots libres de 1h entre HORA_APERTURA y HORA_CIERRE.
    Solo lunes a viernes.
    """
    MAX_SLOTS = 12
    slots  = []
    cursor = desde

    while cursor < hasta and len(slots) < MAX_SLOTS:
        dia = cursor.date()

        # Solo lunes a viernes (weekday 0-4)
        if dia.weekday() >= 5:
            cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            continue

        apertura = cursor.replace(hour=HORA_APERTURA, minute=0, second=0, microsecond=0)
        cierre   = cursor.replace(hour=HORA_CIERRE,   minute=0, second=0, microsecond=0)

        if dia == desde.date() and hora_pedida is not None:
            h = max(HORA_APERTURA, min(HORA_CIERRE - 1, hora_pedida))
            apertura = apertura.replace(hour=h)

        slot = apertura
        while slot + timedelta(hours=1) <= cierre and len(slots) < MAX_SLOTS:
            fin   = slot + timedelta(hours=1)
            libre = not any(fin > bs and slot < be for bs, be in busy_ranges)
            if libre:
                slots.append(slot)
            slot += timedelta(hours=1)

        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return slots


def _slots_ocupados_local(db, profesional_id: str, fecha_inicio: datetime, fecha_fin: datetime) -> list:
    from models import Turno
    fi = fecha_inicio.replace(tzinfo=None)
    ff = fecha_fin.replace(tzinfo=None)
    ocupados = db.query(Turno).filter(
        Turno.profesional_id    == profesional_id,
        Turno.estado            == "reservado",
        Turno.fecha_hora_inicio >= fi,
        Turno.fecha_hora_inicio <  ff,
    ).all()
    return [(t.fecha_hora_inicio, t.fecha_hora_fin) for t in ocupados]


def _consultar_calendar_local(texto_fecha: str, dias: int = 1, profesional_id: str = None, db=None) -> str:
    fecha       = _parse_fecha(texto_fecha)
    hora_pedida = _parse_hora(texto_fecha)

    if fecha is None:
        return "No entendí la fecha. Podés decirme el día?"

    fecha_inicio = datetime(fecha.year, fecha.month, fecha.day, HORA_APERTURA, 0, tzinfo=TIMEZONE)
    time_max     = fecha_inicio + timedelta(days=max(dias, 1))
    busy_ranges  = _slots_ocupados_local(db, profesional_id, fecha_inicio, time_max) if db and profesional_id else []

    if hora_pedida is not None:
        start = datetime(fecha.year, fecha.month, fecha.day, hora_pedida, 0, tzinfo=TIMEZONE)
        end   = start + timedelta(hours=1)
        libre = not any(end > bs and start < be for bs, be in busy_ranges)
        label = start.strftime('%A %d/%m a las %H:%M').capitalize()
        if libre:
            return f"El horario solicitado está DISPONIBLE.\n- {label} [ISO:{start.isoformat()}]"
        alternativas = _slots_disponibles(fecha_inicio, time_max, busy_ranges, None)
        if not alternativas:
            return (f"El horario solicitado está OCUPADO y no hay más disponibilidad el "
                    f"{fecha.strftime('%d/%m')}. Querés otro día?")
        lineas = [f"- {s.strftime('%A %d/%m a las %H:%M').capitalize()} [ISO:{s.isoformat()}]" for s in alternativas]
        return "El horario solicitado está OCUPADO. Alternativas disponibles:\n" + "\n".join(lineas)

    slots = _slots_disponibles(fecha_inicio, time_max, busy_ranges, hora_pedida)
    if not slots:
        return f"No hay disponibilidad el {fecha.strftime('%d/%m')}. Querés que busque en otra fecha?"
    lineas = [f"- {s.strftime('%A %d/%m a las %H:%M').capitalize()} [ISO:{s.isoformat()}]" for s in slots]
    return "Turnos disponibles:\n" + "\n".join(lineas)


def _consultar_calendar(texto_fecha: str, dias: int = 1, calendar_id: str = None) -> str:
    if not calendar_id:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
    try:
        fecha      = _parse_fecha(texto_fecha)
        hora_pedida = _parse_hora(texto_fecha)

        if fecha is None:
            return "No entendí la fecha. Podés decirme el día?"

        fecha_inicio = datetime(fecha.year, fecha.month, fecha.day,
                                HORA_APERTURA, 0, tzinfo=TIMEZONE)
        time_max = fecha_inicio + timedelta(days=max(dias, 1))

        busy_raw = _build_calendar_service().freebusy().query(body={
            'timeMin':  fecha_inicio.isoformat(),
            'timeMax':  time_max.isoformat(),
            'timeZone': str(TIMEZONE),
            'items':    [{'id': calendar_id}],
        }).execute()['calendars'][calendar_id]['busy']

        busy_ranges = [
            (
                datetime.fromisoformat(b['start'].replace('Z', '+00:00')).astimezone(TIMEZONE),
                datetime.fromisoformat(b['end'].replace('Z', '+00:00')).astimezone(TIMEZONE),
            )
            for b in busy_raw
        ]

        if hora_pedida is not None:
            start = datetime(fecha.year, fecha.month, fecha.day, hora_pedida, 0, tzinfo=TIMEZONE)
            end   = start + timedelta(hours=1)
            libre = not any(end > bs and start < be for bs, be in busy_ranges)
            label = start.strftime('%A %d/%m a las %H:%M').capitalize()
            if libre:
                return f"El horario solicitado está DISPONIBLE.\n- {label} [ISO:{start.isoformat()}]"
            alternativas = _slots_disponibles(fecha_inicio, time_max, busy_ranges, None)
            if not alternativas:
                return (f"El horario solicitado está OCUPADO y no hay más disponibilidad el "
                        f"{fecha.strftime('%d/%m')}. Querés otro día?")
            lineas = [f"- {s.strftime('%A %d/%m a las %H:%M').capitalize()} [ISO:{s.isoformat()}]"
                      for s in alternativas]
            return "El horario solicitado está OCUPADO. Alternativas disponibles:\n" + "\n".join(lineas)

        slots = _slots_disponibles(fecha_inicio, time_max, busy_ranges, None)

        if not slots:
            return (f"No hay disponibilidad el {fecha.strftime('%d/%m')}. "
                    f"Querés que busque en otra fecha?")

        lineas = [
            f"- {s.strftime('%A %d/%m a las %H:%M').capitalize()} [ISO:{s.isoformat()}]"
            for s in slots
        ]
        return "Turnos disponibles:\n" + "\n".join(lineas)

    except Exception as e:
        logging.warning(f"[❌ CALENDAR ERROR]: {e}")
        import traceback; traceback.print_exc()
        return "Hubo un problema consultando la agenda. Podés intentar con otra fecha?"


def _is_busy(service, start: datetime, end: datetime, calendar_id: str = None) -> bool:
    if not calendar_id:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
    busy = service.freebusy().query(body={
        'timeMin':  start.isoformat(),
        'timeMax':  end.isoformat(),
        'timeZone': str(TIMEZONE),
        'items':    [{'id': calendar_id}]
    }).execute()['calendars'][calendar_id]['busy']
    return len(busy) > 0


def _crear_evento(service, titulo: str, start: datetime, end: datetime, descripcion: str = '', calendar_id: str = None) -> str:
    if not calendar_id:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
    logging.warning(f"[CALENDAR] Creando evento '{titulo}' | cal={calendar_id} | {start}")
    event = {
        'summary':     titulo,
        'description': descripcion,
        'start': {'dateTime': start.isoformat(), 'timeZone': str(TIMEZONE)},
        'end':   {'dateTime': end.isoformat(),   'timeZone': str(TIMEZONE)}
    }
    try:
        creado = service.events().insert(calendarId=calendar_id, body=event).execute()
        enlace = creado.get('htmlLink', '')
        logging.warning(f"[CALENDAR] Evento creado: {enlace}")
        return enlace
    except Exception as e:
        logging.warning(f"[CALENDAR ERROR] Fallo al crear evento: {e}")
        raise


# ===================================================================
# HISTORIAL — mismas 4 reglas que la principal
# ===================================================================
def _build_historial(sesion: list, agente_actual: str) -> list:
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

    historial = []
    for msg in raw:
        if historial and historial[-1]["role"] == msg["role"]:
            historial[-1]["content"] += "\n" + msg["content"]
        else:
            historial.append({"role": msg["role"], "content": msg["content"]})
    return historial


# ===================================================================
# FUNCIÓN PRINCIPAL
# ===================================================================
async def secretaria_agendadora(
    user_text: str,
    to_number: str,
    msg_id: str = None,
    empresa_id: str = None,
    _store_as_sistema: bool = False,
    _seguimiento: str = None,
):
    """
    Loop: Claude llama tool → ejecutamos → devolvemos tool_result → Claude responde.

    _store_as_sistema: el mensaje de user_text se guarda con agente='sistema' (no contamina historial).
    _seguimiento: instrucción de seguimiento inyectada al system prompt, sin guardarse en BD.
    """
    try:
        await marcar_leido_wpp(msg_id)
    except Exception as e:
        logging.warning(f"[AGENDADORA] Fallo marcar_leido_wpp: {e}")

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

        calendar_id = (empresa.calendar_id if empresa and empresa.calendar_id
                       else os.getenv("CALENDAR_ID", "primary"))

        cliente = db.query(Cliente).filter(
            Cliente.telefono == to_number,
            Cliente.empresa_id == empresa_id
        ).first()
        if not cliente:
            logging.warning(f"[AGENDADORA] Cliente {to_number} no encontrado. Creando...")
            cliente = Cliente(telefono=to_number, empresa_id=empresa_id, estado_agente="agendadora")
            db.add(cliente)
            db.commit()
            db.refresh(cliente)

        logging.warning(f"[AGENDADORA] {to_number} | empresa={empresa_id} | cal={calendar_id}")

        # Historial últimas 6 horas (máx 40 mensajes)
        hace_6h = datetime.utcnow() - timedelta(hours=6)
        mensajes_recientes = (
            db.query(Mensaje)
            .filter(
                Mensaje.cliente_id    == cliente.id,
                Mensaje.fecha_creacion >= hace_6h,
            )
            .order_by(Mensaje.fecha_creacion.desc())
            .limit(40)
            .all()
        )

        historial = _build_historial(mensajes_recientes, "agendadora")

        # Guardar mensaje entrante (sistema o normal)
        if _seguimiento:
            logging.warning(f"[AGENDADORA] Seguimiento para {to_number}")
        elif user_text:
            agente_msg = "sistema" if _store_as_sistema else "agendadora"
            db.add(Mensaje(
                cliente_id = cliente.id,
                empresa_id = empresa_id,
                rol        = "usuario",
                agente     = agente_msg,
                texto      = user_text,
            ))
            db.commit()
            if not _store_as_sistema:
                cliente.mensajes_enviados += 1
                db.commit()
                historial.append({"role": "user", "content": user_text})
                logging.warning(f"\n[AGENDADORA - {to_number}]: {user_text}")

        # System prompt con contexto del paciente
        datos   = cliente.datos_extraidos or {}
        nombre  = cliente.nombre_completo or datos.get("nombre_contacto", "")
        lineas_ctx = []
        if nombre:
            lineas_ctx.append(f"El paciente se llama {nombre}.")
        if cliente.obra_social:
            lineas_ctx.append(f"Su cobertura es {cliente.obra_social}.")
        if cliente.profesional_id:
            from models import Profesional
            prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
            if prof:
                lineas_ctx.append(f"Fue asignado a {prof.nombre} ({prof.especialidad}).")
        elif datos.get("especialidad_turno"):
            lineas_ctx.append(f"Especialidad a agendar: {datos['especialidad_turno']}.")

        ctx_paciente = ("\n\n<CONTEXTO_PACIENTE>\n" + "\n".join(lineas_ctx) + "\n</CONTEXTO_PACIENTE>") if lineas_ctx else ""

        _now_arg = datetime.now(TIMEZONE)
        _dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
        fecha_hoy = f"Hoy es {_dias_es[_now_arg.weekday()]} {_now_arg.strftime('%d/%m/%Y')}, {_now_arg.strftime('%H:%M')}hs (Argentina)."
        system_final = SYSTEM_PROMPT_AGENDADORA + ctx_paciente + f"\n\n<FECHA_ACTUAL>{fecha_hoy}</FECHA_ACTUAL>"

        if _seguimiento:
            system_final += f"\n\n<SEGUIMIENTO>{_seguimiento}</SEGUIMIENTO>"

        definitions, handlers = get_tools_for_agendadora(empresa)

        # Loop de herramientas
        MAX_ITERACIONES = 5
        for _ in range(MAX_ITERACIONES):
            response = client_claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                system=system_final,
                tools=definitions,
                messages=historial
            )

            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            if texto_bloques and not tool_bloques:
                texto_respuesta = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[AGENDADORA] Respuesta final: {texto_respuesta}")

                keywords_cobranza = ("cobr", "transfer", "pago", "abonar", "alias", "cvu")
                if any(kw in texto_respuesta.lower() for kw in keywords_cobranza):
                    logging.warning("[AGENDADORA] Detectado intento de cobranza en texto — forzando tool call")
                    historial.append({"role": "assistant", "content": texto_respuesta})
                    historial.append({"role": "user", "content": "Llamá iniciar_cobranzas como herramienta ahora."})
                    continue

                await enviar_mensaje_wpp(to_number, texto_respuesta)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="agendadora", texto=texto_respuesta))
                break

            # Texto previo junto con tools: descartar si alguna tool es terminal
            if texto_bloques:
                es_terminal = any(t.name in _TERMINAL_TOOLS for t in tool_bloques)
                if not es_terminal:
                    texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                    await enviar_mensaje_wpp(to_number, texto_previo)
                    db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", agente="agendadora", texto=texto_previo))

            if not tool_bloques:
                break

            historial.append({"role": "assistant", "content": response.content})

            tool_results = []
            derivar      = None

            for tool in tool_bloques:
                logging.warning(f"[TOOL] {tool.name} | input: {tool.input}")
                handler_fn = handlers.get(tool.name)
                if handler_fn:
                    try:
                        resultado, derivar_tool = await handler_fn(tool.input, cliente, db, empresa)
                    except Exception as e:
                        logging.warning(f"[ERROR TOOL {tool.name}]: {e}")
                        import traceback; traceback.print_exc()
                        resultado    = "Error al ejecutar la herramienta."
                        derivar_tool = None
                else:
                    logging.warning(f"[TOOL DESCONOCIDA]: {tool.name}")
                    resultado    = f"Tool '{tool.name}' no reconocida."
                    derivar_tool = None

                logging.warning(f"[TOOL] {tool.name} → {resultado[:80]}")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool.id,
                    "content":     resultado
                })
                if derivar_tool == "_skip_":
                    derivar = "_skip_"
                elif derivar_tool:
                    derivar = derivar_tool

            historial.append({"role": "user", "content": tool_results})

            if derivar:
                if derivar != "_skip_":
                    cliente.estado_agente = derivar
                db.commit()
                break

        db.commit()
        logging.warning(f"[✅ AGENDADORA] Proceso completado para {to_number}")

    except Exception as e:
        logging.warning(f"\n[❌ ERROR AGENDADORA]: {e}")
        import traceback; traceback.print_exc()
        try:
            await enviar_mensaje_wpp(to_number, "Perdón, hubo un problema revisando la agenda. Podés decirme otra fecha o horario?")
        except Exception:
            pass
    finally:
        db.close()
