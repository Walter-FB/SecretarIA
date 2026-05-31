import httpx
import logging
from models import ColaAnalisis
from datetime import datetime
from sqlalchemy.dialects.postgresql import insert as pg_insert
import os
import anthropic

WPP_TOKEN     = os.getenv("WHATSAPP_TOKEN")
PHONE_ID      = os.getenv("PHONE_NUMBER_ID")
CLAUDE_KEY    = os.getenv("CLAUDE_API_KEY")
NUMERO_WALTER = "5491131720843"

client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None


async def enviar_mensaje_wpp(to_number: str, texto: str):
    url     = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to":   to_number,
        "type": "text",
        "text": {"body": texto}
    }
    async with httpx.AsyncClient() as http:
        r = await http.post(url, json=payload, headers=headers)
        if r.status_code != 200:
            logging.warning(f"[META {r.status_code}] {r.text}")


async def marcar_leido_wpp(msg_id: str):
    if not msg_id:
        return
    url     = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
    try:
        async with httpx.AsyncClient() as http:
            await http.post(url, json=payload, headers=headers)
    except Exception:
        pass


def upsert_cola_analisis(db, cliente_id: str):
    stmt = pg_insert(ColaAnalisis).values(
        cliente_id=cliente_id,
        fecha_ultima_actividad=datetime.utcnow()
    ).on_conflict_do_update(
        index_elements=['cliente_id'],
        set_={'fecha_ultima_actividad': datetime.utcnow()}
    )
    db.execute(stmt)
    db.commit()
    logging.warning(f"[COLA] Upsert cola_analisis para {cliente_id[:8]}...")


async def enviar_notificacion_a_walter(numero_cliente: str, nombre_cliente: str, numero_walter: str = None):
    destino = numero_walter or NUMERO_WALTER
    mensaje_walter = (
        f"Cliente interezado!\nHola Walter! 🥰 Te informo que el numero {{{numero_cliente}}} "
        f"a nombre de {{{nombre_cliente}}} estaría interesado en contactarte. Háblale, suerte y saludos! 👋"
    )
    try:
        await enviar_mensaje_wpp(destino, mensaje_walter)
        logging.warning("[NOTIFICACIÓN] Walter avisado.")
    except Exception as e:
        logging.warning(f"[NOTIFICACIÓN WALTER ERROR]: {e}")
