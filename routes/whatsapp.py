from fastapi import APIRouter, Request, Response, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Cliente
import os

# env
VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "secretarIA")

router = APIRouter()
LIMITE_MENSAJES = 20


# ===================================================================
# 1. VERIFICACIÓN DEL WEBHOOK (Meta lo pide una vez)
# ===================================================================
@router.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de verificación", status_code=403)


# ===================================================================
# 2. RECEPCIÓN DE MENSAJES — EL SWITCH/ENRUTADOR
# Lee estado_agente del cliente y rutea al agente correcto
# ===================================================================
@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()

    try:
        # Extraemos el mensaje (Estructura estándar de Meta 2026)
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return Response(content="OK", status_code=200)

        message = entry["messages"][0]
        phone_number = message["from"]

        # Solo procesamos mensajes de texto
        if "text" not in message:
            return Response(content="OK", status_code=200)

        text = message["text"]["body"]
        msg_id = message.get("id")

        # -------------------------------------------------------
        # COMANDO SECRETO PARA TESTEO: /borrarChat
        # -------------------------------------------------------
        if text.strip() == "/borrarChat":
            db = SessionLocal()
            try:
                cliente_borrar = db.query(Cliente).filter(Cliente.telefono == phone_number).first()
                if cliente_borrar:
                    db.delete(cliente_borrar)
                    db.commit()
                    print(f"🧹 [RESET] Cliente {phone_number} eliminado de la base de datos.")
                    # Avisarle por WhatsApp
                    from services.secretaria_principal import enviar_mensaje_wpp
                    background_tasks.add_task(enviar_mensaje_wpp, phone_number, "✅ Memoria borrada con éxito. Soy un bot 100% nuevo. Mandame un mensaje para arrancar de cero.")
                return Response(content="OK", status_code=200)
            except Exception as e:
                print(f"Error al borrar cliente: {e}")
            finally:
                db.close()

        # -------------------------------------------------------
        # ANTI-SPAM: Leer contador desde PostgreSQL
        # -------------------------------------------------------
        db = SessionLocal()
        try:
            cliente = db.query(Cliente).filter(Cliente.telefono == phone_number).first()
            mensajes_enviados = cliente.mensajes_enviados if cliente else 0
            # IMPORTANTE: Cambiamos a 'agendadora' por defecto si el cliente no existe
            estado_agente = cliente.estado_agente if cliente else "agendadora"
        finally:
            db.close()

        if mensajes_enviados >= LIMITE_MENSAJES:
            print(f"[⛔ {phone_number}] Bloqueado (límite de {LIMITE_MENSAJES} mensajes).")
            return Response(content="OK", status_code=200)

        # -------------------------------------------------------
        # EL SWITCH — Rutea según estado_agente
        # -------------------------------------------------------
        print(f"🚦 [ROUTER] Mensaje recibido de {phone_number}. Estado: {estado_agente}")
        
        if estado_agente == "principal":
            print(f"➡️ [ROUTER] Derivando a secretaria_principal")
            from services.secretaria_principal import secretaria_principal
            background_tasks.add_task(secretaria_principal, text, phone_number, msg_id)

        elif estado_agente == "agendadora":
            print(f"➡️ [ROUTER] Derivando a secretaria_agendadora")
            from services.agendadora import secretaria_agendadora
            background_tasks.add_task(secretaria_agendadora, text, phone_number, msg_id)

        elif estado_agente == "manual":
            print(f"[🖐️ MANUAL] Cliente {phone_number} en modo manual. Ignorando mensaje.")

        else:
            print(f"[⚠️ WARNING] estado_agente desconocido: '{estado_agente}' para {phone_number}")

    except Exception as e:
        print(f"Error parseando JSON o evento no es un mensaje: {e}")

    # Obligatorio devolver 200 rápido a Meta
    return Response(content="OK", status_code=200)


# ===================================================================
# ENDPOINT PARA VER CLIENTES DESDE POSTGRESQL
# ===================================================================
@router.get("/ver_clientes")
async def ver_clientes():
    db = SessionLocal()
    try:
        clientes = db.query(Cliente).all()
        resultado = []
        for c in clientes:
            resultado.append({
                "id": c.id,
                "telefono": c.telefono,
                "estado_agente": c.estado_agente,
                "mensajes_enviados": c.mensajes_enviados,
                "es_confianza": c.es_confianza,
                "datos_extraidos": c.datos_extraidos or {},
            })
        return {"total": len(resultado), "clientes": resultado}
    finally:
        db.close()
