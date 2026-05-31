from fastapi import FastAPI
from dotenv import load_dotenv
import sys

# Forzar a Python a que NO guarde los prints en memoria (soluciona el problema de los logs en Railway)
sys.stdout.reconfigure(line_buffering=True)

# Cargar variables de entorno
load_dotenv()

# Importamos modelos ANTES de create_all para que SQLAlchemy los registre
from database import engine, Base
import models  # noqa: F401 — necesario para que Base.metadata conozca las tablas

# Crear tablas al iniciar (si no existen)
Base.metadata.create_all(bind=engine)

# ── Aplicar columnas multi-tenant si Alembic no las agregó ────────────
# Necesario porque create_all() no altera tablas existentes y
# la migración 4faaa4399732 puede haber fallado silenciosamente.
def _aplicar_columnas_faltantes():
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    try:
        cols_emp = {c["name"] for c in inspector.get_columns("empresas")}
        cols_cli = {c["name"] for c in inspector.get_columns("clientes")}
        cols_msg = {c["name"] for c in inspector.get_columns("mensajes")}
    except Exception:
        return  # Si las tablas no existen todavía, create_all() las creará completas

    with engine.begin() as conn:
        if "bot_activo" not in cols_emp:
            print("[DB] Agregando columnas multi-tenant a empresas...")
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS bot_activo           BOOLEAN"))
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS numero_walter        VARCHAR"))
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS tools_habilitadas    JSON"))
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS phone_number_id      VARCHAR"))
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS webhook_verify_token VARCHAR"))
            conn.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS calendar_id          VARCHAR"))
            conn.execute(text("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_empresas_phone_number_id')
                    THEN ALTER TABLE empresas ADD CONSTRAINT uq_empresas_phone_number_id UNIQUE (phone_number_id);
                    END IF;
                END $$;
            """))
            print("[DB] Columnas de empresas OK.")

        if "bot_activo" not in cols_cli:
            print("[DB] Agregando bot_activo a clientes...")
            conn.execute(text("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS bot_activo BOOLEAN"))

        if "empresa_id" not in cols_msg:
            print("[DB] Agregando empresa_id a mensajes...")
            conn.execute(text("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS empresa_id VARCHAR"))
            conn.execute(text("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_mensajes_empresa_id')
                    THEN ALTER TABLE mensajes ADD CONSTRAINT fk_mensajes_empresa_id
                         FOREIGN KEY (empresa_id) REFERENCES empresas(id);
                    END IF;
                END $$;
            """))

_aplicar_columnas_faltantes()

# Asegurarse de que exista la empresa por defecto
from init_db import seed_empresa_default, seed_profesionales, seed_abriness_multitenant
seed_empresa_default()
seed_profesionales()
seed_abriness_multitenant()

# Importamos las rutas
from routes import whatsapp
from routes import admin

# Inicializamos FastAPI
app = FastAPI(title="SecretarIA Backend")

# Enchufamos las rutas
app.include_router(whatsapp.router)
app.include_router(admin.router)


# ===================================================================
# JOBS PROGRAMADOS — APScheduler
# ===================================================================
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from agents.analista_nocturno import job_analista_nocturno
from services.seguimiento import job_seguimiento

scheduler = AsyncIOScheduler()

# Job 1: Analista Nocturno — 00:00 UTC = 21:00 Argentina (UTC-3)
scheduler.add_job(job_analista_nocturno, 'cron', hour=0, minute=0, id='analista_nocturno')

# Job 2: Seguimiento — Cada 1 hora
scheduler.add_job(job_seguimiento, 'interval', hours=1, id='seguimiento_hourly')


@app.on_event("startup")
async def startup_event():
    scheduler.start()
    print("[⏰ SCHEDULER] Jobs programados:")
    print("   → Analista Nocturno: todos los días a las 21:00 ARG")
    print("   → Seguimiento: cada 1 hora")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()


print("\n🚀 [SISTEMA] Backend de SecretarIA v2 iniciado. Tablas verificadas. Rutas conectadas.\n")