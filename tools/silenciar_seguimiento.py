DEFINITION = {
    "name": "silenciar_seguimiento",
    "description": (
        "Cancela los mensajes de seguimiento para este paciente. "
        "Usá cuando el paciente se despidió, cerró la charla, o dejó claro que no quiere más contacto."
    ),
    "input_schema": {
        "type": "object",
        "properties": {}
    }
}


async def handler(tool_input, cliente, session, empresa, scope=None):
    from agents.seguimiento import cancelar_timer
    from models import Seguimiento

    cancelar_timer(str(cliente.id))

    session.query(Seguimiento).filter(
        Seguimiento.cliente_id == cliente.id,
        Seguimiento.estado.in_(["esperando_respuesta", "pendiente"])
    ).delete(synchronize_session=False)
    session.commit()

    return "Seguimiento cancelado.", None
