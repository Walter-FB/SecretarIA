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

    from agents.herramientas_secretarias import enviar_mensaje_wpp, enviar_notificacion_a_walter, NUMERO_WALTER

    if es_emergencia:
        destino = numero_walter or NUMERO_WALTER
        mensaje = (
            "🚨 URGENTE — posible crisis de un paciente\n"
            f"Numero: {cliente.telefono}\n"
            f"Nombre: {nombre_cliente}\n"
            "Abby detectó una situación de riesgo, le recordó el 135 y la guardia, "
            "y le avisó que el equipo lo va a contactar. Escribile o llamalo AHORA."
        )
        try:
            await enviar_mensaje_wpp(destino, mensaje)
            logging.warning("[NOTIFICACIÓN] Alerta de EMERGENCIA enviada a Walter.")
        except Exception as e:
            logging.warning(f"[NOTIFICACIÓN EMERGENCIA ERROR]: {e}")
    else:
        await enviar_notificacion_a_walter(cliente.telefono, nombre_cliente, numero_walter)

    return f"Walter notificado. Emergencia: {es_emergencia}.", None
