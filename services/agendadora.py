# ===================================================================
# SECRETARIA AGENDADORA
# Contexto limpio. Solo coordina día y hora con Google Calendar.
# Cuando termina, devuelve el control a la Principal.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje
from services.secretaria_principal import enviar_mensaje_wpp, marcar_leido_wpp, client_claude
import json
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import os.path
import os
import re
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

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
                "texto_fecha": {"type": "string", "description": "Fecha en lenguaje natural, por ejemplo 'mañana a las 10'"},
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
# GOOGLE CALENDAR / AUTH HELPERS
# ===================================================================

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SCOPES = ['https://www.googleapis.com/auth/calendar.events']
TIMEZONE = ZoneInfo('America/Argentina/Buenos_Aires')


def _get_path(env_name: str, default_name: str) -> str:
    return os.getenv(env_name, os.path.join(BASE_DIR, default_name))


def _load_google_credentials() -> Credentials:
    token_path = _get_path('GOOGLE_TOKEN_JSON', 'token.json')
    creds_path = _get_path('GOOGLE_CREDENTIALS_JSON', 'credentials.json')
    google_credentials_env = os.getenv('GOOGLE_CREDENTIALS')

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = None
            if google_credentials_env:
                try:
                    config_data = json.loads(google_credentials_env)
                    client_config = config_data
                except json.JSONDecodeError as e:
                    raise ValueError(f"No se pudo parsear GOOGLE_CREDENTIALS: {e}")

            if client_config is not None:
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            else:
                if not os.path.exists(creds_path):
                    raise FileNotFoundError(f"No se encontró el archivo de credenciales: {creds_path}")
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)

            creds = flow.run_local_server(port=8080)

        with open(token_path, 'w', encoding='utf-8') as token_file:
            token_file.write(creds.to_json())

    return creds


def _build_calendar_service():
    creds = _load_google_credentials()
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)


def _parse_iso_datetime(value: str) -> datetime:
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    return datetime.fromisoformat(value)


def _normalize_text(text: str) -> str:
    return re.sub(r'[^a-z0-9áéíóúüñ\s/\-:\.]', ' ', text.lower())


def _parse_fecha_hora(texto: str, timezone: ZoneInfo = TIMEZONE) -> tuple[datetime, datetime] | tuple[None, None]:
    if not texto:
        return None, None

    texto = texto.lower().replace('pasado manana', 'pasado mañana').replace('pasado mañana', 'pasado mañana').replace('hoy', 'hoy')
    texto = texto.replace(' a las ', ' ').replace(' hs', ' ').replace(' horas', ' ')
    texto = _normalize_text(texto)

    hoy = datetime.now(timezone).date()
    fecha = None

    if 'pasado manana' in texto or 'pasado mañana' in texto:
        fecha = hoy + timedelta(days=2)
    elif 'mañana' in texto:
        fecha = hoy + timedelta(days=1)
    elif 'hoy' in texto:
        fecha = hoy
    else:
        weekdays = {
            'lunes': 0, 'martes': 1, 'miercoles': 2, 'jueves': 3,
            'viernes': 4, 'sabado': 5, 'domingo': 6
        }
        for nombre, target in weekdays.items():
            if nombre in texto:
                hoy_weekday = hoy.weekday()
                delta = (target - hoy_weekday) % 7
                if delta == 0:
                    delta = 7
                fecha = hoy + timedelta(days=delta)
                break

    if fecha is None:
        match = re.search(r'(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?', texto)
        if match:
            dia = int(match.group(1))
            mes = int(match.group(2))
            anio = int(match.group(3)) if match.group(3) else hoy.year
            if anio < 100:
                anio += 2000
            try:
                fecha = datetime(anio, mes, dia).date()
            except ValueError:
                fecha = None

    if fecha is None:
        meses = {
            'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
            'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
            'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12
        }
        for nombre, mes in meses.items():
            if nombre in texto:
                dia_match = re.search(r'(\d{1,2})', texto)
                if dia_match:
                    dia = int(dia_match.group(1))
                    try:
                        fecha = datetime(hoy.year, mes, dia).date()
                    except ValueError:
                        fecha = None
                        continue
                    if fecha < hoy:
                        fecha = datetime(hoy.year + 1, mes, dia).date()
                break

    if fecha is None:
        return None, None

    hora = 10
    minuto = 0
    hora_match = re.search(r'(\d{1,2})(?::(\d{2}))?', texto)
    if hora_match:
        hora = int(hora_match.group(1))
        minuto = int(hora_match.group(2)) if hora_match.group(2) else 0
        if hora < 7:
            hora += 12

    start = datetime(fecha.year, fecha.month, fecha.day, hora, minuto, tzinfo=timezone)
    end = start + timedelta(hours=1)
    return start, end


def _is_busy(service, start: datetime, end: datetime) -> bool:
    query = {
        'timeMin': start.isoformat(),
        'timeMax': end.isoformat(),
        'timeZone': str(TIMEZONE),
        'items': [{'id': 'primary'}]
    }
    busy = service.freebusy().query(body=query).execute()['calendars']['primary']['busy']
    return len(busy) > 0


def _format_day(dt: datetime) -> str:
    return dt.strftime('%A %d/%m').capitalize()


def _format_slot(dt: datetime) -> str:
    return dt.strftime('%A %d/%m a las %H:%M').capitalize()


