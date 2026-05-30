import unicodedata
import re
import logging

from services.secretaria_principal import enviar_mensaje_wpp, enviar_notificacion_a_walter, marcar_leido_wpp
from services.profesionales import (
    get_profesional_by_nombre,
    get_profesional_by_especialidad,
    get_tarifa,
    _normalizar_especialidad,
)
from database import SessionLocal
from models import Cliente

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

PAGO_INFO = {
    "titular": "Juan Manuel Barros Ferreyra",
    "alias":   "juan9910",
    "cvu":     "124235243423432432",
}


def _normalizar_cobertura(cobertura: str):
    if not cobertura:
        return "particular", None
    texto_lower = cobertura.strip().lower()
    if "particular" in texto_lower:
        return "particular", None
    return "obra social", cobertura.strip()


def _resolver_profesional(db, especialidad: str | None):
    """Devuelve el objeto Profesional más apropiado dado un string de especialidad o nombre."""
    if not especialidad:
        return get_profesional_by_especialidad(db, "psicologo")

    # Intentar primero por nombre (ej: "Lic. Renals", "Dr. Barros")
    por_nombre = get_profesional_by_nombre(db, especialidad)
    if por_nombre:
        return por_nombre

    # Fallback por especialidad normalizada
    return get_profesional_by_especialidad(db, _normalizar_especialidad(especialidad))


def generar_mensaje_cobro(db, especialidad: str = None, cobertura: str = None, obra_social: str = None) -> str:
    profesional = _resolver_profesional(db, especialidad)
    modalidad, obra_social_nombre = _normalizar_cobertura(cobertura)
    if modalidad == "obra social" and obra_social:
        obra_social_nombre = obra_social.strip()

    monto           = get_tarifa(profesional, modalidad)
    precio_particular = profesional.tarifa_particular if profesional else monto
    esp_display       = "Psicólogo" if (not profesional or profesional.especialidad == "psicologo") else "Psiquiatra"
    prof_nombre       = profesional.nombre if profesional else esp_display

    mensaje = f"Te paso el detalle para abonar tu consulta con {prof_nombre}:\n\n"

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
    mensaje += "Una vez hecha la transferencia, mandanos el comprobante por este chat. ¡Gracias!"

    return mensaje


async def iniciar_cobranzas(
    to_number: str,
    especialidad: str = None,
    cobertura: str = None,
    obra_social: str = None,
    detalle_turno: str = None,
) -> str:
    """
    Envía instrucciones de pago y, si hay turno confirmado, gestiona el email.
    Retorna el próximo estado_agente: 'principal' o 'esperando_mail'.
    """
    logging.warning(f"[💸 COBRANZAS] Iniciando para {to_number} | especialidad={especialidad} | cobertura={cobertura} | detalle_turno={detalle_turno}")
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()

        # Completar cobertura desde BD si no llegó por parámetro
        if cliente and not cobertura:
            cobertura = cliente.obra_social
            logging.warning(f"[💸 COBRANZAS] Cobertura tomada de BD: {cobertura}")

        # Completar especialidad desde profesional_id si no llegó por parámetro
        if cliente and not especialidad and cliente.profesional_id:
            from models import Profesional
            prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
            if prof:
                especialidad = prof.nombre
                logging.warning(f"[💸 COBRANZAS] Especialidad tomada de profesional del cliente: {especialidad}")

        # Guardar detalle de turno para el email de confirmación
        if cliente and detalle_turno:
            datos = dict(cliente.datos_extraidos or {})
            prof  = _resolver_profesional(db, especialidad)
            datos["ultimo_turno"]       = detalle_turno
            datos["especialidad_turno"] = prof.especialidad if prof else _normalizar_especialidad(especialidad)
            cliente.datos_extraidos = datos
            db.commit()
            logging.warning(f"[💸 COBRANZAS] Turno guardado: {detalle_turno}")

        mensaje = generar_mensaje_cobro(db, especialidad, cobertura, obra_social)
        await enviar_mensaje_wpp(to_number, mensaje)
        logging.warning(f"[💸 COBRANZAS] Mensaje de cobro enviado a {to_number}")

        nombre = "un paciente"
        if cliente:
            nombre = cliente.nombre_completo or (cliente.datos_extraidos or {}).get("nombre_contacto", "un paciente")

        await enviar_notificacion_a_walter(to_number, nombre)
        logging.warning(f"[💸 COBRANZAS] Notificación enviada a Walter")

        # Email: solo si hay turno confirmado
        if detalle_turno:
            if cliente and cliente.mail:
                logging.warning(f"[📧 COBRANZAS] Mail ya registrado ({cliente.mail}), enviando confirmación.")
                await _enviar_email_confirmacion(cliente, detalle_turno, especialidad, db)
                return "principal"
            else:
                logging.warning(f"[📧 COBRANZAS] Sin mail, pidiendo al cliente.")
                await enviar_mensaje_wpp(
                    to_number,
                    "Por último, para enviarte la confirmación del turno por mail, "
                    "¿me podés pasar tu dirección de correo electrónico?"
                )
                return "esperando_mail"

        return "principal"

    finally:
        db.close()


