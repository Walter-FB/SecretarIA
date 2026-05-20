# ===================================================================
# SECRETARIA AGENDADORA — versión corregida
# Fixes:
#   1. Loop de tool_use/tool_result correcto (Claude recibe el resultado
#      del calendario y puede formular una respuesta natural)
#   2. Autenticación Google via Service Account (sin OAuth interactivo,
#      funciona en Railway/producción sin navegador)
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje
from services.secretaria_principal import enviar_mensaje_wpp, marcar_leido_wpp, client_claude
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
Sos la secretaria de agenda de la Clínica Abriness. El paciente ya fue atendido por Abby y está listo para coordinar su turno.
Tu único trabajo es coordinar el turno, confirmar el pago y cerrar. Nada más.
Mensajes cortos, sin markdown, sin **, sin -.
</IDENTIDAD>

<TONO>
Cálido, eficiente, al grano. Usás "vos". Una pregunta por mensaje. Sin emojis salvo ✅ al confirmar el turno.
</TONO>

<TU_TRABAJO>
1. Siempre consultá disponibilidad real con consultar_calendar antes de proponer horarios.
2. Si el paciente menciona una fecha o concepto de tiempo (hoy, mañana, pasado mañana, el lunes, 16 de mayo, etc.), usá eso para consultar el calendario.
3. Si no tiene fecha clara, preguntá: "¿Te queda mejor hoy, mañana, pasado mañana o un día específico?"
4. Respondé siempre con texto visible, aunque uses herramientas. No te quedes en silencio.
5. Cuando el paciente elija un turno, confirmá el resumen y derivá a cobranzas. Al llamar iniciar_cobranzas, incluí siempre el campo iso_datetime copiándolo tal cual aparece entre [ISO:...] en la respuesta del calendario.
Tu trabajo termina cuando el paciente acepta pagar.
</TU_TRABAJO>

<DERIVACIONES>
PACIENTE ACEPTA PAGAR → iniciar_cobranzas (pasás día, hora y profesional)
TEMA SE DESVÍA → volver_secretaria_principal
EMERGENCIA O CRISIS → notificar_walter_urgente (es_emergencia: true)
</DERIVACIONES>

