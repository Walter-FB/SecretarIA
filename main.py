from fastapi import FastAPI
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Importamos modelos ANTES de create_all para que SQLAlchemy los registre
from database import engine, Base
import models  # noqa: F401 — necesario para que Base.metadata conozca las tablas

# Crear tablas al iniciar (si no existen)
Base.metadata.create_all(bind=engine)

# Asegurarse de que exista la empresa por defecto
from init_db import seed_empresa_default
seed_empresa_default()

# Importamos las rutas
from routes import whatsapp

# Inicializamos FastAPI
app = FastAPI(title="SecretarIA Backend")

# Enchufamos las rutas de WhatsApp
app.include_router(whatsapp.router)


# ===================================================================
# JOBS PROGRAMADOS — APScheduler
# ===================================================================
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from services.analista_nocturno import job_analista_nocturno
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