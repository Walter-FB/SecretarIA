from database import engine, Base
import models  # Importamos tus modelos para que SQLAlchemy los vea
from database import SessionLocal
from models import Empresa, Profesional

# ID fijo para la empresa default — se usa en secretaria_principal al crear clientes
EMPRESA_DEFAULT_ID = "secretaria-enterprise"

def crear_tablas():
    print("[INIT] Conectando a Postgres y creando tablas...")
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Tablas creadas con exito!")
    except Exception as e:
        print(f"[ERROR] Error al crear las tablas: {e}")


def seed_empresa_default():
    """Crea la empresa 'SecretarIA Enterprise' si no existe."""
    db = SessionLocal()
    try:
        existe = db.query(Empresa).filter(Empresa.id == EMPRESA_DEFAULT_ID).first()
        if existe:
            print(f"[OK] Empresa '{existe.nombre}' ya existe. No se vuelve a crear.")
            return
        
        empresa = Empresa(
            id=EMPRESA_DEFAULT_ID,
            nombre="SecretarIA Enterprise",
            telefono_bot="",
            prompt_personalidad="SecretarIA - Agencia de automatizacion",
            acepta_pagos=True,
            monto_sena=1,
            alias_pago="Walter.mate3",
            cvu_pago="0000003100000162812352",
            descripcion_pago="Sena para confirmar turno",
            usa_calendar=False,
            usa_seguimiento=True,
            bot_activo=True,
            numero_walter="5491131720843",
            tools_habilitadas=None,   # None = todas habilitadas
            phone_number_id="",       # Llenar con el Meta PHONE_NUMBER_ID real
            calendar_id=None,         # None = usa env var CALENDAR_ID
        )
        db.add(empresa)
        db.commit()
        print("[OK] Empresa 'SecretarIA Enterprise' creada.")
    except Exception as e:
        print(f"[ERROR] Error al crear empresa default: {e}")
        db.rollback()
    finally:
        db.close()


def seed_profesionales():
    """Crea los profesionales de Clínica Abriness si no existen."""
    db = SessionLocal()
    try:
        # OSBA 30% / Galeno 20% sobre tarifa_particular. IOMA, OSDE, Swiss Medical, Médicus → tarifa_obra_social.
        DESCUENTOS = {"OSBA": 30, "Galeno": 20}
        profesionales_data = [
            {
                "nombre":             "Lic. Renals",
                "especialidad":       "psicologo",
                "tarifa_particular":  30000,
                "tarifa_obra_social": 19000,
                "descuentos_obras_sociales": DESCUENTOS,
            },
            {
                "nombre":             "Dr. Barros",
                "especialidad":       "psiquiatra",
                "tarifa_particular":  80000,
                "tarifa_obra_social": 45000,
                "descuentos_obras_sociales": DESCUENTOS,
            },
        ]
        for datos in profesionales_data:
            existe = db.query(Profesional).filter(
                Profesional.nombre == datos["nombre"],
                Profesional.empresa_id == EMPRESA_DEFAULT_ID
            ).first()
            if existe:
                updated = False
                if existe.calendar_id is None:
                    existe.calendar_id = "empresa"
                    updated = True
                if existe.descuentos_obras_sociales is None:
                    existe.descuentos_obras_sociales = datos["descuentos_obras_sociales"]
                    updated = True
                if updated:
                    print(f"[OK] Profesional '{datos['nombre']}': campos actualizados.")
                else:
                    print(f"[OK] Profesional '{datos['nombre']}' ya existe.")
                continue
            prof = Profesional(
                empresa_id                = EMPRESA_DEFAULT_ID,
                nombre                    = datos["nombre"],
                especialidad              = datos["especialidad"],
                tarifa_particular         = datos["tarifa_particular"],
                tarifa_obra_social        = datos["tarifa_obra_social"],
                descuentos_obras_sociales = datos["descuentos_obras_sociales"],
                calendar_id               = "empresa",
                activo                    = True,
            )
            db.add(prof)
            print(f"[OK] Profesional '{datos['nombre']}' creado.")
        db.commit()
    except Exception as e:
        print(f"[ERROR] Error al crear profesionales: {e}")
        db.rollback()
    finally:
        db.close()


def seed_abriness_multitenant():
    """
    Migración de datos: llena los campos multi-tenant de la empresa Abriness
    con los valores que hoy están hardcodeados en el código.
    Idempotente: solo actualiza campos vacíos, no pisa lo que ya tenga valor.
    """
    import os
    db = SessionLocal()
    try:
        empresa = db.query(Empresa).filter(Empresa.id == EMPRESA_DEFAULT_ID).first()
        if not empresa:
            print(f"[WARN] Empresa default no encontrada. Corré seed_empresa_default() primero.")
            return

        # Importar el system prompt completo de Abby (lazy para evitar circular import en startup)
        from agents.secretaria_principal import SYSTEM_PROMPT_PRINCIPAL

        cambios = {}

        if not empresa.phone_number_id:
            pid = os.getenv("PHONE_NUMBER_ID", "")
            if pid:
                cambios["phone_number_id"] = pid

        if not empresa.calendar_id:
            cal = os.getenv("CALENDAR_ID", "")
            if cal:
                cambios["calendar_id"] = cal

        if not empresa.prompt_personalidad or len(empresa.prompt_personalidad) < 200:
            cambios["prompt_personalidad"] = SYSTEM_PROMPT_PRINCIPAL

        if not empresa.tools_habilitadas:
            cambios["tools_habilitadas"] = [
                "registrar_paciente",
                "verificar_paciente_existente",
                "iniciar_agendamiento",
                "iniciar_cobranzas",
                "notificar_walter_urgente",
            ]

        if not empresa.alias_pago:
            cambios["alias_pago"] = "juan9910"

        if not empresa.cvu_pago:
            cambios["cvu_pago"] = "124235243423432432"

        if not empresa.numero_walter:
            cambios["numero_walter"] = "5491131720843"

        if empresa.bot_activo is None:
            cambios["bot_activo"] = True

        if not cambios:
            print("[OK] Empresa Abriness ya tiene todos los datos multi-tenant.")
            return

        for campo, valor in cambios.items():
            setattr(empresa, campo, valor)
        db.commit()
        print(f"[OK] Empresa Abriness actualizada: {list(cambios.keys())}")

    except Exception as e:
        print(f"[ERROR] seed_abriness_multitenant: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    crear_tablas()
    seed_empresa_default()
    seed_profesionales()
    seed_abriness_multitenant()