<EMERGENCIA>
Si detectás crisis o urgencia emocional:
1. Cortá el flujo.
2. "Entiendo que estás pasando por un momento muy difícil. Voy a conectarte con alguien ahora. Si es urgente, llamá al 135 o dirigite a la guardia más cercana."
3. Llamá notificar_walter_urgente con es_emergencia: true.
</EMERGENCIA>"""

# ===================================================================
# TOOLS
# ===================================================================
TOOLS_AGENDADORA = [
    {
        "name": "consultar_calendar",
        "description": "Consulta la disponibilidad real en Google Calendar. Siempre usala antes de proponer horarios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "texto_fecha": {
                    "type": "string",
                    "description": "Fecha en lenguaje natural, por ejemplo 'mañana', 'el lunes', '16 de mayo', 'pasado mañana a las 10'."
                },
                "dias_a_consultar": {
                    "type": "integer",
                    "description": "Cuántos días consultar desde la fecha indicada (default: 1 si se da fecha concreta, 3 si es abierto)."
                }
            },
            "required": ["texto_fecha"]
        }
    },
    {
        "name": "iniciar_cobranzas",
        "description": "Deriva a cobranzas cuando el paciente acepta pagar. Pasás los datos del turno elegido.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dia":          {"type": "string", "description": "Día del turno (ej. miércoles 16)"},
                "hora":         {"type": "string", "description": "Hora del turno (ej. 15hs)"},
                "profesional":  {"type": "string", "description": "Profesional elegido"},
                "iso_datetime": {"type": "string", "description": "Datetime ISO del slot, tal como aparece entre [ISO:...] en la respuesta del calendario. Usalo siempre que esté disponible para evitar errores de interpretación."}
            },
            "required": ["dia", "hora", "profesional"]
        }
    },
    {
        "name": "volver_secretaria_principal",
        "description": "Devuelve al paciente a la secretaria principal si el tema se desvía del agendamiento.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "notificar_walter_urgente",
        "description": "Notifica a Walter ante emergencias o cuando el paciente se niega a pagar anticipadamente.",
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
# GOOGLE CALENDAR — Service Account (no necesita OAuth interactivo)
# ===================================================================
SCOPES   = ['https://www.googleapis.com/auth/calendar.events',
            'https://www.googleapis.com/auth/calendar.readonly']
TIMEZONE = ZoneInfo('America/Argentina/Buenos_Aires')


def _build_calendar_service():
    sa_json = os.getenv('GOOGLE_SERVICE_ACCOUNT')
    if not sa_json:
        logging.warning("❌ [CALENDAR ERROR]: La variable GOOGLE_SERVICE_ACCOUNT está vacía o no existe en Railway.")
        raise EnvironmentError(
            "Falta la variable de entorno GOOGLE_SERVICE_ACCOUNT. "
            "Pegá el JSON completo de la service account en Railway."
        )
    
    try:
        info = json.loads(sa_json)
    except json.JSONDecodeError as e:
        logging.warning(f"❌ [CALENDAR ERROR]: El JSON de la Service Account tiene un error de formato: {e}")
        logging.warning(f"Contenido recibido (primeros 50 caracteres): {sa_json[:50]}...")
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


def _parse_fecha_hora(texto: str) -> tuple[datetime, datetime] | tuple[None, None]:
    if not texto:
        return None, None

    t = texto.lower()
    t = t.replace('pasado mañana', '__pasadomañana__')
    t = t.replace(' de la mañana', ' am').replace(' de la tarde', ' pm').replace(' de la noche', ' pm')
    t = t.replace(' a las ', ' ').replace('hs', '').replace('horas', '')
    t = _normalize(t)

    hoy  = datetime.now(TIMEZONE).date()
    fecha = None

    if '__pasadomañana__' in t or 'pasado manana' in t:
        fecha = hoy + timedelta(days=2)
    elif 'mañana' in t:
        fecha = hoy + timedelta(days=1)
    elif 'hoy' in t:
        fecha = hoy
    else:
        dias_semana = {'lunes': 0, 'martes': 1, 'miercoles': 2, 'miércoles': 2,
                       'jueves': 3, 'viernes': 4, 'sabado': 5, 'sábado': 5, 'domingo': 6}
        for nombre, target in dias_semana.items():
            if nombre in t:
                delta = (target - hoy.weekday()) % 7 or 7
                fecha = hoy + timedelta(days=delta)
                break

    if fecha is None:
        m = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', t)
        if m:
            dia, mes = int(m.group(1)), int(m.group(2))
            anio = int(m.group(3)) if m.group(3) else hoy.year
            if anio < 100: anio += 2000
            try: fecha = datetime(anio, mes, dia).date()
            except ValueError: pass

    if fecha is None:
        meses = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                 'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
        for nombre, mes in meses.items():
            if nombre in t:
                dm = re.search(r'(\d{1,2})', t)
                if dm:
                    try:
                        fecha = datetime(hoy.year, mes, int(dm.group(1))).date()
                        if fecha < hoy: fecha = datetime(hoy.year + 1, mes, int(dm.group(1))).date()
                    except ValueError: pass
                break

    if fecha is None:
        return None, None

    hora, minuto = 10, 0

    # Buscar HH:MM explícito primero
    hm_explicito = re.search(r'\b(\d{1,2}):(\d{2})\b', t)
    if hm_explicito:
        hora   = int(hm_explicito.group(1))
        minuto = int(hm_explicito.group(2))
        if hora < 7: hora += 12
    else:
        # Buscar TODOS los números sueltos y tomar el ÚLTIMO.
        # El número del día aparece primero ("miércoles 16 15hs" → [16, 15]),
        # la hora siempre es el último número en el string.
        todos = list(re.finditer(r'\b(\d{1,2})\b', t))
        if todos:
            ultimo = int(todos[-1].group(1))
            if 1 <= ultimo <= 22:  # validar rango razonable de hora
                hora = ultimo
                if hora < 7: hora += 12

    start = datetime(fecha.year, fecha.month, fecha.day, hora, minuto, tzinfo=TIMEZONE)
    return start, start + timedelta(hours=1)


def _consultar_calendar(texto_fecha: str, dias: int = 3) -> str:
    """Devuelve texto con los horarios disponibles para mostrarle a Claude."""
    try:
        start, _ = _parse_fecha_hora(texto_fecha)
        if start is None:
            # Si no se pudo parsear, consultamos los próximos 3 días desde hoy
            fecha_inicio = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            fecha_inicio = start.replace(hour=0, minute=0, second=0, microsecond=0)

        service  = _build_calendar_service()
        time_min = fecha_inicio
        time_max = fecha_inicio + timedelta(days=dias)
        
        calendar_id = os.getenv("CALENDAR_ID", "primary")

        busy_raw = service.freebusy().query(body={
            'timeMin':  time_min.isoformat(),
            'timeMax':  time_max.isoformat(),
            'timeZone': str(TIMEZONE),
            'items':    [{'id': calendar_id}]
        }).execute()['calendars'][calendar_id]['busy']

        busy_ranges = []
        for item in busy_raw:
            s = datetime.fromisoformat(item['start'].replace('Z', '+00:00')).astimezone(TIMEZONE)
            e = datetime.fromisoformat(item['end'].replace('Z', '+00:00')).astimezone(TIMEZONE)
            busy_ranges.append((s, e))

        # Generar slots disponibles de 1 hora entre 9 y 18
        # IMPORTANTE: el cursor avanza de 1 HORA en 1 HORA (no de 30 min)
        # para que cada slot mostrado sea exactamente el bloque que se va a reservar.
        slots = []
        for day_offset in range(dias):
            day = fecha_inicio + timedelta(days=day_offset)
            cursor = day.replace(hour=9, minute=0)
            end_of_day = day.replace(hour=18, minute=0)
            while cursor + timedelta(hours=1) <= end_of_day and len(slots) < 6:
                cend    = cursor + timedelta(hours=1)
                overlap = any(not (cend <= bs or cursor >= be) for bs, be in busy_ranges)
                if not overlap:
                    slots.append(cursor)
                cursor += timedelta(hours=1)  # avance de 1h, no de 30 min

        if not slots:
            return f"No hay turnos disponibles en los próximos {dias} días desde {texto_fecha}. ¿Querés que busque en otra fecha?"

        lineas = []
        for slot in slots[:4]:
            label = slot.strftime('%A %d/%m a las %H:%M').capitalize()
            iso   = slot.isoformat()          # ej: 2025-05-16T15:00:00-03:00
            lineas.append(f"- {label} [ISO:{iso}]")
        return "Turnos disponibles:\n" + "\n".join(lineas)

    except Exception as e:
        logging.warning(f"[❌ CALENDAR ERROR]: {e}")
        import traceback; traceback.print_exc()
        return "Hubo un problema consultando la agenda. ¿Podés intentar con otra fecha?"


def _is_busy(service, start: datetime, end: datetime) -> bool:
    calendar_id = os.getenv("CALENDAR_ID", "primary")
    busy = service.freebusy().query(body={
        'timeMin':  start.isoformat(),
        'timeMax':  end.isoformat(),
        'timeZone': str(TIMEZONE),
        'items':    [{'id': calendar_id}]
    }).execute()['calendars'][calendar_id]['busy']
    return len(busy) > 0


def _crear_evento(service, titulo: str, start: datetime, end: datetime, descripcion: str = '') -> str:
    logging.warning(f"📅 [CALENDAR INFO] Intentando crear evento: '{titulo}' desde {start} hasta {end}")
    event  = {
        'summary':     titulo,
        'description': descripcion,
        'start': {'dateTime': start.isoformat(), 'timeZone': str(TIMEZONE)},
        'end':   {'dateTime': end.isoformat(),   'timeZone': str(TIMEZONE)}
    }
    try:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
        logging.warning(f"📅 [CALENDAR INFO] Usando calendarId: {calendar_id}")
        creado = service.events().insert(calendarId=calendar_id, body=event).execute()
        enlace = creado.get('htmlLink', '')
        logging.warning(f"✅ [CALENDAR SUCCESS] Evento creado con éxito: {enlace}")
        return enlace
    except Exception as e:
        logging.warning(f"❌ [CALENDAR ERROR]: Falló la creación del evento en Google Calendar: {e}")
        raise


# ===================================================================
# FUNCIÓN PRINCIPAL
# ===================================================================
async def secretaria_agendadora(user_text: str, to_number: str, msg_id: str = None):
    """
    Loop correcto: Claude llama tool → ejecutamos → devolvemos tool_result a Claude
    → Claude formula la respuesta final para el usuario.
    """
    import logging
    logging.warning(f"🚀🚀🚀 [AGENDADORA EJECUTADA] Recibimos mensaje de {to_number}: '{user_text}' 🚀🚀🚀")
    logging.warning(f"🚀 [INIT AGENDADORA] Entrando a la función para el número {to_number}")
    logging.warning(f"💬 [INIT AGENDADORA] Mensaje recibido: {user_text}")
    
    try:
        await marcar_leido_wpp(msg_id)
        logging.warning(f"✅ [INIT AGENDADORA] Mensaje marcado como leído.")
    except Exception as e:
        logging.warning(f"⚠️ [INIT AGENDADORA] Falló marcar_leido_wpp: {e}")

    db = SessionLocal()
    try:
        logging.warning(f"🔍 [INIT AGENDADORA] Buscando cliente en DB...")
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
            logging.warning(f"[⚠️ AGENDADORA] Cliente {to_number} no encontrado. Creándolo automáticamente...")
            from init_db import EMPRESA_DEFAULT_ID
            cliente = Cliente(telefono=to_number, empresa_id=EMPRESA_DEFAULT_ID, estado_agente="agendadora")
            db.add(cliente)
            db.commit()
            db.refresh(cliente)

        cliente.mensajes_enviados += 1
        logging.warning(f"\n[AGENDADORA - {to_number}]: {user_text}")

        # Historial reciente (últimas 6 horas, máx 20 mensajes)
        hace_6h = datetime.utcnow() - timedelta(hours=6)
        mensajes_recientes = (
            db.query(Mensaje)
            .filter(Mensaje.cliente_id == cliente.id, Mensaje.fecha_creacion >= hace_6h)
            .order_by(Mensaje.fecha_creacion.desc())
            .limit(20)
            .all()
        )

        historial = []
        for m in reversed(mensajes_recientes):
            historial.append({
                "role":    "user" if m.rol == "usuario" else "assistant",
                "content": m.texto
            })
        historial.append({"role": "user", "content": user_text})

        db.add(Mensaje(cliente_id=cliente.id, rol="usuario", texto=user_text))

        nombre       = (cliente.datos_extraidos or {}).get("nombre_contacto", "")
        system_final = SYSTEM_PROMPT_AGENDADORA + (f"\nEl cliente se llama {nombre}." if nombre else "")

        # ---------------------------------------------------------------
        # Loop de herramientas: Claude puede llamar varias tools seguidas
        # ---------------------------------------------------------------
        MAX_ITERACIONES = 5  # evitar loops infinitos
        for _ in range(MAX_ITERACIONES):
            response = client_claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                system=system_final,
                tools=TOOLS_AGENDADORA,
                messages=historial
            )

            # Separar bloques de texto y de tool_use
            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            # Si Claude respondió con texto Y sin tools → respuesta final
            if texto_bloques and not tool_bloques:
                texto_respuesta = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[AGENDADORA]: {texto_respuesta}")
                await enviar_mensaje_wpp(to_number, texto_respuesta)
                db.add(Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_respuesta))
                break

            # Si hay texto junto con tools, enviarlo antes de procesar
            if texto_bloques:
                texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                await enviar_mensaje_wpp(to_number, texto_previo)
                db.add(Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_previo))

            if not tool_bloques:
                # Claude paró sin texto ni tools (stop_reason != tool_use)
                break

            # Agregar la respuesta de Claude al historial (con los tool_use)
            historial.append({"role": "assistant", "content": response.content})

            # Ejecutar cada tool y construir los tool_result
            tool_results = []
            derivar      = None  # para manejar derivaciones después del loop

            for tool in tool_bloques:
                tool_name  = tool.name
                tool_input = tool.input
                logging.warning(f"[🔧 TOOL]: {tool_name} | input: {tool_input}")

                try:
                    if tool_name == "consultar_calendar":
                        await enviar_mensaje_wpp(to_number, "Reviso la agenda... un momento.")
                        resultado = _consultar_calendar(
                            texto_fecha     = tool_input.get("texto_fecha", "hoy"),
                            dias            = tool_input.get("dias_a_consultar", 3)
                        )
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": tool.id,
                            "content":     resultado
                        })

                    elif tool_name == "iniciar_cobranzas":
                        dia         = tool_input.get("dia", "")
                        hora        = tool_input.get("hora", "")
                        profesional = tool_input.get("profesional", "")
                        iso_dt      = tool_input.get("iso_datetime", "")

                        # Prioridad: usar el ISO exacto devuelto por consultar_calendar
                        if iso_dt:
                            try:
                                start = datetime.fromisoformat(iso_dt).astimezone(TIMEZONE)
                            except ValueError:
                                start = None
                        else:
                            start, _ = _parse_fecha_hora(f"{dia} {hora}")

                        end = (start + timedelta(hours=1)) if start else None

                        if not start or not end:
                            resultado = f"No pude interpretar la fecha '{dia} {hora}'. ¿Podés confirmar día y hora exactos?"
                        else:
                            service = _build_calendar_service()
                            if _is_busy(service, start, end):
                                resultado = "Ese horario ya está ocupado. Voy a buscar otra alternativa."
                            else:
                                descripcion = f"Turno Abriness con {profesional}. Paciente: {cliente.nombre_completo or to_number}."
                                enlace      = _crear_evento(service, f"Turno Abriness - {profesional}", start, end, descripcion)
                                resultado   = f"Turno reservado el {start.strftime('%A %d/%m a las %H:%M')} con {profesional}."
                                if enlace:
                                    resultado += f" Link: {enlace}"
                                derivar = "cobranzas"

                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": tool.id,
                            "content":     resultado
                        })

                    elif tool_name == "volver_secretaria_principal":
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": tool.id,
                            "content":     "Derivando al paciente a la secretaria principal."
                        })
                        derivar = "principal"

                    elif tool_name == "notificar_walter_urgente":
                        es_emergencia = tool_input.get("es_emergencia", False)
                        from services.secretaria_principal import enviar_notificacion_a_walter
                        nombre_cliente = cliente.nombre_completo or nombre or to_number
                        await enviar_notificacion_a_walter(to_number, nombre_cliente)
                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": tool.id,
                            "content":     f"Walter notificado. Emergencia: {es_emergencia}."
                        })
                        derivar = "manual"

                except Exception as e:
                    logging.warning(f"[❌ ERROR EN TOOL {tool_name}]: {e}")
                    import traceback; traceback.print_exc()
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": tool.id,
                        "content":     "Error al ejecutar la herramienta."
                    })

            # Agregar los resultados de las tools al historial para la próxima iteración
            historial.append({"role": "user", "content": tool_results})

            # Si hubo derivación, actualizar estado y salir del loop
            if derivar:
                if derivar == "cobranzas":
                    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
<<<<<<< HEAD
                    next_state = await iniciar_cobranzas_svc(
                        to_number,
                        especialidad=profesional,
                        detalle_turno=resultado,
                    )
                    cliente.estado_agente = next_state
=======
                    await iniciar_cobranzas_svc(to_number)
                    cliente.estado_agente = "manual"
>>>>>>> 9c37aa49dbc43a033a959b2656006c45bced58d8
                elif derivar == "principal":
                    cliente.estado_agente = "principal"
                elif derivar == "manual":
                    cliente.estado_agente = "manual"
                db.commit()
                break

        db.commit()
        logging.warning(f"[✅ AGENDADORA] Proceso completado para {to_number}")

    except Exception as e:
        logging.warning(f"\n[❌ ERROR AGENDADORA]: {e}")
        import traceback; traceback.print_exc()
        try:
            await enviar_mensaje_wpp(to_number, "Perdón, hubo un problema revisando la agenda. ¿Podés decirme otra fecha o horario?")
        except Exception:
            pass
    finally:
        db.close()
