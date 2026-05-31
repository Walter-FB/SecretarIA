# ===================================================================
# JOB DE SEGUIMIENTO — Cada 1 hora
# Lee seguimientos pendientes, manda mensaje fijo, marca como enviado.
# ===================================================================
from database import SessionLocal
from models import Cliente, Seguimiento
from datetime import datetime


async def job_seguimiento():
    """Se ejecuta cada hora. Manda remarketing a clientes fríos/perdidos."""
    print("[📬 SEGUIMIENTO] Chequeando seguimientos pendientes...")
    
    db = SessionLocal()
    try:
        ahora = datetime.utcnow()
        
        pendientes = db.query(Seguimiento).filter(
            Seguimiento.estado == "pendiente",
            Seguimiento.fecha_programada <= ahora
        ).all()
        
        if not pendientes:
            print("[📬 SEGUIMIENTO] Sin seguimientos pendientes.")
            return
        
        print(f"[📬 SEGUIMIENTO] {len(pendientes)} mensajes a enviar.")
        
        from agents.herramientas_secretarias import enviar_mensaje_wpp
        
        for seg in pendientes:
            try:
                cliente = db.query(Cliente).filter(Cliente.id == seg.cliente_id).first()
                if not cliente:
                    seg.estado = "enviado"
                    continue
                
                # v1: Mensaje fijo de remarketing
                # v2 (futuro): Personalizar con datos del analista
                mensaje_seguimiento = (
                    "Quedó alguna duda?"
                )
                
                await enviar_mensaje_wpp(cliente.telefono, mensaje_seguimiento)
                
                seg.estado = "enviado"
                print(f"[📬 ENVIADO] Seguimiento a {cliente.telefono}")
                
            except Exception as e:
                print(f"[❌ ERROR enviando seguimiento a {seg.cliente_id[:8]}]: {e}")
                continue
        
        db.commit()
        print("[📬 SEGUIMIENTO] Ronda completada.\n")
        
    except Exception as e:
        print(f"[❌ ERROR JOB SEGUIMIENTO]: {e}")
    finally:
        db.close()
