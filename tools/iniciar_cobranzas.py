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

# Definición para la secretaria principal (consulta de precio sin turno confirmado)
DEFINITION_PRECIO = {
    "name": "iniciar_cobranzas",
    "description": "Deriva a cobranzas para consultas de precios. Solo si el paciente aceptó ser derivado.",
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
        _parse_fecha_hora, _build_calendar_service, _is_busy, _crear_evento
    )
    from zoneinfo import ZoneInfo
    import os

    dia         = tool_input.get("dia", "")
    hora        = tool_input.get("hora", "")
    profesional = tool_input.get("profesional", "")
    iso_dt      = tool_input.get("iso_datetime", "")
    TIMEZONE    = ZoneInfo('America/Argentina/Buenos_Aires')

    calendar_id = (
        empresa.calendar_id if empresa and empresa.calendar_id
        else os.getenv("CALENDAR_ID", "primary")
    )
    nombre_clinica = empresa.nombre if empresa else "Clínica"
    empresa_id     = empresa.id if empresa else None

    if iso_dt:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(iso_dt)
            # Claude a veces elimina el offset "-03:00" del ISO generado por consultar_calendar.
            # Si llega naive, ya es hora argentina — no convertir desde UTC.
            start = dt.replace(tzinfo=TIMEZONE) if dt.tzinfo is None else dt.astimezone(TIMEZONE)
        except ValueError:
            start = None
    else:
        start, _ = _parse_fecha_hora(f"{dia} {hora}")

    end = (start + timedelta(hours=1)) if start else None

    if not start or not end:
        content = f"No pude interpretar la fecha '{dia} {hora}'. ¿Podés confirmar día y hora exactos?"
        return content, None

    try:
        service = _build_calendar_service()
        if _is_busy(service, start, end, calendar_id):
            return "Ese horario ya está ocupado. Voy a buscar otra alternativa.", None

        descripcion = f"Turno {nombre_clinica} con {profesional}. Paciente: {cliente.nombre_completo or cliente.telefono}."
        enlace      = _crear_evento(service, f"Turno {nombre_clinica} - {profesional}", start, end, descripcion, calendar_id)
        content     = f"Turno reservado el {start.strftime('%A %d/%m a las %H:%M')} con {profesional}."
        if enlace:
            content += f" Link: {enlace}"
    except Exception as e:
        logging.warning(f"[TOOL iniciar_cobranzas ERROR calendar]: {e}")
        content = "Hubo un problema al reservar el turno. Intentá con otra fecha."
        return content, None

    # Iniciar flujo de cobranza
    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
    next_state = await iniciar_cobranzas_svc(
        cliente.telefono,
        especialidad=profesional,
        detalle_turno=content,
        empresa_id=empresa_id,
    )

    return content, next_state


async def handler_precio(tool_input, cliente, session, empresa, scope=None):
    """
    Handler para el contexto de secretaria_principal: solo consulta de precio, sin turno.
    Retorna (content, next_state).
    """
    empresa_id = empresa.id if empresa else None
    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
    next_state = await iniciar_cobranzas_svc(
        cliente.telefono,
        especialidad=tool_input.get("especialidad"),
        cobertura=tool_input.get("cobertura"),
        empresa_id=empresa_id,
    )
    return "Derivado a cobranzas. Instrucciones de pago enviadas.", next_state
