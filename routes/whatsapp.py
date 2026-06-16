from fastapi import APIRouter, Request, Response, BackgroundTasks
from database import SessionLocal
from models import Cliente, Mensaje, Empresa
import logging
import os
import re
import asyncio

VERIFY_TOKEN    = os.getenv("WEBHOOK_VERIFY_TOKEN", "secretarIA")
LIMITE_MENSAJES = 50
RESET_HORAS     = 24

router = APIRouter()

# Lock por cliente para serializar mensajes del mismo número
_locks: dict[str, asyncio.Lock] = {}

# Deduplicación de msg_id: Meta a veces re-entrega el mismo webhook
_processed_ids: set[str] = set()


def _get_lock(empresa_id: str, telefono: str) -> asyncio.Lock:
    key = f"{empresa_id}:{telefono}"
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


async def _run_locked(fn, *args, empresa_id: str, telefono: str):
    async with _get_lock(empresa_id, telefono):
        await fn(*args)


# ===================================================================
# 1. VERIFICACIÓN DEL WEBHOOK (Meta lo pide una vez)
# ===================================================================
@router.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de verificacion", status_code=403)


# ===================================================================
# 2. RECEPCIÓN DE MENSAJES — EL SWITCH/ENRUTADOR
# ===================================================================
@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()

    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" not in entry:
            return Response(content="OK", status_code=200)

        message        = entry["messages"][0]
        phone_number   = message["from"]
        phone_number_id = entry.get("metadata", {}).get("phone_number_id", "")

        # Deduplicar: ignorar si este msg_id ya fue procesado
        msg_id_check = message.get("id")
        if msg_id_check:
            if msg_id_check in _processed_ids:
                logging.warning(f"[ROUTER] Webhook duplicado ignorado: {msg_id_check}")
                return Response(content="OK", status_code=200)
            _processed_ids.add(msg_id_check)
            # Mantener el set acotado (últimos 500 IDs)
            if len(_processed_ids) > 500:
                _processed_ids.discard(next(iter(_processed_ids)))

        # ── Resolver empresa ─────────────────────────────────────────
        db = SessionLocal()
        try:
            from empresa_scope import EmpresaScope
            from init_db import EMPRESA_DEFAULT_ID
            empresa = EmpresaScope.empresa_por_phone_number_id(phone_number_id, db)
            if not empresa:
                # Fallback: empresa default (mientras phone_number_id no esté configurado en la BD)
                empresa = db.query(Empresa).filter(Empresa.id == EMPRESA_DEFAULT_ID).first()
            if not empresa:
                logging.warning(f"[ROUTER] Sin empresa para phone_number_id={phone_number_id}. Ignorando.")
                return Response(content="OK", status_code=200)
            empresa_id    = empresa.id
            numero_walter = empresa.numero_walter or ""
            bot_activo    = empresa.bot_activo
        finally:
            db.close()

        # ── Mensajes no-texto ─────────────────────────────────────────
        if "text" not in message:
            from agents.herramientas_secretarias import enviar_mensaje_wpp
            background_tasks.add_task(
                enviar_mensaje_wpp,
                phone_number,
                "Por ahora solo proceso mensajes de texto. "
                "Si queres enviar un comprobante de pago, escribi el numero de operacion "
                "o mandalo a Walter directamente."
            )
            return Response(content="OK", status_code=200)

        text   = message["text"]["body"]
        msg_id = message.get("id")

        # ── Empresa desactivada ───────────────────────────────────────
        if not bot_activo:
            logging.warning(f"[ROUTER] Empresa {empresa_id} bot_activo=False. Ignorando.")
            return Response(content="OK", status_code=200)

        # ── Comandos de control de Walter ─────────────────────────────
        if numero_walter and phone_number == numero_walter:
            stripped = text.strip()
            if stripped.startswith("/"):
                await _handle_control_command(stripped, empresa_id, numero_walter, background_tasks)
                return Response(content="OK", status_code=200)

        # ── Comando de testeo /borrarChat ─────────────────────────────
        if text.strip() == "/borrarChat":
            db = SessionLocal()
            try:
                c = db.query(Cliente).filter(
                    Cliente.telefono == phone_number,
                    Cliente.empresa_id == empresa_id
                ).first()
                if c:
                    db.delete(c)
                    db.commit()
                    logging.warning(f"[ROUTER] Cliente {phone_number} eliminado.")
                from agents.herramientas_secretarias import enviar_mensaje_wpp
                background_tasks.add_task(
                    enviar_mensaje_wpp, phone_number,
                    "Memoria borrada. Mandame un mensaje para arrancar de cero."
                )
            except Exception as e:
                logging.warning(f"[ROUTER] Error al borrar cliente: {e}")
            finally:
                db.close()
            return Response(content="OK", status_code=200)

        # ── Cargar estado del cliente ─────────────────────────────────
        db = SessionLocal()
        try:
            cliente = db.query(Cliente).filter(
                Cliente.telefono == phone_number,
                Cliente.empresa_id == empresa_id
            ).first()
            mensajes_enviados  = cliente.mensajes_enviados if cliente else 0
            estado_agente      = cliente.estado_agente     if cliente else "principal"
            bot_activo_cliente = cliente.bot_activo        if cliente else True
            cliente_id         = cliente.id                if cliente else None
        finally:
            db.close()

        # ── Checks de bloqueo ─────────────────────────────────────────
        if not bot_activo_cliente:
            logging.warning(f"[ROUTER] {phone_number} bot_activo=False (cliente). Guardando mensaje, no procesando.")
            if cliente_id:
                db = SessionLocal()
                try:
                    msg_obj = Mensaje(
                        cliente_id = cliente_id,
                        empresa_id = empresa_id,
                        rol        = "usuario",
                        texto      = text,
                    )
                    db.add(msg_obj)
                    db.commit()
                except Exception as e:
                    logging.warning(f"[ROUTER] Error guardando mensaje muteado: {e}")
                finally:
                    db.close()
            return Response(content="OK", status_code=200)

        if mensajes_enviados >= LIMITE_MENSAJES:
            # Reset si pasaron más de RESET_HORAS desde el último mensaje del usuario
            from datetime import datetime, timedelta
            db = SessionLocal()
            try:
                ultimo = (
                    db.query(Mensaje)
                    .filter(Mensaje.cliente_id == cliente_id, Mensaje.rol == "usuario")
                    .order_by(Mensaje.fecha_creacion.desc())
                    .first()
                )
                if ultimo and (datetime.utcnow() - ultimo.fecha_creacion) > timedelta(hours=RESET_HORAS):
                    c = db.query(Cliente).filter(Cliente.id == cliente_id).first()
                    if c:
                        c.mensajes_enviados = 0
                        db.commit()
                        mensajes_enviados = 0
                        logging.warning(f"[ROUTER] {phone_number} contador reseteado (inactividad >{RESET_HORAS}h)")
            finally:
                db.close()

        if mensajes_enviados >= LIMITE_MENSAJES:
            logging.warning(f"[ROUTER] {phone_number} bloqueado (limite {LIMITE_MENSAJES} mensajes).")
            return Response(content="OK", status_code=200)

        # ── Despacho al agente ────────────────────────────────────────
        logging.warning(f"[ROUTER] {phone_number} | empresa={empresa_id} | estado={estado_agente}")

        if estado_agente in ("principal", "agendadora"):
            from agents.abby import abby
            background_tasks.add_task(_run_locked, abby, text, phone_number, msg_id, empresa_id,
                                      empresa_id=empresa_id, telefono=phone_number)

        elif estado_agente == "esperando_mail":
            from services.cobranza import handler_esperando_mail
            background_tasks.add_task(_run_locked, handler_esperando_mail, text, phone_number, msg_id, empresa_id,
                                      empresa_id=empresa_id, telefono=phone_number)

        elif estado_agente == "manual":
            logging.warning(f"[ROUTER] {phone_number} en modo manual. Ignorando.")

        else:
            logging.warning(f"[ROUTER] Estado desconocido: '{estado_agente}' para {phone_number}")

        # Timer de seguimiento: se resetea en cada mensaje activo (excluye manual)
        if cliente_id and estado_agente != "manual":
            from agents.seguimiento import resetear_timer
            resetear_timer(cliente_id, phone_number, empresa_id)

    except Exception as e:
        logging.warning(f"[ROUTER ERROR]: {e}")
        import traceback; traceback.print_exc()

    return Response(content="OK", status_code=200)


