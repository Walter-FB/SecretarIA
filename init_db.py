import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

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
            telefono_bot="",  # Se llena cuando se configure multi-tenant
            prompt_personalidad="SecretarIA - Agencia de automatizacion",
            acepta_pagos=True,
            monto_sena=1,                          # $1 para demo
            alias_pago="Walter.mate3",
            cvu_pago="0000003100000162812352",
            descripcion_pago="Sena para confirmar turno",
            usa_calendar=False,                     # Aun no implementado
            usa_seguimiento=True,
        )
        db.add(empresa)
        db.commit()
        print("[OK] Empresa 'SecretarIA Enterprise' creada con acepta_pagos=True")
    except Exception as e:
        print(f"[ERROR] Error al crear empresa default: {e}")
        db.rollback()
    finally:
        db.close()


def seed_profesionales():
    """Crea los profesionales de Clínica Abriness si no existen."""
    db = SessionLocal()
    try:
        profesionales_data = [
            {
                "nombre":             "Lic. Renals",
                "especialidad":       "psicologo",
                "tarifa_particular":  30000,
                "tarifa_obra_social": 19000,
            },
            {
                "nombre":             "Dr. Barros",
                "especialidad":       "psiquiatra",
                "tarifa_particular":  80000,
                "tarifa_obra_social": 45000,
            },
        ]
        for datos in profesionales_data:
            existe = db.query(Profesional).filter(
                Profesional.nombre == datos["nombre"],
                Profesional.empresa_id == EMPRESA_DEFAULT_ID
            ).first()
            if existe:
                print(f"[OK] Profesional '{datos['nombre']}' ya existe.")
                continue
            prof = Profesional(
                empresa_id         = EMPRESA_DEFAULT_ID,
                nombre             = datos["nombre"],
                especialidad       = datos["especialidad"],
                tarifa_particular  = datos["tarifa_particular"],
                tarifa_obra_social = datos["tarifa_obra_social"],
                calendar_id        = None,
                activo             = True,
            )
            db.add(prof)
            print(f"[OK] Profesional '{datos['nombre']}' creado.")
        db.commit()
    except Exception as e:
        print(f"[ERROR] Error al crear profesionales: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    crear_tablas()
    seed_empresa_default()
    seed_profesionales()