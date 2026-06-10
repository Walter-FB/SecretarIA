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
# En Railway (PostgreSQL) el releaseCommand "alembic upgrade head" lo maneja todo.
if engine.dialect.name == "sqlite":
    Base.metadata.create_all(bind=engine)

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