def _build_available_slots(busy_ranges: list[tuple[datetime, datetime]], start_date: datetime, days: int = 3) -> list[datetime]:
    slots = []
    for day_offset in range(days):
        day = (start_date + timedelta(days=day_offset)).date()
        cursor = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TIMEZONE)
        end_of_day = datetime(day.year, day.month, day.day, 18, 0, tzinfo=TIMEZONE)
        while cursor + timedelta(hours=1) <= end_of_day and len(slots) < 6:
            candidate_end = cursor + timedelta(hours=1)
            overlap = any(not (candidate_end <= busy_start or cursor >= busy_end) for busy_start, busy_end in busy_ranges)
            if not overlap:
                slots.append(cursor)
            cursor += timedelta(minutes=30)
    return slots


def _consultar_calendar(fecha_desde: str, dias: int = 3) -> str:
    if not fecha_desde:
        fecha_desde = datetime.now(TIMEZONE).strftime('%Y-%m-%d')
    try:
        fecha_inicio = datetime.fromisoformat(fecha_desde).date()
    except ValueError:
        fecha_inicio = datetime.now(TIMEZONE).date()

    service = _build_calendar_service()
    time_min = datetime(fecha_inicio.year, fecha_inicio.month, fecha_inicio.day, 0, 0, tzinfo=TIMEZONE)
    time_max = time_min + timedelta(days=dias)
    query = {
        'timeMin': time_min.isoformat(),
        'timeMax': time_max.isoformat(),
        'timeZone': str(TIMEZONE),
        'items': [{'id': 'primary'}]
    }
    busy = service.freebusy().query(body=query).execute()['calendars']['primary']['busy']
    busy_ranges = []
    for item in busy:
        start = _parse_iso_datetime(item['start']).astimezone(TIMEZONE)
        end = _parse_iso_datetime(item['end']).astimezone(TIMEZONE)
        busy_ranges.append((start, end))

    available_slots = _build_available_slots(busy_ranges, time_min, dias)
    if not available_slots:
        return f"No hay turnos disponibles en Google Calendar desde {fecha_desde} por {dias} días."

    opciones = available_slots[:3]
    lineas = [f"- {slot.strftime('%A %d/%m a las %H:%M')}" for slot in opciones]
    return "Disponibilidad real en Google Calendar:\n" + "\n".join(lineas)


def _crear_evento_calendar(titulo: str, start: datetime, end: datetime, descripcion: str = '') -> str:
    service = _build_calendar_service()
    event = {
        'summary': titulo,
        'description': descripcion,
        'start': {'dateTime': start.isoformat(), 'timeZone': str(TIMEZONE)},
        'end': {'dateTime': end.isoformat(), 'timeZone': str(TIMEZONE)}
    }
    creado = service.events().insert(calendarId='primary', body=event).execute()
    return creado.get('htmlLink', '')


def _build_nome_from_cliente(cliente: Cliente) -> str:
    return cliente.nombre_completo or cliente.telefono or 'Paciente'


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
        
        hace_6_horas = datetime.utcnow() - timedelta(hours=6)
        mensajes_recientes = db.query(Mensaje).filter(
            Mensaje.cliente_id == cliente.id,
            Mensaje.fecha_creacion >= hace_6_horas
        ).order_by(Mensaje.fecha_creacion.desc()).limit(20).all()
        
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
                fecha_desde = tool["input"].get("fecha_desde", datetime.utcnow().strftime("%Y-%m-%d"))
                dias = tool["input"].get("dias_a_consultar", 3)
                resultado = _consultar_calendar(fecha_desde, dias)
                await enviar_mensaje_wpp(to_number, resultado)
            elif tool["name"] == "iniciar_cobranzas":
                dia = tool["input"].get("dia", "")
                hora = tool["input"].get("hora", "")
                profesional = tool["input"].get("profesional", "")
                turno_texto = f"{dia} {hora}".strip()
                start, end = _parse_fecha_hora(turno_texto)
                if not start or not end:
                    await enviar_mensaje_wpp(to_number, f"No pude interpretar fecha y hora: '{turno_texto}'. ¿Podés escribir la fecha y hora exactas para confirmar el turno?")
                    cliente.estado_agente = "agendadora"
                    db.commit()
                    continue

                service = _build_calendar_service()
                if _is_busy(service, start, end):
                    await enviar_mensaje_wpp(to_number, "Ese horario ya está ocupado en Google Calendar. Voy a buscar otra alternativa.")
                    cliente.estado_agente = "agendadora"
                    db.commit()
                    continue

                descripcion = f"Turno Abriness con {profesional}. Paciente: {cliente.nombre_completo or to_number}."
                enlace = _crear_evento_calendar(
                    f"Turno Abriness - {profesional}",
                    start,
                    end,
                    descripcion
                )
                confirmacion = f"Perfecto, ya reservé tu turno para {start.strftime('%A %d/%m a las %H:%M')} con {profesional}."
                if enlace:
                    confirmacion += f"\nLo registré en Google Calendar: {enlace}"
                await enviar_mensaje_wpp(to_number, confirmacion)

                print(f"[💸 AGENDADORA] Turno reservado {start} - {end} con {profesional}. Derivando a cobranzas...")
                from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
                await iniciar_cobranzas_svc(to_number)
                cliente.estado_agente = "manual"
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
