import asyncio
import logging
from datetime import datetime, timedelta, timezone

# Timer por cliente: cliente_id → Task activa
_timers: dict[str, asyncio.Task] = {}

# Clientes donde ya se disparó un seguimiento en esta sesión de proceso (máx 1)
_disparados: set[str] = set()


def resetear_timer(cliente_id: str, telefono: str, empresa_id: str):
    """Cancela el timer existente y arranca uno nuevo de 2m30s."""
    existing = _timers.get(cliente_id)
    if existing and not existing.done():
        existing.cancel()
    _timers[cliente_id] = asyncio.create_task(
        _disparar_seguimiento(cliente_id, telefono, empresa_id)
    )


def cancelar_timer(cliente_id: str):
    """Cancela el timer sin crear uno nuevo. Limpia el flag de disparado."""
    task = _timers.pop(cliente_id, None)
    if task and not task.done():
        task.cancel()
    _disparados.discard(cliente_id)


async def _disparar_seguimiento(cliente_id: str, telefono: str, empresa_id: str):
    """Espera 2m30s y manda un toque contextual si el cliente sigue sin responder."""
    await asyncio.sleep(150)

    if cliente_id in _disparados:
        return

    from database import SessionLocal
    db = SessionLocal()
    try:
        from models import Cliente
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente or not cliente.bot_activo or cliente.estado_agente == "manual":
            return

        datos = cliente.datos_extraidos or {}
        if datos.get("pago_estado") in ("esperando_comprobante", "pagado"):
            return

        # Solo dentro del horario de atención (09:00–21:00 ARG = UTC-3)
        hora_arg = (datetime.now(timezone.utc) + timedelta(hours=-3)).hour
        if not (9 <= hora_arg < 21):
            return

        _disparados.add(cliente_id)
        await _toque_contextual(cliente, db, telefono)
    except Exception as e:
        logging.warning(f"[SEGUIMIENTO ERROR]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
        _timers.pop(cliente_id, None)


async def _toque_contextual(cliente, db, telefono: str):
    """Llama al agente activo con instrucción sintética. No persiste el trigger en DB."""
    from models import Mensaje
    from agents.herramientas_secretarias import client_claude, enviar_mensaje_wpp

    hace_6h = datetime.utcnow() - timedelta(hours=6)
    mensajes = (
        db.query(Mensaje)
        .filter(Mensaje.cliente_id == cliente.id, Mensaje.fecha_creacion >= hace_6h)
        .order_by(Mensaje.fecha_creacion.desc())
        .limit(40)
        .all()
    )

    agente_actual = cliente.estado_agente  # "principal" o "agendadora"

    raw = []
    for m in reversed(mensajes):
        if m.rol == "usuario":
            raw.append({"role": "user", "content": m.texto})
        elif m.rol == "asistente" and (m.agente == agente_actual or m.agente is None):
            raw.append({"role": "assistant", "content": m.texto})

    historial = []
    for msg in raw:
        if historial and historial[-1]["role"] == msg["role"]:
            historial[-1]["content"] += "\n" + msg["content"]
        else:
            historial.append(dict(msg))

    # Trigger sintético — no se guarda en DB ni se añade al historial persistente
    historial.append({
        "role": "user",
        "content": (
            "El paciente no respondio. Mandales un toque corto y contextual al paso pendiente "
            "(una oracion). Si no hay nada pendiente, usa omitir_respuesta."
        )
    })

    # Reusar system prompt y tools del agente correspondiente
    if agente_actual == "principal":
        from agents.secretaria_principal import _build_system_prompt
        from tools.registry import get_tools_for_empresa
        from database import SessionLocal as _SL
        from models import Empresa
        _db2 = _SL()
        try:
            empresa = _db2.query(Empresa).filter(Empresa.id == cliente.empresa_id).first()
        finally:
            _db2.close()
        system    = _build_system_prompt(cliente, db, empresa)
        definitions, _ = get_tools_for_empresa(empresa)
    else:
        from agents.agendadora import SYSTEM_PROMPT_AGENDADORA
        from tools.registry import get_tools_for_agendadora
        system    = SYSTEM_PROMPT_AGENDADORA
        definitions, _ = get_tools_for_agendadora()

    try:
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=system,
            tools=definitions,
            messages=historial
        )
    except Exception as e:
        logging.warning(f"[SEGUIMIENTO CLAUDE ERROR]: {e}")
        return

    for block in response.content:
        if block.type == "tool_use" and block.name == "omitir_respuesta":
            logging.warning(f"[SEGUIMIENTO] {telefono} → omitir_respuesta (silencio intencional)")
            return
        if block.type == "text" and block.text.strip():
            texto = block.text.strip().replace("¿", "").replace("¡", "").replace("**", "")
            await enviar_mensaje_wpp(telefono, texto)
            db.add(Mensaje(
                cliente_id=cliente.id,
                empresa_id=cliente.empresa_id,
                rol="asistente",
                agente=agente_actual,
                texto=texto
            ))
            db.commit()
            logging.warning(f"[SEGUIMIENTO] Toque enviado a {telefono}: {texto[:60]}")
            return