def _buscar_cliente_normalizado(db, telefono_raw: str, empresa_id: str):
    """Busca un cliente ignorando formato del número (solo dígitos)."""
    digitos = re.sub(r'\D', '', telefono_raw)
    clientes = db.query(Cliente).filter(Cliente.empresa_id == empresa_id).all()
    for c in clientes:
        if re.sub(r'\D', '', c.telefono) == digitos:
            return c
    return None


async def _handle_control_command(cmd: str, empresa_id: str, numero_walter: str, background_tasks):
    """Procesa comandos de control enviados por Walter: /mute, /unmute, /estado, /ayuda."""
    from agents.herramientas_secretarias import enviar_mensaje_wpp

    parts  = cmd.split(maxsplit=1)
    accion = parts[0].lower()
    arg    = parts[1].strip() if len(parts) > 1 else ""

    # /ayuda ──────────────────────────────────────────────────────────
    if accion == "/ayuda":
        texto = (
            "Comandos disponibles:\n"
            "• /mute <numero>   — silencia el bot para ese cliente\n"
            "• /unmute <numero> — reactiva el bot para ese cliente\n"
            "• /estado <numero> — muestra estado actual del cliente\n"
            "• /ayuda           — esta lista"
        )
        background_tasks.add_task(enviar_mensaje_wpp, numero_walter, texto)
        return

    # Comandos que requieren <numero> ─────────────────────────────────
    if accion in ("/mute", "/unmute", "/estado"):
        if not arg:
            background_tasks.add_task(
                enviar_mensaje_wpp, numero_walter,
                f"Uso: {accion} <numero>"
            )
            return

        db = SessionLocal()
        try:
            cliente = _buscar_cliente_normalizado(db, arg, empresa_id)

            if not cliente:
                background_tasks.add_task(
                    enviar_mensaje_wpp, numero_walter,
                    f"[ERROR] No encontré al cliente {arg}."
                )
                return

            if accion == "/estado":
                mute_str  = "OFF (muteado)" if not cliente.bot_activo else "ON"
                nombre    = cliente.nombre_completo or "sin nombre"
                respuesta = (
                    f"Estado de {cliente.telefono}:\n"
                    f"• Nombre: {nombre}\n"
                    f"• Bot: {mute_str}\n"
                    f"• Agente: {cliente.estado_agente}"
                )
                background_tasks.add_task(enviar_mensaje_wpp, numero_walter, respuesta)

            elif accion in ("/mute", "/unmute"):
                cliente.bot_activo = (accion == "/unmute")
                db.commit()
                estado_str = "desmuteado ✅" if accion == "/unmute" else "muteado 🔇"
                background_tasks.add_task(
                    enviar_mensaje_wpp, numero_walter,
                    f"[OK] {cliente.telefono} {estado_str}."
                )
                logging.warning(f"[CONTROL] {cliente.telefono} -> bot_activo={cliente.bot_activo}")

        except Exception as e:
            logging.warning(f"[CONTROL ERROR]: {e}")
        finally:
            db.close()
        return

    # Comando desconocido ──────────────────────────────────────────────
    background_tasks.add_task(
        enviar_mensaje_wpp, numero_walter,
        f"Comando no reconocido: {accion}\nMandá /ayuda para ver los disponibles."
    )


