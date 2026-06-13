from tools import (
    registrar_paciente,
    verificar_paciente_existente,
    iniciar_cobranzas,
    notificar_walter_urgente,
    consultar_calendar,
    consultar_precio,
    silenciar_seguimiento,
    omitir_respuesta,
)

# Catálogo completo. Cada entrada: nombre → módulo con DEFINITION y handler.
TOOL_CATALOG = {
    "registrar_paciente":           registrar_paciente,
    "verificar_paciente_existente": verificar_paciente_existente,
    "iniciar_cobranzas":            iniciar_cobranzas,
    "notificar_walter_urgente":     notificar_walter_urgente,
    "consultar_calendar":           consultar_calendar,
    "consultar_precio":             consultar_precio,
    "silenciar_seguimiento":        silenciar_seguimiento,
    "omitir_respuesta":             omitir_respuesta,
}


def get_tools_for_abby(empresa=None):
    """
    Devuelve (definitions, handlers) para Abby — agente unificado.
    Usa DEFINITION (con dia/hora/profesional/iso_datetime) para iniciar_cobranzas.
    """
    tools_activas = (
        empresa.tools_habilitadas
        if empresa and empresa.tools_habilitadas
        else list(TOOL_CATALOG.keys())
    )

    definitions = []
    handlers    = {}
    for name in tools_activas:
        module = TOOL_CATALOG.get(name)
        if module is None:
            continue
        definitions.append(module.DEFINITION)
        handlers[name] = module.handler

    return definitions, handlers


# ── Funciones legacy (código muerto — no se usan más) ─────────────
# Se conservan por si hace falta rollback.

def get_tools_for_empresa(empresa):
    """DEPRECATED — usar get_tools_for_abby()."""
    return get_tools_for_abby(empresa)


def get_tools_for_agendadora(empresa=None):
    """DEPRECATED — la agendadora ya no existe como agente separado."""
    return get_tools_for_abby(empresa)
