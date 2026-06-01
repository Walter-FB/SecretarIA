from models import Mensaje, Seguimiento
from datetime import datetime, timedelta, timezone
from agents.herramientas_secretarias import client_claude
import logging


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

_TOOL_ANALISIS = {
    "name": "analisis_charla",
    "description": "Clasifica el estado de la conversación.",
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
                "description": "Resumen en 1-3 líneas."
            }
        },
        "required": ["estado_charla", "resumen_nocturno"]
    }
}

_SYSTEM_PROMPT = """Sos un analista interno. NO hablás con el paciente.
Leé el historial y clasificá la conversación.

Contexto previo:
{contexto}

ESTADOS:
- cerrada: El paciente agendó turno o su consulta fue resuelta.
- en_progreso: Hay interés activo, la charla sigue viva.
- fria: El paciente dejó de responder o mostró poco interés.
- perdida: El paciente rechazó explícitamente o pidió no ser contactado.

Usá la herramienta 'analisis_charla'. El resumen debe ser 1-3 líneas."""


async def analizar_y_registrar(cliente, db) -> None:
    """
    Analiza la conversación reciente del cliente con IA, guarda el resultado
    en datos_extraidos y crea un seguimiento si la charla está fría o perdida.
    Usada tanto por job_seguimiento (dinámico) como por analista_nocturno (fallback).
    """
    hace_24_horas = _ahora() - timedelta(hours=24)
    mensajes = (
        db.query(Mensaje)
        .filter(Mensaje.cliente_id == cliente.id, Mensaje.fecha_creacion >= hace_24_horas)
        .order_by(Mensaje.fecha_creacion.asc())
        .all()
    )
    if not mensajes:
        return

    historial = "\n".join(
        f"{'CLIENTE' if m.rol == 'usuario' else 'SECRETARIA'}: {m.texto}"
        for m in mensajes
    )
    datos = cliente.datos_extraidos or {}
    system = _SYSTEM_PROMPT.format(contexto=datos.get("resumen_situacion", "Sin contexto previo"))

    try:
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=system,
            tools=[_TOOL_ANALISIS],
            tool_choice={"type": "tool", "name": "analisis_charla"},
            messages=[{"role": "user", "content": f"Historial:\n{historial}"}]
        )
    except Exception as e:
        logging.warning(f"[❌ ANALISIS] Claude error para {cliente.telefono}: {e}")
        return

    for block in response.content:
        if block.type != "tool_use":
            continue

        estado  = block.input.get("estado_charla", "en_progreso")
        resumen = block.input.get("resumen_nocturno", "")

        datos["estado_charla"]    = estado
        datos["resumen_nocturno"] = resumen
        cliente.datos_extraidos   = datos
        cliente.mensajes_enviados = 0  # reset diario del contador anti-spam

        logging.warning(f"[ANALISIS] {cliente.telefono}: {estado} — {resumen}")

        if estado in ("fria", "perdida"):
            # Programar remarketing para mañana a las 14hs ARG (17:00 UTC)
            manana_14hs = (_ahora() + timedelta(days=1)).replace(
                hour=17, minute=0, second=0, microsecond=0
            )
            db.add(Seguimiento(
                cliente_id       = cliente.id,
                estado           = "pendiente",
                fecha_programada = manana_14hs,
            ))
            logging.warning(f"[ANALISIS] Remarketing creado para {cliente.telefono} → {manana_14hs}")

    db.commit()
