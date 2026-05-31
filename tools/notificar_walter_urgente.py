import logging

DEFINITION = {
    "name": "notificar_walter_urgente",
    "description": "Escalar a un humano. Para emergencias, crisis, pedido de hablar con humano, recetas o frustración.",
    "input_schema": {
        "type": "object",
        "properties": {
            "es_emergencia": {"type": "boolean"}
        },
        "required": ["es_emergencia"]
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Notifica a Walter por WhatsApp.
    No cambia el estado del cliente — el agente que la llama sigue activo.
    Retorna (content, None).
    """
    es_emergencia  = tool_input.get("es_emergencia", False)
    numero_walter  = empresa.numero_walter if empresa else None
    nombre_cliente = cliente.nombre_completo or (cliente.datos_extraidos or {}).get("nombre_contacto", "un paciente")

    logging.warning(f"[TOOL notificar_walter_urgente] {cliente.telefono} | emergencia={es_emergencia}")

    from agents.herramientas_secretarias import enviar_notificacion_a_walter
    await enviar_notificacion_a_walter(cliente.telefono, nombre_cliente, numero_walter)

    return f"Walter notificado. Emergencia: {es_emergencia}.", None
