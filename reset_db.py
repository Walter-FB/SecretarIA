from database import engine, Base
import models

print("Borrando tablas viejas...")
Base.metadata.drop_all(bind=engine)

print("Creando tablas nuevas con las columnas correctas...")
Base.metadata.create_all(bind=engine)

print("¡Listo! Base de datos reiniciada con éxito.")
