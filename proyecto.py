import datetime
import os
import re
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar.events']

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'data', 'credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'data', 'token.json')


def _get_calendar_service():
    """Autentica y devuelve el servicio de Google Calendar."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def _proximo_dia_semana(desde: datetime.datetime, dia_semana: int) -> datetime.datetime:
    """Devuelve la próxima fecha del día de semana indicado (0=lunes, 6=domingo)."""
    dias_hasta = (dia_semana - desde.weekday()) % 7
    if dias_hasta == 0:
        dias_hasta = 7
    return desde + datetime.timedelta(days=dias_hasta)


def _parsear_horario(horario_str: str):
    """
    Intenta parsear un string de horario en español a un par (start, end) datetime.
    Si no puede determinar el día u hora con certeza, usa fallbacks razonables.
    """
    ahora = datetime.datetime.now()
    s = horario_str.lower()

    # --- Día ---
    if 'mañana' in s or 'manana' in s:
        base = ahora + datetime.timedelta(days=1)
    elif 'lunes' in s:
        base = _proximo_dia_semana(ahora, 0)
    elif 'martes' in s:
        base = _proximo_dia_semana(ahora, 1)
    elif 'miércoles' in s or 'miercoles' in s:
        base = _proximo_dia_semana(ahora, 2)
    elif 'jueves' in s:
        base = _proximo_dia_semana(ahora, 3)
    elif 'viernes' in s:
        base = _proximo_dia_semana(ahora, 4)
    elif 'sábado' in s or 'sabado' in s:
        base = _proximo_dia_semana(ahora, 5)
    elif 'domingo' in s:
        base = _proximo_dia_semana(ahora, 6)
    else:
        base = ahora + datetime.timedelta(days=1)  # fallback: mañana

    # --- Hora ---
    hora, minuto = 10, 0  # fallback

    match_hhmm = re.search(r'(\d{1,2}):(\d{2})', horario_str)
    if match_hhmm:
        hora = int(match_hhmm.group(1))
        minuto = int(match_hhmm.group(2))
    else:
        match_las = re.search(r'las?\s+(\d{1,2})', s)
        if match_las:
            hora = int(match_las.group(1))
        elif 'tarde' in s:
            hora = 15
        elif 'mediodía' in s or 'mediodia' in s:
            hora = 12
        elif 'mañana' in s or 'manana' in s:
            # "a la mañana" como franja horaria (no el día)
            hora = 10

    start = base.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    end = start + datetime.timedelta(hours=1)
    return start, end


def crear_evento_reunion(
    nombre_cliente: str,
    horario_reunion: str,
    numero_cliente: str = None,
    rubro: str = None
) -> dict:
    """
    Crea un evento en Google Calendar para una reunión coordinada por SecretarIA.

    Args:
        nombre_cliente:  Nombre del cliente (ej: "Juan García")
        horario_reunion: Horario acordado en texto libre (ej: "jueves a las 10:00")
        numero_cliente:  Número de WhatsApp del cliente (opcional)
        rubro:           Rubro/industria del cliente (opcional)

    Returns:
        {'status': 'success', 'link': '...'} o {'status': 'error', 'error': '...'}
    """
    try:
        service = _get_calendar_service()

        partes = [f"Reunión coordinada por SecretarIA con {nombre_cliente}."]
        if numero_cliente:
            partes.append(f"WhatsApp: {numero_cliente}")
        if rubro:
            partes.append(f"Rubro: {rubro}")
        partes.append(f"Horario solicitado: {horario_reunion}")
        descripcion = "\n".join(partes)

        start_dt, end_dt = _parsear_horario(horario_reunion)

        evento = {
            'summary': f'Reunión con {nombre_cliente} — SecretarIA',
            'description': descripcion,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
        }

        creado = service.events().insert(calendarId='primary', body=evento).execute()
        link = creado.get('htmlLink')
        print(f"[✅ CALENDAR] Evento creado para {nombre_cliente}: {link}")
        return {'status': 'success', 'link': link}

    except Exception as e:
        print(f"[❌ CALENDAR ERROR] No se pudo crear el evento: {e}")
        return {'status': 'error', 'error': str(e)}


# -----------------------------------------------------------------------
# Ejecución standalone (para probar manualmente que funcione la auth)
# -----------------------------------------------------------------------
if __name__ == '__main__':
    resultado = crear_evento_reunion(
        nombre_cliente="Cliente Prueba",
        horario_reunion="mañana a las 10:00",
        numero_cliente="5491100000000",
        rubro="Servicios"
    )
    print(resultado)