# ===================================================================
# ENDPOINT PARA VER CONVERSACIÓN COMPLETA DE UN CLIENTE
# ===================================================================
@router.get("/conversacion/{telefono}")
async def ver_conversacion(telefono: str):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == telefono).first()
        if not cliente:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        mensajes = (
            db.query(Mensaje)
            .filter(Mensaje.cliente_id == cliente.id)
            .order_by(Mensaje.fecha_creacion.asc())
            .all()
        )
        return {
            "telefono": telefono,
            "estado_agente": cliente.estado_agente,
            "nombre": cliente.nombre_completo,
            "bot_activo": cliente.bot_activo,
            "datos_extraidos": cliente.datos_extraidos or {},
            "mensajes": [
                {"rol": m.rol, "texto": m.texto, "fecha": m.fecha_creacion.isoformat()}
                for m in mensajes
            ]
        }
    finally:
        db.close()


# ===================================================================
# ENDPOINT PARA VER CLIENTES
# ===================================================================
@router.get("/ver_clientes")
async def ver_clientes():
    db = SessionLocal()
    try:
        clientes = db.query(Cliente).all()
        return {
            "total": len(clientes),
            "clientes": [
                {
                    "id":               c.id,
                    "empresa_id":       c.empresa_id,
                    "telefono":         c.telefono,
                    "estado_agente":    c.estado_agente,
                    "bot_activo":       c.bot_activo,
                    "mensajes_enviados": c.mensajes_enviados,
                    "datos_extraidos":  c.datos_extraidos or {},
                }
                for c in clientes
            ]
        }
    finally:
        db.close()
