import mercadopago

# Inicializás el SDK con tu Access Token (el de Producción si vas a lo suicida, o el de Prueba)
ACCESS_TOKEN_MP = "TEST-6692961248682372-042909-d9cb342a4fd82cf71770aa8970370c9e-472597631"
sdk = mercadopago.SDK(ACCESS_TOKEN_MP)

def generar_link_pago(titulo_servicio: str, monto: float, cliente_id: str) -> str:
    """
    Crea la preferencia en MP y devuelve el link de pago listo para WhatsApp.
    """
    preference_data = {
        "items": [
            {
                "title": titulo_servicio,
                "quantity": 1,
                "unit_price": float(monto),
                "currency_id": "ARS"
            }
        ],
        # Metemos el ID de tu cliente acá para que cuando MP nos avise que pagó, sepamos quién fue
        "external_reference": cliente_id,
        
        # ACÁ ES DONDE MP TE VA A AVISAR (El webhook que te da paja testear)
        "notification_url": "https://tu-proyecto.railway.app/webhook-pagos",
    }

    try:
        # Hacemos el POST a Mercado Pago
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]
        
        # init_point es el link oficial de pago de Mercado Pago
        link_de_pago = preference["init_point"] 
        return link_de_pago
        
    except Exception as e:
        print(f"[❌ ERROR MERCADO PAGO]: {e}")
        return None