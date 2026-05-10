import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from database import engine, Base
import models  # Importamos tus modelos para que SQLAlchemy los vea
from database import SessionLocal
from models import Empresa

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


if __name__ == "__main__":
    crear_tablas()
    seed_empresa_default()