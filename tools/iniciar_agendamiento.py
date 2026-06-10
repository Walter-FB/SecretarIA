import logging

DEFINITION = {
    "name": "iniciar_agendamiento",
    "description": "Deriva a la agendadora para coordinar el turno. Usar cuando el paciente ya está registrado o verificado.",
    "input_schema": {
        "type": "object",
        "properties": {
            "especialidad": {"type": "string", "enum": ["psicología", "psiquiatría"]},
            "cobertura":    {"type": "string"},
            "profesional":  {"type": "string", "description": "Nombre del profesional elegido (ej: 'Lic. Renals', 'Dr. Barros')."},
        },
        "required": ["especialidad", "cobertura"]
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Asigna profesional, cambia estado a 'agendadora' y arranca el flujo de agendamiento.
    Retorna (content, "agendadora") para que el service cambie el estado.
    """
    especialidad = tool_input.get("especialidad", "no especificada")
    cobertura    = tool_input.get("cobertura",    "no especificada")
    profesional  = tool_input.get("profesional",  "")
    empresa_id   = empresa.id if empresa else None

    logging.warning(f"[TOOL iniciar_agendamiento] {cliente.telefono} | {especialidad} | {cobertura} | prof={profesional}")

    # Actualizar profesional del cliente: por nombre si se pasó, o por especialidad como fallback
    from services.profesionales import get_profesional_by_nombre, get_profesional_by_especialidad, _normalizar_especialidad
    prof_obj = None
    if profesional:
        prof_obj = get_profesional_by_nombre(session, profesional, empresa_id)
    if not prof_obj and especialidad:
        prof_obj = get_profesional_by_especialidad(session, _normalizar_especialidad(especialidad), empresa_id)
    if prof_obj:
        cliente.profesional_id = prof_obj.id
        session.commit()
        logging.warning(f"[TOOL iniciar_agendamiento] Profesional actualizado: {prof_obj.nombre}")

    from agents.herramientas_secretarias import enviar_mensaje_wpp
    await enviar_mensaje_wpp(
        cliente.telefono,
        "Dale, dejame revisar la agenda para coordinar día y hora. Un segundo..."
    )

    try:
        from agents.agendadora import secretaria_agendadora
        await secretaria_agendadora(
            f"Iniciar agendamiento para especialidad {especialidad} con cobertura {cobertura}.",
            cliente.telefono,
            None,
            empresa_id
        )
    except Exception as e:
        logging.warning(f"[TOOL iniciar_agendamiento ERROR]: {e}")

    return f"Derivado a agendadora. Especialidad: {especialidad}, Cobertura: {cobertura}.", "agendadora"
