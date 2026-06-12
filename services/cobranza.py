import unicodedata
import re
import logging

from agents.herramientas_secretarias import enviar_mensaje_wpp, enviar_notificacion_a_walter, marcar_leido_wpp
from services.profesionales import (
    get_profesional_by_nombre,
    get_profesional_by_especialidad,
    get_tarifa,
    _normalizar_especialidad,
)
from database import SessionLocal
from models import Cliente, Mensaje

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

PAGO_INFO = {
    "titular": "Walter Franco Berrutto",
    "alias":   "walter.mate3",
    "cvu":     "",
}


def _normalizar_cobertura(cobertura: str):
    if not cobertura:
        return "particular", None
    texto_lower = cobertura.strip().lower()
    if "particular" in texto_lower:
        return "particular", None
    return "obra social", cobertura.strip()


def _resolver_profesional(db, especialidad: str | None, empresa_id: str = None):
    """Devuelve el objeto Profesional más apropiado dado un string de especialidad o nombre."""
    if not especialidad:
        return get_profesional_by_especialidad(db, "psicologo", empresa_id)

    por_nombre = get_profesional_by_nombre(db, especialidad, empresa_id)
    if por_nombre:
        return por_nombre

    return get_profesional_by_especialidad(db, _normalizar_especialidad(especialidad), empresa_id)


def generar_mensaje_cobro(db, especialidad: str = None, cobertura: str = None, obra_social: str = None, empresa=None) -> str:
    empresa_id  = empresa.id if empresa else None
    profesional = _resolver_profesional(db, especialidad, empresa_id)
    modalidad, obra_social_nombre = _normalizar_cobertura(cobertura)
    if modalidad == "obra social" and obra_social:
        obra_social_nombre = obra_social.strip()

    os_nombre = obra_social_nombre or (cobertura if modalidad == "obra social" else None)
    monto     = get_tarifa(profesional, modalidad, os_nombre)
    precio_particular = profesional.tarifa_particular if profesional else monto
    esp_display       = "Psicólogo" if (not profesional or profesional.especialidad == "psicologo") else "Psiquiatra"
    prof_nombre       = profesional.nombre if profesional else esp_display

    mensaje = f"Te paso el detalle para abonar tu consulta con {prof_nombre}:\n\n"

    if modalidad == "obra social":
        os_display = os_nombre or "Obra Social"
        descuento  = precio_particular - monto
        mensaje += f"Precio de lista: ${precio_particular:,}\n"
        mensaje += f"Descuento {os_display}: -${descuento:,}\n"
        mensaje += f"Total a pagar: ${monto:,}\n\n"
    else:
        mensaje += f"Total a pagar: ${monto:,}\n\n"

    if not (empresa and empresa.alias_pago):
        logging.warning(f"[⚠️ PAGO] empresa '{getattr(empresa, 'nombre', 'desconocida')}' sin alias_pago — usando fallback de Walter")
    alias   = (empresa.alias_pago if empresa and empresa.alias_pago else PAGO_INFO["alias"])
    cvu     = (empresa.cvu_pago   if empresa and empresa.cvu_pago   else PAGO_INFO["cvu"])
    titular = (empresa.nombre     if empresa else PAGO_INFO["titular"])

    mensaje += "Datos para transferir:\n"
    mensaje += f"• Alias: {alias}\n"
    mensaje += f"• Titular: {titular}\n"
    if cvu:
        mensaje += f"• CVU: {cvu}\n"

    return mensaje


