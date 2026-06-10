import logging
from datetime import timedelta

# ──────────────────────────────────────────────────────────────────────────────
# NOTA SOBRE CONTEXTOS:
# Esta tool tiene dos usos distintos con schemas distintos:
#
# Contexto A — secretaria_principal (consulta de precio sin turno):
#   Input: {"especialidad": "psicología", "cobertura": "OSDE"}
#   Handler: llama a iniciar_cobranzas_svc() directamente, sin crear evento de calendar.
#   DEFINITION usada en secretaria_principal: DEFINITION_PRECIO (abajo)
#
# Contexto B — agendadora (turno ya elegido, crear evento + cobrar):
#   Input: {"dia": "...", "hora": "...", "profesional": "...", "iso_datetime": "..."}
#   Handler: parsea datetime, crea evento en Google Calendar, luego llama a cobranza.
#   DEFINITION usada en agendadora: DEFINITION (abajo)
#
# El registry expone DEFINITION (agendadora) por defecto.
# Cuando los services se conecten al catálogo, el agente correcto usará la definición correcta.
# ──────────────────────────────────────────────────────────────────────────────

# Definición para la agendadora (turno confirmado)
DEFINITION = {
    "name": "iniciar_cobranzas",
    "description": "Deriva a cobranzas cuando el paciente acepta pagar. Pasás los datos del turno elegido.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dia":          {"type": "string", "description": "Día del turno (ej. miércoles 16)"},
            "hora":         {"type": "string", "description": "Hora del turno (ej. 15hs)"},
            "profesional":  {"type": "string", "description": "Profesional elegido"},
            "iso_datetime": {
                "type": "string",
                "description": "Datetime ISO del slot, tal como aparece entre [ISO:...] en la respuesta del calendario."
            }
        },
        "required": ["dia", "hora", "profesional"]
    }
}

