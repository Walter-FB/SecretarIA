# ===================================================================
# ANALISTA DE RESUMEN
# Se dispara SOLO cuando la Principal va a escalar o transferir.
# Walter nunca recibe un ping sin contexto.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje
from datetime import datetime, timedelta
import os
import anthropic

CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")
client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None


async def secretaria_resumen(db, cliente) -> str:
    """
    Genera un resumen de 3 líneas de la conversación actual.
    Usa Haiku (barato). Se llama antes de escalar a Walter o transferir a la Agendadora.
    Guarda el resumen en datos_extraidos del cliente.
    """
    hace_6_horas = datetime.utcnow() - timedelta(hours=6)
    mensajes = db.query(Mensaje).filter(
        Mensaje.cliente_id == cliente.id,
        Mensaje.fecha_creacion >= hace_6_horas
    ).order_by(Mensaje.fecha_creacion.asc()).all()
    
    if not mensajes:
        return "Sin historial reciente para resumir."
    
    historial_text = "\n".join([
        f"{'CLIENTE' if m.rol == 'usuario' else 'SECRETARIA'}: {m.texto}"
        for m in mensajes
    ])
    
    datos_actuales = cliente.datos_extraidos or {}
    
    tool_resumen = {
        "name": "guardar_resumen",
        "description": "Guarda el análisis del estado actual de la conversación.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_contacto": {"type": "string", "description": "Nombre del cliente si se mencionó."},
                "rubro_empresa": {"type": "string", "description": "Rubro o actividad del cliente."},
                "necesidad_cliente": {"type": "string", "description": "Qué problema necesita resolver."},
                "resumen_situacion": {"type": "string", "description": "Resumen de MÁXIMO 3 líneas: qué quiere el cliente y en qué punto está la charla."}
            },
            "required": ["resumen_situacion"]
        }
    }
    
    system_prompt = f"""Sos un analista de datos interno. NO hablás con el cliente.
Tu único trabajo es leer el historial y completar la ficha del cliente.

Datos que ya teníamos:
{datos_actuales}

INSTRUCCIONES:
1. Usá la herramienta 'guardar_resumen'.
2. Si un dato ya existe y es correcto, mandalo igual.
3. Si detectás un dato nuevo, actualizalo.
4. El resumen_situacion debe ser de 1 a 3 líneas máximo."""
    
    try:
        response = client_claude.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            system=system_prompt,
            tools=[tool_resumen],
            tool_choice={"type": "tool", "name": "guardar_resumen"},
            messages=[{"role": "user", "content": f"Historial a analizar:\n{historial_text}"}]
        )
        
        for block in response.content:
            if block.type == "tool_use":
                nuevos_datos = block.input
                datos_actuales.update(nuevos_datos)
                cliente.datos_extraidos = datos_actuales
                db.commit()
                resumen = nuevos_datos.get("resumen_situacion", "Sin resumen")
                print(f"[🔍 ANALISTA RESUMEN] {resumen}")
                return resumen
        
        return "Analista no generó resumen."
        
    except Exception as e:
        print(f"[❌ ERROR ANALISTA RESUMEN]: {e}")
        return f"Error al generar resumen: {str(e)}"
