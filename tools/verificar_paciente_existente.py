import logging
from models import Cliente

DEFINITION = {
    "name": "verificar_paciente_existente",
    "description": "Busca un paciente ya registrado por DNI. Usar cuando el paciente dice que NO es primera vez en la clínica.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dni": {"type": "string", "description": "DNI del paciente a buscar."}
        },
        "required": ["dni"]
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    """
    Busca por DNI en toda la BD (cross-teléfono).
    Si encuentra datos en otro registro, los copia al cliente actual.
    Retorna (content, derivar). derivar siempre None.
    """
    dni_buscado = tool_input.get("dni", "").strip()
    logging.warning(f"[TOOL verificar_paciente_existente] Buscando DNI {dni_buscado}")

    encontrado = session.query(Cliente).filter(Cliente.dni == dni_buscado).first()

    if not encontrado:
        logging.warning(f"[TOOL verificar_paciente_existente] DNI {dni_buscado} no encontrado.")
        return (
            f"No se encontró ningún paciente con DNI {dni_buscado}. "
            "Pedile todos los datos para registrarlo como paciente nuevo.",
            None
        )

    if encontrado.id == cliente.id:
        resumen = f"{cliente.nombre_completo or 'Sin nombre'} | Obra social: {cliente.obra_social or 'No registrada'}"
        logging.warning(f"[TOOL verificar_paciente_existente] Mismo cliente. Datos: {resumen}")
        return f"Datos ya cargados: {resumen}.", None

    # Copiar datos de otro registro al cliente actual (paciente en un teléfono diferente)
    logging.warning(f"[TOOL verificar_paciente_existente] Encontrado en otro teléfono. Copiando datos...")
    cliente.nombre_completo  = encontrado.nombre_completo
    cliente.dni              = encontrado.dni
    cliente.obra_social      = encontrado.obra_social
    cliente.numero_afiliado  = encontrado.numero_afiliado
    cliente.fecha_nacimiento = encontrado.fecha_nacimiento
    if encontrado.mail:
        cliente.mail = encontrado.mail
    if encontrado.profesional_id:
        cliente.profesional_id = encontrado.profesional_id
    session.commit()

    prof_info = ""
    if cliente.profesional_id:
        from models import Profesional
        prof = session.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
        if prof:
            prof_info = f" | Profesional habitual: {prof.nombre}"

    content = (
        f"Paciente encontrado: {cliente.nombre_completo} | "
        f"Obra social: {cliente.obra_social or 'No registrada'}{prof_info}. "
        "Datos cargados correctamente."
    )
    logging.warning(f"[TOOL verificar_paciente_existente] {content}")
    return content, None
