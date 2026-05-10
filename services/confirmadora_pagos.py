# ===================================================================
# CONFIRMADORA DE PAGOS
# Sin IA. 100% determinístico.
# Cuando el cliente dice "ya pagué", consulta las últimas 3
# transacciones de Mercado Pago y compara con los datos del cliente.
# ===================================================================
import httpx
from database import SessionLocal
from models import Cliente, Pago
from services.secretaria_principal import enviar_mensaje_wpp, NUMERO_WALTER
from datetime import datetime
import os


async def verificar_pago_cliente(telefono_cliente: str):
    """
    El cliente dijo "ya pagué". 
    Consultamos las últimas 3 transacciones de MP y buscamos un match.
    """
    
    MP_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    if not MP_TOKEN:
        print("[❌ CONFIRMADORA] No hay MERCADOPAGO_ACCESS_TOKEN en .env")
        await enviar_mensaje_wpp(telefono_cliente, 
            "Ups, tengo un problema técnico para verificar el pago. Walter va a revisarlo manualmente 😊")
        return
    
    db = SessionLocal()
    try:
        # 1. Buscar al cliente y su pago pendiente
        cliente = db.query(Cliente).filter(Cliente.telefono == telefono_cliente).first()
        if not cliente:
            print(f"[⚠️ CONFIRMADORA] Cliente {telefono_cliente} no encontrado")
            return
        
        pago_pendiente = db.query(Pago).filter(
            Pago.cliente_id == cliente.id,
            Pago.estado == "pendiente"
        ).order_by(Pago.fecha_creacion.desc()).first()
        
        if not pago_pendiente:
            await enviar_mensaje_wpp(telefono_cliente, 
                "No encontré un pago pendiente para tu cuenta. Si necesitás ayuda, escribime 😊")
            return
        
        # 2. Datos del cliente para matchear
        datos = cliente.datos_extraidos or {}
        nombre_cliente = datos.get("nombre_contacto", "").strip().lower()
        monto_esperado = pago_pendiente.monto
        
        print(f"[💳 CONFIRMADORA] Buscando pago de '{nombre_cliente}' por ${monto_esperado}")
        
        # 3. Consultar las últimas 3 transacciones aprobadas de MP
        url = "https://api.mercadopago.com/v1/payments/search"
        headers = {"Authorization": f"Bearer {MP_TOKEN}"}
        params = {
            "sort": "date_created",
            "criteria": "desc",
            "limit": 3,
            "status": "approved"
        }
        
        async with httpx.AsyncClient() as http:
            r = await http.get(url, headers=headers, params=params)
            
            if r.status_code != 200:
                print(f"[❌ CONFIRMADORA] Error MP API: {r.status_code} — {r.text}")
                await enviar_mensaje_wpp(telefono_cliente,
                    "No pude verificar el pago en este momento. Intentá en unos minutos 😊")
                return
            
            data = r.json()
        
        resultados = data.get("results", [])
        print(f"[💳 CONFIRMADORA] {len(resultados)} transacciones recientes encontradas")
        
        # 4. Buscar match en las transacciones
        pago_encontrado = None
        for tx in resultados:
            tx_monto = tx.get("transaction_amount", 0)
            
            # Datos del pagador
            payer = tx.get("payer", {})
            tx_nombre = f"{payer.get('first_name', '')} {payer.get('last_name', '')}".strip().lower()
            tx_id = str(tx.get("id", ""))
            tx_status = tx.get("status", "")
            
            print(f"  → TX #{tx_id}: {tx_nombre} — ${tx_monto} — {tx_status}")
            
            # Match por monto Y nombre (fuzzy: contiene)
            monto_ok = (tx_monto == monto_esperado)
            nombre_ok = False
            
            if nombre_cliente and tx_nombre:
                # Matcheo flexible: alguna de las partes del nombre coincide
                partes_cliente = nombre_cliente.split()
                partes_tx = tx_nombre.split()
                nombre_ok = any(
                    parte_c in tx_nombre or parte_t in nombre_cliente
                    for parte_c in partes_cliente
                    for parte_t in partes_tx
                )
            
            if monto_ok and nombre_ok:
                pago_encontrado = tx
                break
            
            # Si el monto es correcto pero no tenemos nombre, aceptamos igual 
            # (para demo con $1 y sin nombre)
            if monto_ok and not nombre_cliente:
                pago_encontrado = tx
                break
        
        # 5. Resultado
        if pago_encontrado:
            await _confirmar_pago_exitoso(db, cliente, pago_pendiente, pago_encontrado)
        else:
            await enviar_mensaje_wpp(telefono_cliente,
                "Todavía no veo tu transferencia reflejada 🔍\n"
                "A veces tarda unos minutos. Intentá de nuevo en un ratito escribiendo 'ya pagué'.")
            print(f"[💳 CONFIRMADORA] No se encontró match para {telefono_cliente}")
    
    except Exception as e:
        print(f"[❌ ERROR CONFIRMADORA]: {e}")
        import traceback
        traceback.print_exc()
        await enviar_mensaje_wpp(telefono_cliente,
            "Tuve un error verificando el pago. Walter lo va a revisar manualmente 😊")
    finally:
        db.close()


async def _confirmar_pago_exitoso(db, cliente, pago: Pago, tx_mp: dict):
    """Confirma el pago, avisa al cliente y a Walter."""
    
    payer = tx_mp.get("payer", {})
    nombre_pagador = f"{payer.get('first_name', '')} {payer.get('last_name', '')}".strip()
    
    # 1. Actualizar registro de pago
    pago.estado = "aprobado"
    pago.mp_payment_id = str(tx_mp.get("id", ""))
    pago.nombre_pagador = nombre_pagador
    pago.fecha_confirmacion = datetime.utcnow()
    
    # 2. Volver a principal
    cliente.estado_agente = "principal"
    db.commit()
    
    print(f"[✅ PAGO CONFIRMADO] {cliente.telefono} — ${pago.monto} — TX #{pago.mp_payment_id}")
    
    # 3. Avisar al cliente
    await enviar_mensaje_wpp(cliente.telefono,
        f"Recibimos tu pago de ${pago.monto} ✅\n"
        f"Tu turno queda confirmado: {pago.detalle_turno}\n"
        f"¡Nos vemos! 😊")
    
    # 4. Notificar a Walter
    nombre_cliente = (cliente.datos_extraidos or {}).get("nombre_contacto", "un cliente")
    await enviar_mensaje_wpp(NUMERO_WALTER,
        f"✅ Pago confirmado!\n"
        f"Cliente: {nombre_cliente} ({cliente.telefono})\n"
        f"Monto: ${pago.monto}\n"
        f"Turno: {pago.detalle_turno}\n"
        f"Pagador MP: {nombre_pagador}")
