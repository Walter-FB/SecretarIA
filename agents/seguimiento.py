from database import SessionLocal
from models import Cliente, Seguimiento
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import logging

_TIMEZONE_ARG = ZoneInfo("America/Argentina/Buenos_Aires")

# Timer por cliente: cliente_id → Task activa
_timers: dict[str, asyncio.Task] = {}


def resetear_timer(cliente_id: str, telefono: str, empresa_id: str):
    """Cancela el timer existente y arranca uno nuevo de 2m30s."""
    existing = _timers.get(cliente_id)
    if existing and not existing.done():
        existing.cancel()
    _timers[cliente_id] = asyncio.create_task(
        _disparar_seguimiento(cliente_id, telefono, empresa_id)
    )


def cancelar_timer(cliente_id: str):
    """Cancela el timer sin crear uno nuevo. Usado cuando el paciente cierra la charla."""
    task = _timers.pop(cliente_id, None)
    if task and not task.done():
        task.cancel()


async def _disparar_seguimiento(cliente_id: str, telefono: str, empresa_id: str):
    """Espera 2m30s y reenvía al agente activo con instrucción de seguimiento."""
    await asyncio.sleep(150)

    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()

        # Condiciones de NO disparo
        if not cliente or not cliente.bot_activo or cliente.estado_agente == "manual":
            return

        # Ya se disparó un seguimiento en esta sesión (máximo 1)
        if db.query(Seguimiento).filter(
            Seguimiento.cliente_id == cliente_id,
            Seguimiento.estado     == "esperando_respuesta",
        ).first():
            return

        # Turno confirmado → no interrumpir
        datos = cliente.datos_extraidos or {}
        if datos.get("pago_estado") in ("esperando_comprobante", "pagado"):
            return

        # Fuera de horario laboral ARG (09:00–21:00)
        hora_local = datetime.now(_TIMEZONE_ARG).hour
        if hora_local < 9 or hora_local >= 21:
            return

        # Registrar para evitar duplicados
        db.add(Seguimiento(
            cliente_id       = cliente_id,
            estado           = "esperando_respuesta",
            fecha_programada = datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        db.commit()

        instruccion = (
            "El paciente no respondió. Mandale un toque corto y contextual al paso "
            "pendiente (una oración). Si no hay nada pendiente, llamá omitir_respuesta."
        )

        estado_actual = cliente.estado_agente
        if estado_actual == "principal":
            from agents.secretaria_principal import secretaria_principal
            await secretaria_principal("", telefono, None, empresa_id, _seguimiento=instruccion)
        elif estado_actual == "agendadora":
            from agents.agendadora import secretaria_agendadora
            await secretaria_agendadora("", telefono, None, empresa_id, _seguimiento=instruccion)

        logging.warning(f"[SEGUIMIENTO] Toque disparado para {telefono} (estado={estado_actual})")

    except Exception as e:
        logging.warning(f"[❌ SEGUIMIENTO TIMER]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
        _timers.pop(cliente_id, None)
