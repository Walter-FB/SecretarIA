# DEPRECADO — usar Alembic para migraciones en adelante.
# Este script solo es válido para desarrollo local (SQLite).
# NUNCA correr contra producción (Railway): borra todos los datos.
#
# Para desarrollo local limpio:  python reset_db.py
# Para migraciones de esquema:   alembic revision --autogenerate -m "descripcion"
#                                 alembic upgrade head
from database import engine, Base
import models

print("Borrando tablas viejas...")
Base.metadata.drop_all(bind=engine)

print("Creando tablas nuevas con las columnas correctas...")
Base.metadata.create_all(bind=engine)

print("¡Listo! Base de datos reiniciada con éxito.")
