from services.secretaria_principal import enviar_mensaje_wpp, enviar_notificacion_a_walter
from database import SessionLocal
from models import Cliente

async def iniciar_cobranzas(to_number: str):
    # 1. Enviar mensaje de MVP al paciente
    mensaje = "Gracias por probar SecretarIA, hasta aca llega el 1er mvp."
    await enviar_mensaje_wpp(to_number, mensaje)
    print(f"[COBRANZAS] Mensaje de MVP enviado a {to_number}")
    
    # 2. Buscar al paciente en la DB para sacar el nombre
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        nombre = "un paciente"
        if cliente:
            if cliente.nombre_completo:
                nombre = cliente.nombre_completo
            elif cliente.datos_extraidos and "nombre_contacto" in cliente.datos_extraidos:
                nombre = cliente.datos_extraidos["nombre_contacto"]
                
        # 3. Avisarle a Walter
        await enviar_notificacion_a_walter(to_number, nombre)
        print(f"[COBRANZAS] Notificación enviada a Walter sobre {to_number}")
    finally:
        db.close()
