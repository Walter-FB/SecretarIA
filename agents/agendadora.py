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


def _consultar_calendar(texto_fecha: str, dias: int = 3, calendar_id: str = None) -> str:
    """Devuelve texto con los horarios disponibles para mostrarle a Claude."""
    if not calendar_id:
        calendar_id = os.getenv("CALENDAR_ID", "primary")
    try:
        start, _ = _parse_fecha_hora(texto_fecha)
        if start is None:
            fecha_inicio = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            fecha_inicio = start.replace(hour=0, minute=0, second=0, microsecond=0)

        service  = _build_calendar_service()
        time_min = fecha_inicio
        time_max = fecha_inicio + timedelta(days=dias)

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
# FUNCIÓN PRINCIPAL
# ===================================================================
async def secretaria_agendadora(user_text: str, to_number: str, msg_id: str = None, empresa_id: str = None):
    """Loop: Claude llama tool → ejecutamos → devolvemos tool_result → Claude responde."""
    try:
        await marcar_leido_wpp(msg_id)
    except Exception as e:
        logging.warning(f"[AGENDADORA] Fallo marcar_leido_wpp: {e}")

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

        calendar_id = (empresa.calendar_id if empresa and empresa.calendar_id
                       else os.getenv("CALENDAR_ID", "primary"))

        # ── Cargar / crear cliente ──────────────────────────────────
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

        db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="usuario", texto=user_text))

        nombre       = cliente.nombre_completo or (cliente.datos_extraidos or {}).get("nombre_contacto", "")
        system_final = SYSTEM_PROMPT_AGENDADORA + (f"\nEl cliente se llama {nombre}." if nombre else "")

        definitions, handlers = get_tools_for_agendadora(empresa)

        # ---------------------------------------------------------------
        # Loop de herramientas: Claude puede llamar varias tools seguidas
        # ---------------------------------------------------------------
        MAX_ITERACIONES = 5  # evitar loops infinitos
        for _ in range(MAX_ITERACIONES):
            response = client_claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=400,
                system=system_final,
                tools=definitions,
                messages=historial
            )

            # Separar bloques de texto y de tool_use
            texto_bloques = [b for b in response.content if b.type == "text"]
            tool_bloques  = [b for b in response.content if b.type == "tool_use"]

            if texto_bloques and not tool_bloques:
                texto_respuesta = " ".join(b.text.strip() for b in texto_bloques)
                logging.warning(f"[AGENDADORA] Respuesta final: {texto_respuesta}")
                await enviar_mensaje_wpp(to_number, texto_respuesta)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", texto=texto_respuesta))
                break

            if texto_bloques:
                texto_previo = " ".join(b.text.strip() for b in texto_bloques)
                await enviar_mensaje_wpp(to_number, texto_previo)
                db.add(Mensaje(cliente_id=cliente.id, empresa_id=empresa_id, rol="asistente", texto=texto_previo))

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
                if derivar_tool:
                    derivar = derivar_tool

            historial.append({"role": "user", "content": tool_results})

            if derivar:
                cliente.estado_agente = derivar
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