# Definición para la secretaria principal (fallback: turno ya confirmado pero cobranza no se completó)
DEFINITION_PRECIO = {
    "name": "iniciar_cobranzas",
    "description": (
        "Usá SOLO si el paciente ya tiene turno confirmado pero nunca recibió las instrucciones de pago "
        "(la agendadora no completó el flujo). "
        "Para consultas de precio usá consultar_precio en su lugar."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "especialidad": {"type": "string", "enum": ["psicología", "psiquiatría"]},
            "cobertura":    {"type": "string"}
        }
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Handler para el contexto de agendadora: parsea el datetime, crea el evento en calendar,
    luego lanza cobranzas.
    Retorna (content, "cobranzas").
    """
    from agents.agendadora import (
        _parse_fecha, _parse_hora, _build_calendar_service, _is_busy, _crear_evento
    )
    from zoneinfo import ZoneInfo
    from datetime import datetime
    import os

    dia         = tool_input.get("dia", "")
    hora        = tool_input.get("hora", "")
    profesional = tool_input.get("profesional", "")
    iso_dt      = tool_input.get("iso_datetime", "")
    TIMEZONE    = ZoneInfo('America/Argentina/Buenos_Aires')

    nombre_clinica = empresa.nombre if empresa else "Clínica"
    empresa_id     = empresa.id if empresa else None

    if iso_dt:
        try:
            dt = datetime.fromisoformat(iso_dt)
            # Claude a veces elimina el offset "-03:00" del ISO generado por consultar_calendar.
            # Si llega naive, ya es hora argentina — no convertir desde UTC.
            start = dt.replace(tzinfo=TIMEZONE) if dt.tzinfo is None else dt.astimezone(TIMEZONE)
        except ValueError:
            start = None
    else:
        texto = f"{dia} {hora}"
        fecha = _parse_fecha(texto)
        h     = _parse_hora(texto)
        start = datetime(fecha.year, fecha.month, fecha.day,
                         h if h is not None else 10, 0, tzinfo=TIMEZONE) if fecha else None

    end = (start + timedelta(hours=1)) if start else None

    if not start or not end:
        return f"No pude interpretar la fecha '{dia} {hora}'. ¿Podés confirmar día y hora exactos?", None

    # Resolver el profesional del turno actual.
    # Primero buscamos por nombre del tool input (fuente más precisa para este turno);
    # solo si no se encuentra, usamos el profesional_id que ya tenía el cliente.
    from models import Profesional as ProfModel
    from services.profesionales import get_profesional_by_nombre
    profesional_obj = None
    if profesional:
        profesional_obj = get_profesional_by_nombre(session, profesional, empresa_id)
    if not profesional_obj and cliente and cliente.profesional_id:
        profesional_obj = session.query(ProfModel).filter(ProfModel.id == cliente.profesional_id).first()

    # Actualizar profesional_id del cliente al recién confirmado (para sesiones futuras)
    if profesional_obj and cliente and cliente.profesional_id != profesional_obj.id:
        cliente.profesional_id = profesional_obj.id
        session.commit()

    usar_local = bool(profesional_obj and profesional_obj.calendar_id is None)

    if usar_local:
        from models import Turno
        from sqlalchemy.exc import IntegrityError
        start_naive = start.replace(tzinfo=None)
        end_naive   = end.replace(tzinfo=None)

        ocupado = session.query(Turno).filter(
            Turno.profesional_id    == profesional_obj.id,
            Turno.fecha_hora_inicio == start_naive,
            Turno.estado            == "reservado",
        ).first()
        if ocupado:
            return "Ese horario se acaba de ocupar. Voy a buscar otra alternativa.", None

        try:
            session.add(Turno(
                profesional_id    = profesional_obj.id,
                cliente_id        = cliente.id,
                empresa_id        = empresa_id,
                fecha_hora_inicio = start_naive,
                fecha_hora_fin    = end_naive,
                estado            = "reservado",
            ))
            session.commit()
            content = f"Turno reservado el {start.strftime('%A %d/%m a las %H:%M')} con {profesional_obj.nombre}."
        except IntegrityError:
            session.rollback()
            return "Ese horario se acaba de ocupar. Voy a buscar otra alternativa.", None
    else:
        # Motor Calendar
        if profesional_obj and profesional_obj.calendar_id and profesional_obj.calendar_id != "empresa":
            calendar_id = profesional_obj.calendar_id
        else:
            calendar_id = (empresa.calendar_id if empresa and empresa.calendar_id
                           else os.getenv("CALENDAR_ID", "primary"))
        try:
            service = _build_calendar_service()
            if _is_busy(service, start, end, calendar_id):
                return "Ese horario ya está ocupado. Voy a buscar otra alternativa.", None

            prof_nombre = profesional_obj.nombre if profesional_obj else profesional
            descripcion = f"Turno {nombre_clinica} con {prof_nombre}. Paciente: {cliente.nombre_completo or cliente.telefono}."
            enlace      = _crear_evento(service, f"Turno {nombre_clinica} - {prof_nombre}", start, end, descripcion, calendar_id)
            content     = f"Turno reservado el {start.strftime('%A %d/%m a las %H:%M')} con {prof_nombre}."
            if enlace:
                content += f" Link: {enlace}"
        except Exception as e:
            logging.warning(f"[TOOL iniciar_cobranzas ERROR calendar]: {e}")
            return "Hubo un problema al reservar el turno. Intentá con otra fecha.", None

    # Iniciar flujo de cobranza
    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
    esp = profesional_obj.nombre if profesional_obj else profesional
    next_state = await iniciar_cobranzas_svc(
        cliente.telefono,
        especialidad   = esp,
        detalle_turno  = content,
        empresa_id     = empresa_id,
    )
    return content, next_state


async def handler_precio(tool_input, cliente, session, empresa, scope=None):
    """
    Handler fallback para secretaria_principal: el turno ya fue confirmado por la agendadora
    pero el flujo de cobranza no se completó. Lee ultimo_turno de datos_extraidos si existe.
    """
    empresa_id    = empresa.id if empresa else None
    datos         = dict(cliente.datos_extraidos or {}) if cliente else {}
    detalle_turno = datos.get("ultimo_turno")

    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
    next_state = await iniciar_cobranzas_svc(
        cliente.telefono,
        especialidad=tool_input.get("especialidad"),
        cobertura=tool_input.get("cobertura"),
        detalle_turno=detalle_turno,
        empresa_id=empresa_id,
    )
    return "Instrucciones de pago enviadas.", next_state
