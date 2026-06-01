import logging
import re

DEFINITION = {
    "name": "registrar_paciente",
    "description": (
        "Guarda los datos de un paciente nuevo. "
        "Llamar antes de iniciar_agendamiento si es primera vez o si verificar_paciente_existente no encontró resultados."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre_completo":  {"type": "string"},
            "dni":              {"type": "string"},
            "obra_social":      {"type": "string"},
            "numero_afiliado":  {"type": "string"},
            "fecha_nacimiento": {"type": "string"},
            "mail":             {"type": "string"},
        },
        "required": ["nombre_completo", "dni", "obra_social", "numero_afiliado", "fecha_nacimiento"]
    }
}


def _normalizar_dni(dni: str) -> str | None:
    """Devuelve solo los dígitos si son 7 u 8, o None si el formato es inválido."""
    digitos = re.sub(r'\D', '', dni or '')
    return digitos if 7 <= len(digitos) <= 8 else None


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Guarda los datos del paciente en las columnas directas del modelo Cliente.
    Retorna (content, derivar). derivar siempre None — Abby sigue en el mismo estado.
    """
    logging.warning(f"[TOOL registrar_paciente] Guardando datos para {cliente.telefono}")

    dni_raw = tool_input.get("dni", "")
    dni_ok  = _normalizar_dni(dni_raw)
    if not dni_ok:
        logging.warning(f"[TOOL registrar_paciente] DNI inválido: '{dni_raw}'")
        return (
            f"DNI '{dni_raw}' inválido. El DNI argentino tiene 7 u 8 dígitos. "
            "Pedile al paciente que lo reenvíe correctamente.",
            None,
        )

    cliente.nombre_completo  = tool_input.get("nombre_completo")
    cliente.dni              = dni_ok
    cliente.obra_social      = tool_input.get("obra_social")
    cliente.numero_afiliado  = tool_input.get("numero_afiliado")
    cliente.fecha_nacimiento = tool_input.get("fecha_nacimiento")
    if tool_input.get("mail"):
        cliente.mail = tool_input.get("mail")
    session.commit()

    logging.warning(f"[TOOL registrar_paciente] Guardado: {cliente.nombre_completo} | DNI: {cliente.dni}")
    content = (
        f"Paciente registrado correctamente: {cliente.nombre_completo} | "
        f"DNI: {cliente.dni} | Obra social: {cliente.obra_social}."
    )
    return content, None
