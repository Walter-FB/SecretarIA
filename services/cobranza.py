import unicodedata
import re
import logging

from services.secretaria_principal import enviar_mensaje_wpp, enviar_notificacion_a_walter, marcar_leido_wpp
from database import SessionLocal
from models import Cliente

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

TARIFAS = {
    "psicologo": {"particular": 30000, "obra social": 19000},
    "psiquiatra": {"particular": 80000, "obra social": 45000},
}

PAGO_INFO = {
    "titular": "Juan Manuel Barros Ferreyra",
    "alias": "juan9910",
    "cvu": "124235243423432432",
}


def _normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto_norm = unicodedata.normalize("NFKD", texto)
    texto_norm = texto_norm.encode("ascii", "ignore").decode("ascii")
    return texto_norm.strip().lower()


def _normalizar_especialidad(especialidad: str) -> str:
    if not especialidad:
        return "psicologo"

    valor = _normalizar_texto(especialidad)
    if "psiquiatra" in valor or "psiquiatria" in valor or "dr. barros" in valor:
        return "psiquiatra"
    if "psicologo" in valor or "psicologia" in valor or "lic." in valor or "lic " in valor or "renals" in valor:
        return "psicologo"
    return "psicologo"


def _normalizar_cobertura(cobertura: str):
    if not cobertura:
        return "particular", None

    texto = cobertura.strip()
    texto_lower = texto.lower()
    if "particular" in texto_lower:
        return "particular", None

    return "obra social", texto


def _calcular_tarifa(especialidad: str, modalidad: str) -> int:
    especialidad_norm = _normalizar_especialidad(especialidad)
    modalidad_norm = modalidad.strip().lower() if modalidad else "particular"
    if especialidad_norm not in TARIFAS:
        especialidad_norm = "psicologo"
    if modalidad_norm not in TARIFAS[especialidad_norm]:
        modalidad_norm = "particular"

    return TARIFAS[especialidad_norm][modalidad_norm]


def generar_mensaje_cobro(especialidad: str = None, cobertura: str = None, obra_social: str = None) -> str:
    especialidad_norm = _normalizar_especialidad(especialidad)
    modalidad, obra_social_nombre = _normalizar_cobertura(cobertura)
    if modalidad == "obra social" and obra_social:
        obra_social_nombre = obra_social.strip()

    precio_particular = TARIFAS[especialidad_norm]["particular"]
    monto = _calcular_tarifa(especialidad_norm, modalidad)

    esp_display = "Psicólogo" if especialidad_norm == "psicologo" else "Psiquiatra"
    mensaje = f"Te paso el detalle para abonar tu consulta con el {esp_display}:\n\n"

    if modalidad == "obra social":
        obra_social_nombre = obra_social_nombre or cobertura or "Obra Social"
        descuento = precio_particular - monto
        mensaje += f"Precio de lista: ${precio_particular:,}\n"
        mensaje += f"Descuento {obra_social_nombre}: -${descuento:,}\n"
        mensaje += f"Total a pagar: ${monto:,}\n\n"
    else:
        mensaje += f"Total a pagar: ${monto:,}\n\n"

    mensaje += "Datos para transferir:\n"
    mensaje += f"• Alias: {PAGO_INFO['alias']}\n"
    mensaje += f"• Titular: {PAGO_INFO['titular']}\n"
    mensaje += f"• CVU: {PAGO_INFO['cvu']}\n\n"
    mensaje += "Una vez hecha la transferencia, mandanos el comprobante por este chat para registrar el pago. ¡Gracias!"

    return mensaje


