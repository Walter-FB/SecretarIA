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
    El trigger sintético se guarda con agente='sistema' para que no contamine el historial.
    """
    especialidad = tool_input.get("especialidad", "no especificada")
    cobertura    = tool_input.get("cobertura",    "no especificada")
    profesional  = tool_input.get("profesional",  "")
    empresa_id   = empresa.id if empresa else None

    logging.warning(f"[TOOL iniciar_agendamiento] {cliente.telefono} | {especialidad} | {cobertura} | prof={profesional}")

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

    try:
        from agents.agendadora import secretaria_agendadora
        trigger = f"Iniciar agendamiento para especialidad {especialidad} con cobertura {cobertura}."
        await secretaria_agendadora(trigger, cliente.telefono, None, empresa_id, _store_as_sistema=True)
    except Exception as e:
        logging.warning(f"[TOOL iniciar_agendamiento ERROR]: {e}")

    return f"Derivado a agendadora. Especialidad: {especialidad}, Cobertura: {cobertura}.", "agendadora"
