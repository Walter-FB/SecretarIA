import logging
import os
import re
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE      = ZoneInfo('America/Argentina/Buenos_Aires')
HORA_APERTURA = 9
HORA_CIERRE   = 18

SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly',
]


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
        'lunes': 0, 'martes': 1,
        'miercoles': 2, 'miércoles': 2,
        'jueves': 3,
        'viernes': 4,
        'sabado': 5, 'sábado': 5,
        'domingo': 6,
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


def _parse_hora(texto: str):
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
    hora_pedida,
) -> list:
    MAX_SLOTS = 45
    slots  = []
    cursor = desde

    while cursor < hasta and len(slots) < MAX_SLOTS:
        dia = cursor.date()

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


def _formatear_slots(slots: list) -> str:
    """Agrupa slots por día, muestra hasta 5 por día con 'etc' si hay más."""
    from collections import defaultdict
    by_day = defaultdict(list)
    for s in slots:
        by_day[s.date()].append(s)
    lineas = []
    for day in sorted(by_day):
        day_slots = by_day[day]
        for s in day_slots[:5]:
            lineas.append(f"- {s.strftime('%A %d/%m a las %H:%M').capitalize()} [ISO:{s.isoformat()}]")
        if len(day_slots) > 5:
            lineas.append("  (etc.)")
    return "Turnos disponibles:\n" + "\n".join(lineas)


def _consultar_calendar_local(texto_fecha: str, dias: int = 1, profesional_id: str = None, db=None) -> str:
    fecha       = _parse_fecha(texto_fecha)
    hora_pedida = _parse_hora(texto_fecha)

    if fecha is None:
        return "No entendí la fecha. Podés decirme el día?"

    ahora        = datetime.now(TIMEZONE)
    fecha_inicio = datetime(fecha.year, fecha.month, fecha.day, HORA_APERTURA, 0, tzinfo=TIMEZONE)
    # Si la fecha de inicio es en el pasado, adelantar a ahora (redondeado a la hora siguiente)
    if fecha_inicio < ahora:
        fecha_inicio = ahora.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    time_max     = datetime(fecha.year, fecha.month, fecha.day, HORA_APERTURA, 0, tzinfo=TIMEZONE) + timedelta(days=max(dias, 1))
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
    return _formatear_slots(slots)


def _consultar_calendar(texto_fecha: str, dias: int = 1, calendar_id: str = None) -> str:
    if not calendar_id:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
    try:
        fecha       = _parse_fecha(texto_fecha)
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

        return _formatear_slots(slots)

    except Exception as e:
        logging.warning(f"[❌ CALENDAR ERROR]: {e}")
        import traceback; traceback.print_exc()
        return "Hubo un problema consultando la agenda. Podés intentar con otra fecha?"


def _build_calendar_service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
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
