# ===================================================================
# ANALISTA NOCTURNO — Corre a las 21:00hs Argentina
# Barre cola_analisis, clasifica charlas, llena seguimientos, limpia cola.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje, ColaAnalisis, Seguimiento
from datetime import datetime, timedelta
import os
import anthropic

CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None


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
                    "description": "Clasifica y resume el estado de una conversación.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "nombre_contacto": {"type": "string", "description": "Nombre del cliente."},
                            "rubro_empresa": {"type": "string", "description": "Rubro o actividad."},
                            "necesidad_cliente": {"type": "string", "description": "Qué necesita resolver."},
                            "estado_charla": {
                                "type": "string",
                                "enum": ["cerrada", "en_progreso", "fria", "perdida"],
                                "description": "Estado actual de la charla de venta."
                            },
                            "resumen_situacion": {"type": "string", "description": "Resumen de MÁXIMO 3 líneas."}
                        },
                        "required": ["estado_charla", "resumen_situacion"]
                    }
                }
                
                system_prompt = f"""Sos un analista de ventas interno. NO hablás con el cliente.
Tu trabajo es leer el historial de esta conversación y clasificarla.

Datos previos del cliente:
{datos_actuales}

ESTADOS POSIBLES:
- cerrada: El cliente agendó reunión o se resolvió su consulta.
- en_progreso: Hay interés activo, la charla sigue viva.
- fria: El cliente dejó de responder o mostró poco interés.
- perdida: El cliente rechazó explícitamente o pidió no ser contactado.

INSTRUCCIONES:
1. Usá la herramienta 'analisis_charla'.
2. Si un dato ya existe y es correcto, mandalo igual.
3. Clasificá el estado según la definición de arriba.
4. El resumen debe ser de 1 a 3 líneas."""

                response = client_claude.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=300,
                    system=system_prompt,
                    tools=[tool_analisis],
                    tool_choice={"type": "tool", "name": "analisis_charla"},
                    messages=[{"role": "user", "content": f"Historial del día:\n{historial_text}"}]
                )
                
                for block in response.content:
                    if block.type == "tool_use":
                        resultado = block.input
                        estado = resultado.get("estado_charla", "en_progreso")
                        resumen = resultado.get("resumen_situacion", "Sin resumen")
                        
                        datos_actuales.update(resultado)
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
