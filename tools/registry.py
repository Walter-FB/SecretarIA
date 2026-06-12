from tools import (
    registrar_paciente,
    verificar_paciente_existente,
    iniciar_agendamiento,
    iniciar_cobranzas,
    notificar_walter_urgente,
    consultar_calendar,
    volver_secretaria_principal,
    consultar_precio,
    silenciar_seguimiento,
    omitir_respuesta,
)

# Catálogo completo. Cada entrada: nombre → módulo con DEFINITION y handler.
TOOL_CATALOG = {
    "registrar_paciente":           registrar_paciente,
    "verificar_paciente_existente": verificar_paciente_existente,
    "iniciar_agendamiento":         iniciar_agendamiento,
    "iniciar_cobranzas":            iniciar_cobranzas,
    "notificar_walter_urgente":     notificar_walter_urgente,
    "consultar_calendar":           consultar_calendar,
    "volver_secretaria_principal":  volver_secretaria_principal,
    "consultar_precio":             consultar_precio,
    "silenciar_seguimiento":        silenciar_seguimiento,
    "omitir_respuesta":             omitir_respuesta,
}


def get_tools_for_empresa(empresa):
    """
    Devuelve (definitions, handlers) para las tools habilitadas de una empresa.
    Contexto: secretaria_principal — usa DEFINITION_PRECIO para iniciar_cobranzas.

    - definitions: lista de dicts para pasarle a Claude (el parámetro 'tools')
    - handlers:    dict nombre → función handler async

    Si empresa.tools_habilitadas es None o vacía, devuelve todas las tools del catálogo.
    """
    tools_activas = empresa.tools_habilitadas if empresa and empresa.tools_habilitadas else list(TOOL_CATALOG.keys())

    definitions = []
    handlers    = {}
    for name in tools_activas:
        module = TOOL_CATALOG.get(name)
        if module is None:
            continue
        if name == "iniciar_cobranzas":
            definitions.append(module.DEFINITION_PRECIO)
            handlers[name] = module.handler_precio
        else:
            definitions.append(module.DEFINITION)
            handlers[name] = module.handler

    return definitions, handlers


def get_tools_for_agendadora(empresa=None):
    """
    Subconjunto fijo de tools para la agendadora.
    Usa DEFINITION de cada módulo, incluyendo la variante de iniciar_cobranzas con turno.
    """
    names = [
        "consultar_calendar",
        "iniciar_cobranzas",
        "volver_secretaria_principal",
        "notificar_walter_urgente",
        "omitir_respuesta",
    ]
    definitions = [TOOL_CATALOG[n].DEFINITION for n in names if n in TOOL_CATALOG]
    handlers    = {n: TOOL_CATALOG[n].handler for n in names if n in TOOL_CATALOG}
    return definitions, handlers
