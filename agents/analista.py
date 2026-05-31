# ===================================================================
# ANALISTA DE TRANSICIÓN
# Se dispara SOLO cuando Abby va a derivar (agendadora, cobranzas, Walter).
# Solo escribe datos_extraidos["resumen_situacion"] — no toca columnas directas.
# ===================================================================
import logging
from database import SessionLocal
from models import Cliente, Mensaje
from datetime import datetime, timedelta
from agents.herramientas_secretarias import client_claude


async def secretaria_resumen(db, cliente) -> str:
    """
    Genera un resumen de 1-3 líneas de la conversación actual y lo guarda en
    datos_extraidos["resumen_situacion"]. NO toca ningún otro campo.
    """
    hace_6_horas = datetime.utcnow() - timedelta(hours=6)
    mensajes = (
        db.query(Mensaje)
        .filter(Mensaje.cliente_id == cliente.id, Mensaje.fecha_creacion >= hace_6_horas)
        .order_by(Mensaje.fecha_creacion.asc())
        .all()
    )

    if not mensajes:
        logging.warning("[🔍 ANALISTA] Sin historial reciente — se omite el resumen.")
        return "Sin historial reciente."

    historial_text = "\n".join([
        f"{'CLIENTE' if m.rol == 'usuario' else 'SECRETARIA'}: {m.texto}"
        for m in mensajes
    ])

    tool_resumen = {
        "name": "guardar_resumen",
        "description": "Guarda el resumen del estado actual de la conversación.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resumen_situacion": {
                    "type": "string",
                    "description": "Resumen de MÁXIMO 3 líneas: qué quiere el paciente y en qué punto está la charla."
                }
            },
            "required": ["resumen_situacion"]
        }
    }

    system_prompt = (
        "Sos un analista interno. NO hablás con el paciente.\n"
        "Tu único trabajo: leer el historial y escribir un resumen de 1 a 3 líneas "
        "explicando qué quiere el paciente y en qué punto está la conversación.\n"
        "Usá la herramienta 'guardar_resumen'."
    )

    try:
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            system=system_prompt,
            tools=[tool_resumen],
            tool_choice={"type": "tool", "name": "guardar_resumen"},
            messages=[{"role": "user", "content": f"Historial:\n{historial_text}"}]
        )

        for block in response.content:
            if block.type == "tool_use":
                resumen = block.input.get("resumen_situacion", "")
                if resumen:
                    datos = dict(cliente.datos_extraidos or {})
                    datos["resumen_situacion"] = resumen   # único campo que escribe este analista
                    cliente.datos_extraidos = datos
                    db.commit()
                    logging.warning(f"[🔍 ANALISTA] Resumen guardado: {resumen}")
                    return resumen

        logging.warning("[🔍 ANALISTA] No se generó resumen.")
        return "Sin resumen."

    except Exception as e:
        logging.warning(f"[❌ ANALISTA ERROR]: {e}")
        return f"Error al generar resumen: {e}"