async def iniciar_cobranzas(
    to_number: str,
    especialidad: str = None,
    cobertura: str = None,
    obra_social: str = None,
    detalle_turno: str = None,
) -> str:
    """
    Sends payment instructions and, if an appointment was confirmed (detalle_turno provided),
    either sends the confirmation email immediately or asks the client for their email first.
    Returns the next estado_agente: 'manual' or 'esperando_mail'.
    """
    logging.warning(f"[💸 COBRANZAS] Iniciando para {to_number} | especialidad={especialidad} | cobertura={cobertura} | detalle_turno={detalle_turno}")
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if cliente and not cobertura:
            cobertura = cliente.obra_social
            logging.warning(f"[💸 COBRANZAS] Cobertura tomada de DB: {cobertura}")

        # Persist turno details for the confirmation email
        if cliente and detalle_turno:
            datos = dict(cliente.datos_extraidos or {})
            datos["ultimo_turno"]     = detalle_turno
            datos["especialidad_turno"] = _normalizar_especialidad(especialidad)
            cliente.datos_extraidos   = datos
            db.commit()
            logging.warning(f"[💸 COBRANZAS] Turno guardado en datos_extraidos: {detalle_turno}")

        mensaje = generar_mensaje_cobro(especialidad, cobertura, obra_social)
        await enviar_mensaje_wpp(to_number, mensaje)
        logging.warning(f"[💸 COBRANZAS] Mensaje de cobro enviado a {to_number}")

        nombre = "un paciente"
        if cliente:
            if cliente.nombre_completo:
                nombre = cliente.nombre_completo
            elif cliente.datos_extraidos and "nombre_contacto" in cliente.datos_extraidos:
                nombre = cliente.datos_extraidos["nombre_contacto"]

        await enviar_notificacion_a_walter(to_number, nombre)
        logging.warning(f"[💸 COBRANZAS] Notificación enviada a Walter sobre {to_number}")

        # Email logic — only when an appointment was actually confirmed
        if detalle_turno:
            logging.warning(f"[📧 COBRANZAS] Turno confirmado, evaluando email para {to_number} | mail_guardado={cliente.mail if cliente else 'sin cliente'}")
            if cliente and cliente.mail:
                logging.warning(f"[📧 COBRANZAS] Mail ya registrado ({cliente.mail}), enviando confirmación directo.")
                await _enviar_email_confirmacion(cliente, detalle_turno, especialidad)
                return "manual"
            else:
                logging.warning(f"[📧 COBRANZAS] Sin mail registrado, pidiendo al cliente.")
                await enviar_mensaje_wpp(
                    to_number,
                    "Por último, para enviarte la confirmación del turno por mail, "
                    "¿me podés pasar tu dirección de correo electrónico?"
                )
                return "esperando_mail"
        else:
            logging.warning(f"[💸 COBRANZAS] Sin detalle_turno — solo consulta de precio, no se manda mail.")

        return "manual"
    finally:
        db.close()


async def _enviar_email_confirmacion(cliente: Cliente, detalle_turno: str, especialidad: str = None) -> None:
    from services.mail_confirmacion import enviar_mail_confirmacion
    datos = cliente.datos_extraidos or {}
    esp   = datos.get("especialidad_turno") or _normalizar_especialidad(especialidad)
    logging.warning(f"[📧 MAIL] Preparando envío a {cliente.mail} | nombre={cliente.nombre_completo} | especialidad={esp} | turno={detalle_turno}")
    ok = await enviar_mail_confirmacion(
        mail_destino    = cliente.mail,
        nombre          = cliente.nombre_completo or datos.get("nombre_contacto", "Paciente"),
        especialidad    = esp,
        detalle_turno   = detalle_turno,
        obra_social     = cliente.obra_social,
        dni             = cliente.dni,
        numero_afiliado = cliente.numero_afiliado,
        fecha_nacimiento = cliente.fecha_nacimiento,
    )
    if ok:
        logging.warning(f"[📧 MAIL] ✅ Confirmación enviada exitosamente a {cliente.mail}")
    else:
        logging.warning(f"[📧 MAIL] ❌ Falló el envío a {cliente.mail}")


async def handler_esperando_mail(user_text: str, to_number: str, msg_id: str = None) -> None:
    """Handles the 'esperando_mail' state: validates the email, saves it, sends confirmation."""
    logging.warning(f"[📧 ESPERANDO_MAIL] Recibido de {to_number}: '{user_text}'")
    await marcar_leido_wpp(msg_id)

    texto = user_text.strip()

    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
            logging.warning(f"[📧 ESPERANDO_MAIL] ⚠️ Cliente {to_number} no encontrado en DB.")
            return

        if _EMAIL_RE.match(texto):
            logging.warning(f"[📧 ESPERANDO_MAIL] Email válido recibido: {texto}. Guardando y enviando confirmación.")
            cliente.mail         = texto
            cliente.estado_agente = "manual"
            db.commit()

            await enviar_mensaje_wpp(
                to_number,
                "¡Perfecto! Ya te envío la confirmación del turno a ese correo. ¡Hasta pronto!"
            )

            datos        = cliente.datos_extraidos or {}
            detalle      = datos.get("ultimo_turno", "Tu próximo turno en Clínica Abriness")
            especialidad = datos.get("especialidad_turno")
            logging.warning(f"[📧 ESPERANDO_MAIL] Disparando email | detalle={detalle} | especialidad={especialidad}")
            await _enviar_email_confirmacion(cliente, detalle, especialidad)

        else:
            logging.warning(f"[📧 ESPERANDO_MAIL] Email inválido recibido: '{texto}'")
            await enviar_mensaje_wpp(
                to_number,
                "Hmm, eso no parece un email válido 🤔\n"
                "Por favor enviame tu correo electrónico (ej: nombre@gmail.com)."
            )
    finally:
        db.close()
