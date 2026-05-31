import logging

DEFINITION = {
    "name": "volver_secretaria_principal",
    "description": "Devuelve al paciente a la secretaria principal si el tema se desvía del agendamiento.",
    "input_schema": {"type": "object", "properties": {}}
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Señaliza que el cliente debe volver a la secretaria principal.
    El service es responsable de actualizar cliente.estado_agente = "principal".
    Retorna (content, "principal").
    """
    logging.warning(f"[TOOL volver_secretaria_principal] {cliente.telefono} vuelve a principal")
    return "Derivando al paciente a la secretaria principal.", "principal"
