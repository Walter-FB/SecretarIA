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

# En local (SQLite) creamos las tablas directo porque SQLite no soporta
# todas las operaciones de Alembic (FKs en ALTER TABLE, etc.).
# En Railway (PostgreSQL) corremos alembic upgrade head al arrancar para garantizar
# que las migraciones siempre se apliquen incluso si el releaseCommand falla.
if engine.dialect.name == "sqlite":
    Base.metadata.create_all(bind=engine)
else:
    # En PostgreSQL aplicamos esquema faltante directamente para no depender
    # del estado de alembic_version (que en Railway puede estar desincronizado).
    from sqlalchemy import text, inspect as sa_inspect
    with engine.begin() as conn:
        inspector = sa_inspect(engine)
        tablas = inspector.get_table_names()

        # ── Tabla turnos (no creada por alembic en producción) ──────
        if "turnos" not in tablas:
            conn.execute(text("""
                CREATE TABLE turnos (
                    id                VARCHAR PRIMARY KEY,
                    profesional_id    VARCHAR NOT NULL REFERENCES profesionales(id),
                    cliente_id        VARCHAR REFERENCES clientes(id),
                    empresa_id        VARCHAR NOT NULL REFERENCES empresas(id),
                    fecha_hora_inicio TIMESTAMP NOT NULL,
                    fecha_hora_fin    TIMESTAMP NOT NULL,
                    estado            VARCHAR DEFAULT 'reservado',
                    CONSTRAINT uq_turno_profesional_hora UNIQUE (profesional_id, fecha_hora_inicio)
                )
            """))
            print("[STARTUP] Tabla 'turnos' creada ✓")
        else:
            print("[STARTUP] Tabla 'turnos' ya existe ✓")

        # ── Columna profesional_id en clientes ──────────────────────
        cols_clientes = [c["name"] for c in inspector.get_columns("clientes")]
        if "profesional_id" not in cols_clientes:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN profesional_id VARCHAR REFERENCES profesionales(id)"))
            print("[STARTUP] Columna 'profesional_id' agregada a clientes ✓")

        # ── Columna bot_activo en clientes ──────────────────────────
        if "bot_activo" not in cols_clientes:
            conn.execute(text("ALTER TABLE clientes ADD COLUMN bot_activo BOOLEAN DEFAULT TRUE"))
            print("[STARTUP] Columna 'bot_activo' agregada a clientes ✓")

        # ── Columna agente en mensajes ───────────────────────────────
        cols_mensajes = [c["name"] for c in inspector.get_columns("mensajes")]
        if "agente" not in cols_mensajes:
            conn.execute(text("ALTER TABLE mensajes ADD COLUMN agente VARCHAR"))
            print("[STARTUP] Columna 'agente' agregada a mensajes ✓")
        else:
            print("[STARTUP] Columna 'agente' ya existe ✓")

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
from agents.seguimiento import job_seguimiento

scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def startup_event():
    scheduler.add_job(job_seguimiento, 'interval', minutes=5, id='seguimiento_fases_2_3')
    scheduler.start()
    print("[⏰ SCHEDULER] Jobs programados:")
    print("   → Seguimiento fases 2+3: cada 5 minutos (fase 1 corre por timer por charla)")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()


print("\n🚀 [SISTEMA] Backend de SecretarIA v2 iniciado. Tablas verificadas. Rutas conectadas.\n")