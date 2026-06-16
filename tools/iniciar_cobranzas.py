import logging
from datetime import timedelta

DEFINITION = {
    "name": "iniciar_cobranzas",
    "description": "Reserva el turno y envía al paciente los datos de pago. Llamala cuando el paciente confirmó el horario — sin anunciarlo.",
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
        "required": ["dia", "hora", "profesional", "iso_datetime"]
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    from tools.calendar_utils import (
        TIMEZONE, _parse_fecha, _parse_hora,
        _build_calendar_service, _is_busy, _crear_evento,
    )
    from datetime import datetime
    import os

    dia         = tool_input.get("dia", "")
    hora        = tool_input.get("hora", "")
    profesional = tool_input.get("profesional", "")
    iso_dt      = tool_input.get("iso_datetime", "")

    nombre_clinica = empresa.nombre if empresa else "Clínica"
    empresa_id     = empresa.id if empresa else None

    _DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

    if iso_dt:
        try:
            dt = datetime.fromisoformat(iso_dt)
            # Si llega naive, ya es hora argentina — no convertir desde UTC.
            start = dt.replace(tzinfo=TIMEZONE) if dt.tzinfo is None else dt.astimezone(TIMEZONE)
        except ValueError:
            return "El código [ISO:...] no es válido. Copiá exactamente el código que aparece en los horarios ofrecidos.", None
    else:
        texto = f"{dia} {hora}"
        fecha = _parse_fecha(texto)
        h     = _parse_hora(texto)
        start = datetime(fecha.year, fecha.month, fecha.day,
                         h if h is not None else 10, 0, tzinfo=TIMEZONE) if fecha else None

    end = (start + timedelta(hours=1)) if start else None

    if not start or not end:
        return f"No pude interpretar la fecha '{dia} {hora}'. Confirma día y hora exactos.", None

    if start.weekday() >= 5:
        return f"Los turnos son de lunes a viernes. {_DIAS_ES[start.weekday()].capitalize()} no tiene disponibilidad. Elegí otro horario.", None

    from models import Profesional as ProfModel
    from services.profesionales import get_profesional_by_nombre
    profesional_obj = None
    if profesional:
        profesional_obj = get_profesional_by_nombre(session, profesional, empresa_id)
    if not profesional_obj and cliente and cliente.profesional_id:
        profesional_obj = session.query(ProfModel).filter(ProfModel.id == cliente.profesional_id).first()

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
            from agents.seguimiento import cancelar_timer
            cancelar_timer(cliente.id)
            fecha_display = f"{_DIAS_ES[start.weekday()]} {start.strftime('%d/%m')} a las {start.strftime('%H')}hs"
            content = f"Turno reservado el {fecha_display} con {profesional_obj.nombre}."
        except IntegrityError:
            session.rollback()
            return "Ese horario se acaba de ocupar. Voy a buscar otra alternativa.", None
    else:
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
            _crear_evento(service, f"Turno {nombre_clinica} - {prof_nombre}", start, end, descripcion, calendar_id)
            from agents.seguimiento import cancelar_timer
            cancelar_timer(cliente.id)
            fecha_display = f"{_DIAS_ES[start.weekday()]} {start.strftime('%d/%m')} a las {start.strftime('%H')}hs"
            content = f"Turno reservado el {fecha_display} con {prof_nombre}."
        except Exception as e:
            logging.warning(f"[TOOL iniciar_cobranzas ERROR calendar]: {e}")
            return "Hubo un problema al reservar el turno. Intentá con otra fecha.", None

    from services.cobranza import iniciar_cobranzas as iniciar_cobranzas_svc
    esp = profesional_obj.nombre if profesional_obj else profesional
    try:
        next_state = await iniciar_cobranzas_svc(
            cliente.telefono,
            especialidad   = esp,
            detalle_turno  = content,
            empresa_id     = empresa_id,
        )
    except Exception as e:
        # El turno YA está en la BD — el servicio falló después del commit.
        # Devolvemos éxito para que el loop corte y Claude no diga "está ocupado".
        logging.warning(f"[iniciar_cobranzas] Servicio de cobranza falló (turno ya reservado): {e}")
        next_state = "principal"
    return content, next_state
