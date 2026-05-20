from fastapi import FastAPI, Request, Response
import uvicorn

app = FastAPI()

@app.post("/webhook")
async def handle_webhook(request: Request):
    # 1. Recibimos el impacto
    payload = await request.json()
    
    print("\n--- NUEVA NOTIFICACIÓN RECIBIDA ---")
    print(f"Tipo: {payload.get('type')}")
    print(f"ID del Recurso: {payload.get('data', {}).get('id')}")
    print(f"Payload completo: {payload}")
    
    # 2. Respondemos 200 al toque. 
    # Si no respondes esto rápido, MP te va a stalkear con reintentos.
    return Response(status_code=200)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)