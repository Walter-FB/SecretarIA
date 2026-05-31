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
    Consulta la disponibilidad en Google Calendar y devuelve los slots libres.
    Retorna (content, None) — la respuesta es informativa, no cambia estado.
    """
    from agents.agendadora import _consultar_calendar
    from agents.herramientas_secretarias import enviar_mensaje_wpp

    calendar_id = (
        empresa.calendar_id if empresa and empresa.calendar_id
        else os.getenv("CALENDAR_ID", "primary")
    )

    await enviar_mensaje_wpp(cliente.telefono, "Reviso la agenda... un momento.")

    resultado = _consultar_calendar(
        texto_fecha = tool_input.get("texto_fecha", "hoy"),
        dias        = tool_input.get("dias_a_consultar", 3),
        calendar_id = calendar_id
    )

    logging.warning(f"[TOOL consultar_calendar] Resultado: {resultado[:80]}...")
    return resultado, None
