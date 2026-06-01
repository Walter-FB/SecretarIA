from database import SessionLocal
from models import Cliente, Seguimiento, ColaAnalisis
from datetime import datetime, timedelta, timezone
import logging


async def job_seguimiento():
    """
    Job horario. Gestiona el ciclo de vida de cada conversación:

    Fase 1 — Reactivación:
        cola_analisis inactiva >= 1h → manda "¿Quedó alguna duda?" →
        crea seguimiento estado="esperando_respuesta" con vencimiento en 1h.

    Fase 2 — Análisis:
        seguimiento "esperando_respuesta" vencido →
        si el cliente respondió: limpia y listo.
        si no respondió: corre análisis IA (clasifica, crea remarketing si fría/perdida).

    Fase 3 — Remarketing:
        seguimiento "pendiente" vencido → manda mensaje de remarketing programado.
    """
    logging.warning("[📬 SEGUIMIENTO] Iniciando ronda...")
    ahora  = datetime.now(timezone.utc).replace(tzinfo=None)
    umbral = ahora - timedelta(hours=1)

    db = SessionLocal()
    try:
        from agents.herramientas_secretarias import enviar_mensaje_wpp
        from services.analisis_charla import analizar_y_registrar

        # ── FASE 2: verificar respuestas y analizar si siguen quietos ───────────
        esperando = db.query(Seguimiento).filter(
            Seguimiento.estado == "esperando_respuesta",
            Seguimiento.fecha_programada <= ahora,
        ).all()

        for seg in esperando:
            cola    = db.query(ColaAnalisis).filter(ColaAnalisis.cliente_id == seg.cliente_id).first()
            cliente = db.query(Cliente).filter(Cliente.id == seg.cliente_id).first()

            if not cola or not cliente:
                if cola:
                    db.delete(cola)
                db.delete(seg)
                db.commit()
                continue

            # sent_at ≈ cuando mandamos el mensaje de reactivación (1h antes de fecha_programada)
            sent_at = seg.fecha_programada - timedelta(hours=1)

            if cola.fecha_ultima_actividad > sent_at:
                logging.warning(f"[SEGUIMIENTO] {cliente.telefono} respondió — cancelando análisis.")
                db.delete(seg)
                db.delete(cola)
            else:
                logging.warning(f"[SEGUIMIENTO] {cliente.telefono} sin respuesta — analizando.")
                await analizar_y_registrar(cliente, db)
                db.delete(seg)
                db.delete(cola)

            db.commit()

        # ── FASE 1: detectar conversaciones inactivas >= 1h ─────────────────────
        inactivos = db.query(ColaAnalisis).filter(
            ColaAnalisis.fecha_ultima_actividad <= umbral
        ).all()

        for cola in inactivos:
            ya_activo = db.query(Seguimiento).filter(
                Seguimiento.cliente_id == cola.cliente_id,
                Seguimiento.estado     == "esperando_respuesta",
            ).first()
            if ya_activo:
                continue

            cliente = db.query(Cliente).filter(Cliente.id == cola.cliente_id).first()
            if not cliente:
                continue

            if not cliente.bot_activo or cliente.estado_agente == "manual":
                continue

            await enviar_mensaje_wpp(cliente.telefono, "¿Quedó alguna duda?")
            db.add(Seguimiento(
                cliente_id       = cola.cliente_id,
                estado           = "esperando_respuesta",
                fecha_programada = ahora + timedelta(hours=1),
            ))
            db.commit()
            logging.warning(f"[SEGUIMIENTO] Reactivación enviada a {cliente.telefono} — esperando 1h.")

        # ── FASE 3: remarketing programado (pendiente) ───────────────────────────
        remarketing = db.query(Seguimiento).filter(
            Seguimiento.estado           == "pendiente",
            Seguimiento.fecha_programada <= ahora,
        ).all()

        for seg in remarketing:
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