async def _enviar_email_confirmacion(cliente: Cliente, detalle_turno: str, especialidad: str = None, db=None) -> None:
    from services.mail_confirmacion import enviar_mail_confirmacion

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        datos = cliente.datos_extraidos or {}
        prof  = _resolver_profesional(db, especialidad or datos.get("especialidad_turno"))
        esp   = prof.especialidad if prof else _normalizar_especialidad(especialidad)

        cobertura  = cliente.obra_social or "particular"
        modalidad, _ = _normalizar_cobertura(cobertura)
        monto        = get_tarifa(prof, modalidad)
        precio_lista = prof.tarifa_particular if prof else monto
        descuento    = (precio_lista - monto) if modalidad == "obra social" else None

        logging.warning(f"[📧 MAIL] Preparando envío a {cliente.mail} | nombre={cliente.nombre_completo} | monto={monto}")
        ok = await enviar_mail_confirmacion(
            mail_destino     = cliente.mail,
            nombre           = cliente.nombre_completo or datos.get("nombre_contacto", "Paciente"),
            especialidad     = esp,
            detalle_turno    = detalle_turno,
            obra_social      = cliente.obra_social,
            dni              = cliente.dni,
            numero_afiliado  = cliente.numero_afiliado,
            fecha_nacimiento = cliente.fecha_nacimiento,
            monto            = monto,
            descuento        = descuento,
            precio_lista     = precio_lista if descuento else None,
        )
        level = "✅" if ok else "❌"
        logging.warning(f"[📧 MAIL] {level} Envío a {cliente.mail}: {'OK' if ok else 'FALLÓ'}")
    finally:
        if close_db:
            db.close()


async def handler_esperando_mail(user_text: str, to_number: str, msg_id: str = None) -> None:
    """Maneja el estado 'esperando_mail': valida el email, lo guarda y envía confirmación."""
    logging.warning(f"[📧 ESPERANDO_MAIL] Recibido de {to_number}: '{user_text}'")
    await marcar_leido_wpp(msg_id)

    texto = user_text.strip()
    db    = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
            logging.warning(f"[📧 ESPERANDO_MAIL] ⚠️ Cliente {to_number} no encontrado.")
            return

        if _EMAIL_RE.match(texto):
            logging.warning(f"[📧 ESPERANDO_MAIL] Email válido: {texto}")
            cliente.mail          = texto
            cliente.estado_agente = "principal"
            db.commit()

            await enviar_mensaje_wpp(to_number, "¡Perfecto! Ya te envío la confirmación del turno a ese correo. ¡Hasta pronto!")

            datos        = cliente.datos_extraidos or {}
            detalle      = datos.get("ultimo_turno", "Tu próximo turno en Clínica Abriness")
            especialidad = datos.get("especialidad_turno")
            logging.warning(f"[📧 ESPERANDO_MAIL] Disparando email | detalle={detalle}")
            await _enviar_email_confirmacion(cliente, detalle, especialidad, db)

        else:
            logging.warning(f"[📧 ESPERANDO_MAIL] Email inválido: '{texto}'")
            await enviar_mensaje_wpp(
                to_number,
                "Hmm, eso no parece un email válido 🤔\n"
                "Por favor enviame tu correo electrónico (ej: nombre@gmail.com)."
            )
    finally:
        db.close()
