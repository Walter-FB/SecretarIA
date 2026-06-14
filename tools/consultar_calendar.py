import logging
import os

DEFINITION = {
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
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Consulta disponibilidad con motor híbrido:
    - profesional.calendar_id is None → motor local (tabla turnos)
    - "empresa" o ID real          → Google Calendar
    """
    from tools.calendar_utils import _consultar_calendar, _consultar_calendar_local
    from agents.herramientas_secretarias import enviar_mensaje_wpp

    # Resolver profesional y motor
    profesional = None
    usar_local  = False
    if cliente and cliente.profesional_id:
        from models import Profesional
        profesional = session.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
    if profesional:
        usar_local = profesional.calendar_id is None

    if usar_local:
        resultado = _consultar_calendar_local(
            texto_fecha   = tool_input.get("texto_fecha", "hoy"),
            dias          = tool_input.get("dias_a_consultar", 3),
            profesional_id = str(profesional.id),
            db            = session,
        )
    else:
        if profesional and profesional.calendar_id and profesional.calendar_id != "empresa":
            cal_id = profesional.calendar_id
        else:
            cal_id = (empresa.calendar_id if empresa and empresa.calendar_id
                      else os.getenv("CALENDAR_ID", "primary"))
        resultado = _consultar_calendar(
            texto_fecha = tool_input.get("texto_fecha", "hoy"),
            dias        = tool_input.get("dias_a_consultar", 3),
            calendar_id = cal_id,
        )

    logging.warning(f"[TOOL consultar_calendar] Resultado: {resultado[:80]}...")
    return resultado, None
