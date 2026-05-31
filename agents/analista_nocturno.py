# ===================================================================
# ANALISTA NOCTURNO — Corre a las 21:00hs Argentina
# Barre cola_analisis, clasifica charlas, llena seguimientos, limpia cola.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje, ColaAnalisis, Seguimiento
from datetime import datetime, timedelta
from agents.herramientas_secretarias import client_claude


async def job_analista_nocturno():
    """Se ejecuta una vez al día a las 21:00hs ARG (00:00 UTC)."""
    print("\n[🌙 ANALISTA NOCTURNO] Iniciando barrido de cola_analisis...")
    
    db = SessionLocal()
    try:
        pendientes = db.query(ColaAnalisis).all()
        
        if not pendientes:
            print("[🌙 ANALISTA NOCTURNO] Cola vacía. Nada que procesar.")
            return
        
        print(f"[🌙 ANALISTA NOCTURNO] {len(pendientes)} charlas a procesar.")
        
        for cola in pendientes:
            try:
                cliente = db.query(Cliente).filter(Cliente.id == cola.cliente_id).first()
                if not cliente:
                    db.delete(cola)
                    continue
                
                hace_24_horas = datetime.utcnow() - timedelta(hours=24)
                mensajes = db.query(Mensaje).filter(
                    Mensaje.cliente_id == cliente.id,
                    Mensaje.fecha_creacion >= hace_24_horas
                ).order_by(Mensaje.fecha_creacion.asc()).all()
                
                if not mensajes:
                    db.delete(cola)
                    continue
                
                historial_text = "\n".join([
                    f"{'CLIENTE' if m.rol == 'usuario' else 'SECRETARIA'}: {m.texto}"
                    for m in mensajes
                ])
                
                datos_actuales = cliente.datos_extraidos or {}
                
                tool_analisis = {
                    "name": "analisis_charla",
                    "description": "Clasifica el estado de la conversación del día.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "estado_charla": {
                                "type": "string",
                                "enum": ["cerrada", "en_progreso", "fria", "perdida"],
                                "description": "Estado actual de la conversación."
                            },
                            "resumen_nocturno": {
                                "type": "string",
                                "description": "Resumen del día en 1-3 líneas."
                            }
                        },
                        "required": ["estado_charla", "resumen_nocturno"]
                    }
                }

                system_prompt = f"""Sos un analista interno. NO hablás con el paciente.
Leé el historial del día y clasificá la conversación.

Contexto previo del paciente:
{datos_actuales.get('resumen_situacion', 'Sin contexto previo')}

ESTADOS:
- cerrada: El paciente agendó turno o su consulta fue resuelta.
- en_progreso: Hay interés activo, la charla sigue viva.
- fria: El paciente dejó de responder o mostró poco interés.
- perdida: El paciente rechazó explícitamente o pidió no ser contactado.

Usá la herramienta 'analisis_charla'. El resumen debe ser de 1 a 3 líneas."""

                response = client_claude.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=300,
                    system=system_prompt,
                    tools=[tool_analisis],
                    tool_choice={"type": "tool", "name": "analisis_charla"},
                    messages=[{"role": "user", "content": f"Historial del día:\n{historial_text}"}]
                )
                
                for block in response.content:
                    if block.type == "tool_use":
                        resultado = block.input
                        estado  = resultado.get("estado_charla", "en_progreso")
                        resumen = resultado.get("resumen_nocturno", "")

                        # Merge explícito: solo escribe los campos del nocturno,
                        # nunca pisa resumen_situacion (del analista de transición).
                        datos_actuales["estado_charla"]    = estado
                        datos_actuales["resumen_nocturno"] = resumen
                        cliente.datos_extraidos = datos_actuales

                        print(f"[🌙] {cliente.telefono}: {estado} — {resumen}")
                        
                        if estado in ("fria", "perdida"):
                            manana_14hs = datetime.utcnow().replace(
                                hour=17, minute=0, second=0, microsecond=0
                            ) + timedelta(days=1)
                            seguimiento = Seguimiento(
                                cliente_id=cliente.id,
                                estado="pendiente",
                                fecha_programada=manana_14hs
                            )
                            db.add(seguimiento)
                            print(f"[📋 SEGUIMIENTO] Creado para {cliente.telefono} → {manana_14hs}")
                
                db.delete(cola)
                db.commit()
                
            except Exception as e:
                print(f"[❌ ERROR procesando cliente {cola.cliente_id[:8]}]: {e}")
                continue
        
        print("[🌙 ANALISTA NOCTURNO] Barrido completado.\n")
        
    except Exception as e:
        print(f"[❌ ERROR ANALISTA NOCTURNO]: {e}")
    finally:
        db.close()
