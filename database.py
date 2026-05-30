import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# URL de tu PostgreSQL. Si no hay, usa SQLite por defecto para que no explote en local
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./secretaria_local.db")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)
elif SQLALCHEMY_DATABASE_URL.startswith("postgresql://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)

engine = create_engine(SQLALCHEMY_DATABASE_URL)

try:
    with engine.connect() as connection:
        if "sqlite" in SQLALCHEMY_DATABASE_URL:
            print("[DB] Conectado a SQLite local")
        else:
            print("[DB] Conectado a PostgreSQL (Railway)")
except Exception as e:
    print(f"[DB ERROR] No se pudo conectar: {e}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia para inyectar la sesión en las rutas
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()