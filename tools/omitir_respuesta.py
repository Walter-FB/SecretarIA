DEFINITION = {
    "name": "omitir_respuesta",
    "description": (
        "Usala cuando NO corresponde enviar ningún mensaje: el paciente cerró la charla "
        "(ok, gracias, emoji), la consulta ya quedó resuelta, o responder sería relleno."
    ),
    "input_schema": {"type": "object", "properties": {}}
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    return ("", "_omitir_")
