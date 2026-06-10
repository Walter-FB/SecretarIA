DEFINITION = {
    "name": "consultar_precio",
    "description": "Consulta el precio de una consulta según especialidad y cobertura del paciente. Usá esta tool cuando el paciente pregunte cuánto cuesta — el mensaje se envía automáticamente, no necesitás responder nada más.",
    "input_schema": {
        "type": "object",
        "properties": {
            "especialidad": {
                "type": "string",
                "enum": ["psicología", "psiquiatría"],
                "description": "Especialidad consultada"
            },
            "cobertura": {
                "type": "string",
                "description": "Obra social (ej: OSDE, IOMA) o 'particular'. Si el paciente no lo dijo, omitilo."
            }
        },
        "required": ["especialidad"]
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    from services.profesionales import get_profesional_by_especialidad, get_tarifa, _normalizar_especialidad
    from services.cobranza import _normalizar_cobertura
    from agents.herramientas_secretarias import enviar_mensaje_wpp

    especialidad = tool_input.get("especialidad", "psicología")
    cobertura    = tool_input.get("cobertura") or (cliente.obra_social if cliente else None) or "particular"
    empresa_id   = empresa.id if empresa else None

    profesional = get_profesional_by_especialidad(session, _normalizar_especialidad(especialidad), empresa_id)
    if not profesional:
        await enviar_mensaje_wpp(cliente.telefono, "Por el momento no tengo información de precios disponible. Te comunico con el equipo.")
        return "Sin info de precios.", None

    modalidad, nombre_os = _normalizar_cobertura(cobertura)
    monto             = get_tarifa(profesional, modalidad)
    precio_particular = profesional.tarifa_particular

    tiene_datos = bool(cliente.nombre_completo and cliente.dni)
    cierre = " ¿Querés que coordinemos un turno?" if tiene_datos else ""

    def _fmt(n: int) -> str:
        return f"{n:,}".replace(",", ".")

    if modalidad == "obra social":
        nombre_os   = nombre_os or cobertura
        esp_display = "psicología" if profesional.especialidad == "psicologo" else "psiquiatría"
        mensaje = f"Con {nombre_os} la consulta de {esp_display} ({profesional.nombre}) sale ${_fmt(monto)}."
    else:
        mensaje = f"La consulta con {profesional.nombre} es de ${_fmt(monto)}."

    mensaje += cierre
    await enviar_mensaje_wpp(cliente.telefono, mensaje)
    return "Precio informado.", "_skip_"