async def iniciar_cobranzas(
    to_number: str,
    especialidad: str = None,
    cobertura: str = None,
    obra_social: str = None,
    detalle_turno: str = None,
    empresa_id: str = None,
) -> str:
    """
    Envía instrucciones de pago y, si hay turno confirmado, gestiona el email.
    Retorna el próximo estado_agente: 'principal' o 'esperando_mail'.
    """
    logging.warning(f"[COBRANZAS] {to_number} | esp={especialidad} | cob={cobertura} | turno={detalle_turno}")
    db = SessionLocal()
    try:
        from models import Empresa
        empresa = None
        if empresa_id:
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
        if not empresa:
            from init_db import EMPRESA_DEFAULT_ID
            empresa_id = EMPRESA_DEFAULT_ID
            empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

        cliente = db.query(Cliente).filter(
            Cliente.telefono == to_number,
            Cliente.empresa_id == empresa_id
        ).first()

        if cliente and not cobertura:
            cobertura = cliente.obra_social
        if cliente and not especialidad and cliente.profesional_id:
            from models import Profesional
            prof = db.query(Profesional).filter(Profesional.id == cliente.profesional_id).first()
            if prof:
                especialidad = prof.nombre

        # Guardar detalle de turno para el email
        if cliente and detalle_turno:
            datos = dict(cliente.datos_extraidos or {})
            prof  = _resolver_profesional(db, especialidad, empresa_id)
            datos["ultimo_turno"]       = detalle_turno
            datos["especialidad_turno"] = prof.especialidad if prof else _normalizar_especialidad(especialidad)
            cliente.datos_extraidos = datos
            db.commit()

        # Mensaje con precio + alias
        mensaje_cobro = generar_mensaje_cobro(db, especialidad, cobertura, obra_social, empresa)
        await enviar_mensaje_wpp(to_number, mensaje_cobro)
        logging.warning(f"[COBRANZAS] Mensaje de cobro enviado a {to_number}")

        # Template de cierre con info del turno
        if detalle_turno and cliente:
            nombre = (cliente.nombre_completo or "Paciente").split()[0]
            info_turno = (
                detalle_turno
                .replace("Turno reservado el ", "")
                .split(". Link:")[0]
                .rstrip(".")
            )
            template = (
                f"Gracias {nombre}! quedaste agendado/a para el {info_turno}. "
                f"Cualquier consulta estamos acá 😊"
            )
            await enviar_mensaje_wpp(to_number, template)
            db.add(Mensaje(
                cliente_id = cliente.id,
                empresa_id = empresa_id,
                rol        = "asistente",
                agente     = "cobranzas",
                texto      = template,
            ))
            db.commit()
            logging.warning(f"[COBRANZAS] Template de cierre enviado")

        nombre        = (cliente.nombre_completo if cliente else None) or "un paciente"
        numero_walter = empresa.numero_walter if empresa else None
        await enviar_notificacion_a_walter(to_number, nombre, numero_walter)

        # Email: solo si hay turno confirmado
        if detalle_turno:
            if cliente and cliente.mail:
                await _enviar_email_confirmacion(cliente, detalle_turno, especialidad, db)
                return "principal"
            else:
                await enviar_mensaje_wpp(
                    to_number,
                    "Por último, para enviarte la confirmación del turno por mail, "
                    "me pasas tu dirección de correo electrónico?"
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
        prof  = _resolver_profesional(db, especialidad or datos.get("especialidad_turno"), cliente.empresa_id)
        esp   = prof.especialidad if prof else _normalizar_especialidad(especialidad)

        cobertura          = cliente.obra_social or "particular"
        modalidad, os_nombre = _normalizar_cobertura(cobertura)
        monto              = get_tarifa(prof, modalidad, os_nombre)
        precio_lista       = prof.tarifa_particular if prof else monto
        descuento          = (precio_lista - monto) if modalidad == "obra social" else None

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


async def handler_esperando_mail(user_text: str, to_number: str, msg_id: str = None, empresa_id: str = None) -> None:
    """Maneja el estado 'esperando_mail': valida el email, lo guarda y envía confirmación."""
    logging.warning(f"[ESPERANDO_MAIL] {to_number}: '{user_text}'")
    await marcar_leido_wpp(msg_id)

    texto = user_text.strip()
    db    = SessionLocal()
    try:
        if not empresa_id:
            from init_db import EMPRESA_DEFAULT_ID
            empresa_id = EMPRESA_DEFAULT_ID

        from models import Empresa
        empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()

        cliente = db.query(Cliente).filter(
            Cliente.telefono == to_number,
            Cliente.empresa_id == empresa_id
        ).first()
        if not cliente:
            logging.warning(f"[ESPERANDO_MAIL] Cliente {to_number} no encontrado.")
            return

        datos = dict(cliente.datos_extraidos or {})

        if _EMAIL_RE.match(texto):
            logging.warning(f"[ESPERANDO_MAIL] Email válido: {texto}")
            cliente.mail          = texto
            cliente.estado_agente = "principal"
            datos.pop("intentos_email", None)
            cliente.datos_extraidos = datos
            db.commit()

            await enviar_mensaje_wpp(to_number, "Perfecto! Ya te envío la confirmación del turno a ese correo. Hasta pronto!")

            detalle      = datos.get("ultimo_turno", "Tu próximo turno en Clínica Abriness")
            especialidad = datos.get("especialidad_turno")
            await _enviar_email_confirmacion(cliente, detalle, especialidad, db)

        else:
            intentos = datos.get("intentos_email", 0) + 1
            datos["intentos_email"] = intentos
            cliente.datos_extraidos = datos
            db.commit()

            logging.warning(f"[ESPERANDO_MAIL] Email inválido: '{texto}' | intento {intentos}/3")

            if intentos >= 3:
                cliente.estado_agente = "principal"
                datos.pop("intentos_email", None)
                cliente.datos_extraidos = datos
                db.commit()

                numero_walter = empresa.numero_walter if empresa else None
                await _notificar_walter_email_fallido(to_number, numero_walter)
                await enviar_mensaje_wpp(
                    to_number,
                    "No hay problema, te confirmamos el turno por WhatsApp. Hasta pronto!"
                )
                logging.warning(f"[ESPERANDO_MAIL] 3 intentos fallidos — {to_number} vuelve a principal.")
            else:
                await enviar_mensaje_wpp(
                    to_number,
                    "Hmm, eso no parece un email válido.\n"
                    "Por favor enviame tu correo electrónico (ej: nombre@gmail.com)."
                )
    finally:
        db.close()


async def _notificar_walter_email_fallido(telefono: str, numero_walter: str = None) -> None:
    from agents.herramientas_secretarias import NUMERO_WALTER
    destino = numero_walter or NUMERO_WALTER
    await enviar_mensaje_wpp(
        destino,
        f"El paciente {telefono} no pudo enviar un email válido tras 3 intentos. Revisá manualmente."
    )
