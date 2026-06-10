from database import SessionLocal
from models import Cliente, Seguimiento, ColaAnalisis
from datetime import datetime, timedelta, timezone
import asyncio
import logging

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
    """Espera 2m30s y manda el seguimiento si el cliente sigue sin responder."""
    await asyncio.sleep(150)

    db = SessionLocal()
    try:
        from agents.herramientas_secretarias import enviar_mensaje_wpp

        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente or not cliente.bot_activo or cliente.estado_agente == "manual":
            return

        ya_activo = db.query(Seguimiento).filter(
            Seguimiento.cliente_id == cliente_id,
            Seguimiento.estado     == "esperando_respuesta",
        ).first()
        if ya_activo:
            return

        ahora = datetime.now(timezone.utc).replace(tzinfo=None)
        await enviar_mensaje_wpp(telefono, "¿Quedó alguna duda?")
        db.add(Seguimiento(
            cliente_id       = cliente_id,
            estado           = "esperando_respuesta",
            fecha_programada = ahora + timedelta(hours=1),
        ))
        db.commit()
        logging.warning(f"[SEGUIMIENTO] Disparado para {telefono}")
    except Exception as e:
        logging.warning(f"[❌ SEGUIMIENTO TIMER]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
        _timers.pop(cliente_id, None)


async def job_seguimiento():
    """
    Job cada 5 minutos — solo fases 2 y 3.

    Fase 2 — Análisis:
        seguimiento "esperando_respuesta" vencido →
        si respondió: limpia. Si no: corre análisis IA.

    Fase 3 — Remarketing:
        seguimiento "pendiente" vencido → manda mensaje.
    """
    logging.warning("[📬 SEGUIMIENTO] Ronda fases 2+3...")
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    db    = SessionLocal()
    try:
        from agents.herramientas_secretarias import enviar_mensaje_wpp
        from services.analisis_charla import analizar_y_registrar

        # ── FASE 2 ───────────────────────────────────────────────────────────────
        for seg in db.query(Seguimiento).filter(
            Seguimiento.estado           == "esperando_respuesta",
            Seguimiento.fecha_programada <= ahora,
        ).all():
            cola    = db.query(ColaAnalisis).filter(ColaAnalisis.cliente_id == seg.cliente_id).first()
            cliente = db.query(Cliente).filter(Cliente.id == seg.cliente_id).first()

            if not cola or not cliente:
                if cola: db.delete(cola)
                db.delete(seg)
                db.commit()
                continue

            sent_at = seg.fecha_programada - timedelta(hours=1)
            if cola.fecha_ultima_actividad > sent_at:
                logging.warning(f"[SEGUIMIENTO] {cliente.telefono} respondió — limpiando.")
            else:
                logging.warning(f"[SEGUIMIENTO] {cliente.telefono} sin respuesta — analizando.")
                await analizar_y_registrar(cliente, db)

            db.delete(seg)
            db.delete(cola)
            db.commit()

        # ── FASE 3 ───────────────────────────────────────────────────────────────
        for seg in db.query(Seguimiento).filter(
            Seguimiento.estado           == "pendiente",
            Seguimiento.fecha_programada <= ahora,
        ).all():
            cliente = db.query(Cliente).filter(Cliente.id == seg.cliente_id).first()
            if not cliente:
                seg.estado = "enviado"
                db.commit()
                continue
            try:
                await enviar_mensaje_wpp(cliente.telefono, "¿Seguís interesado en sacar un turno?")
                seg.estado = "enviado"
                db.commit()
                logging.warning(f"[REMARKETING] Enviado a {cliente.telefono}")
            except Exception as e:
                logging.warning(f"[❌ REMARKETING] {cliente.telefono}: {e}")

    except Exception as e:
        logging.warning(f"[❌ ERROR JOB SEGUIMIENTO]: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()
