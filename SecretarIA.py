from fastapi import FastAPI, Request, Response, BackgroundTasks
import httpx
import os
import json
import anthropic
import traceback
from dotenv import load_dotenv

# ===================================================================
# CONFIGURACIÓN E INICIALIZACIÓN DE LA APP Y VARIABLES DE ENTORNO
# ===================================================================
# Cargar variables de entorno desde el archivo .env
load_dotenv()

app = FastAPI()

# Configuración usando los nombres del .env
WPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "secretarIA")
CLAUDE_KEY = os.getenv("CLAUDE_API_KEY")

# --- DEBUG DE INICIO ---
print("\n--- [DEBUG CONFIGURACIÓN] ---")
print(f"WHATSAPP_TOKEN: {'✅ Cargado' if WPP_TOKEN else '❌ NO CARGADO'}")
print(f"PHONE_NUMBER_ID: {PHONE_ID if PHONE_ID else '❌ NO CARGADO'}")
print(f"CLAUDE_API_KEY: {'✅ Cargada' if CLAUDE_KEY else '❌ NO CARGADA'}")
print("-----------------------------\n")

# Inicializamos como None para evitar NameError si la clave falla
client_claude = None

if CLAUDE_KEY:
    client_claude = anthropic.Anthropic(api_key=CLAUDE_KEY)
else:
    print("⚠️ ALERTA: No se encontró CLAUDE_API_KEY en el archivo .env")

DB_FILE = os.path.join("data", "datos_clientes.json")

def cargar_datos():
    """Carga los datos del archivo JSON. Si no existe, devuelve un diccionario vacío."""
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error leyendo {DB_FILE}: {e}")
        return {}

def guardar_datos(datos):
    """Guarda los datos en el archivo JSON."""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error guardando en {DB_FILE}: {e}")

# ===================================================================
# 1. VERIFICACIÓN DEL WEBHOOK (Meta lo pide una vez)
# Endpoint necesario para conectar nuestra app a Facebook/Meta
# ===================================================================
@app.get("/webhook")
async def verify(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Error de verificación", status_code=403)

# ===================================================================
# 2. RECEPCIÓN DE MENSAJES (WEBHOOK POST)
# Atrapa y procesa brevemente los eventos enviados por WhatsApp
# ===================================================================
@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    
    try:
        # Extraemos el mensaje (Estructura estándar de Meta 2026)
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" in entry:
            message = entry["messages"][0]
            phone_number = message["from"]
            
            # Verificamos que sea un mensaje de texto
            if "text" in message:
                text = message["text"]["body"]
                msg_id = message.get("id")

                # Verificamos si el usuario es de confianza o excedió el límite
                datos = cargar_datos()
                cliente = datos.get(phone_number, {})
                es_confianza = cliente.get("es_confianza", False)
                mensajes_enviados = cliente.get("mensajes_enviados", 0)

# ===================================================================
                # LIMITE ANTISPAM / ANTI-HACKER
                # Si alguien adivina la URL, solo podrá enviar este número máximo
                # de mensajes, evitando que agote el saldo de tokens de Claude.
                # ===================================================================
                LIMITE_MENSAJES = 30
                if mensajes_enviados >= LIMITE_MENSAJES:
                    print(f"[{phone_number}] Usuario bloqueado (límite de {LIMITE_MENSAJES} mensajes alcanzado).")
                else:
                    # Usamos BackgroundTasks para responder rápido a Meta y evitar reintentos
                    background_tasks.add_task(procesar_y_responder, text, phone_number, msg_id)
                
    except Exception as e:
        print(f"Error parseando JSON o evento no es un mensaje: {e}")

    # Es obligatorio devolver 200 rápido a Meta
    return Response(content="OK", status_code=200)

# ===================================================================
# 3. LÓGICA PRINCIPAL DE SECRETARIA (IA, JSON Y RESPUESTA)
# Aquí reside toda la inteligencia del chatbot y el flujo de la venta
# ===================================================================
async def procesar_y_responder(user_text: str, to_number: str, msg_id: str = None):
    # ===================================================================
    # CONFIRMACIÓN DE LECTURA Y ESTADO (Check azul y "Escribiendo...")
    # ===================================================================
    # Enviar confirmación de "Visto" (Doble Check Azul) a WhatsApp
    url_base = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    
    if msg_id:
        try:
            payload_read = {"messaging_product": "whatsapp", "status": "read", "message_id": msg_id}
            async with httpx.AsyncClient() as client_http:
                await client_http.post(url_base, json=payload_read, headers=headers)
        except Exception:
            pass
            
    # Enviar estado "Escribiendo..." (Nota: sujeto a disponibilidad oficial según la cuenta, pero enviamos el payload estándar de Meta)
    try:
        payload_typing = {"messaging_product": "whatsapp", "to": to_number, "type": "sender_action", "sender_action": "typing_on"}
        async with httpx.AsyncClient() as client_http:
            await client_http.post(url_base, json=payload_typing, headers=headers)
    except Exception:
        pass

    # ===================================================================
    # CARGA DE CLIENTE Y GESTIÓN DE LA MEMORIA (JSON)
    # Inicializa clientes nuevos o recupera datos de usuarios existentes
    # ===================================================================
    # Cargar los datos de clientes actuales (para ir ágiles con JSON)
    datos = cargar_datos()
    
    # Si el cliente es nuevo, lo introducimos en el JSON
    if to_number not in datos:
        datos[to_number] = {
            "telefono": to_number,
            "es_confianza": False,
            "mensajes_enviados": 0,
            "historial": [],  # Acá guardaremos la conversación
            "notificacion_enviada": False,
            "datos_extraidos": {
                "etapa": 0,
                "nombre_contacto": None,
                "rubro_empresa": None,
                "necesidad_cliente": None,
                "reunion_coordinada": False,
                "horario_reunion": None,
                "tipo_contacto": None
            }
        }
    
    cliente = datos[to_number]
    
    # Marcar como confianza e incrementar el contador de mensajes
    cliente["es_confianza"] = True
    cliente["mensajes_enviados"] = cliente.get("mensajes_enviados", 0) + 1
    
    # Printeamos en consola lo que dice el cliente
    nombre_mostrar = cliente.get("datos_extraidos", {}).get("nombre_contacto") or "None"
    print(f"\n[CLIENTE - {nombre_mostrar} - {to_number}]: {user_text}")

    # ===================================================================
    # GESTIÓN DEL HISTORIAL Y DATOS EXTRAÍDOS
    # Agrega el mensaje al historial, lo recorta y arma formato de IA
    # ===================================================================
    # Agregamos el mensaje del usuario al historial
    cliente["historial"].append({"rol": "usuario", "texto": user_text})
    
    # Para no saturar a Claude con demasiado texto, solo le pasamos los últimos 10 mensajes (ahorro de tokens)
    historial_reciente = cliente["historial"][-10:]
    
    mensajes_claude = []
    for msg in historial_reciente:
        # Claude prefiere 'user' y 'assistant' (roles nativos son mucho más eficientes que texto plano)
        rol = "user" if msg["rol"] == "usuario" else "assistant"
        mensajes_claude.append({"role": rol, "content": msg["texto"]})

    # Para evitar errores con usuarios viejos que no tienen estas nuevas keys usamos .get()
    datos_cliente = cliente.get("datos_extraidos", {})
    etapa_actual = datos_cliente.get("etapa", 0)
    nombre = datos_cliente.get("nombre_contacto", "No especificado")
    rubro = datos_cliente.get("rubro_empresa", "No especificado")
    necesidad = datos_cliente.get("necesidad_cliente", "No especificada")
    reunion = "Sí" if datos_cliente.get("reunion_coordinada") else "No"
    horario = datos_cliente.get("horario_reunion", "No especificado")
    tipo_contacto = datos_cliente.get("tipo_contacto", "No especificado")

    # ===================================================================
    # SISTEMA DE PROMPTS Y MAPEO DE ETAPAS DE VENTA
    # Instrucciones que definen el comportamiento de SecretarIA
    # ===================================================================
    
    system_prompt_base = f"""Sos SecretarIA, la asistente virtual de una empresa argentina que desarrolla chatbots de IA a medida para negocios.

<IDENTIDAD>
- Hablás de "vos", sos argentina, tono porteño natural pero profesional
- Directa, sin vueltas, sin paja — cada mensaje tiene un propósito
- Cálida pero eficiente: no sos fría, pero tampoco charlás al pedo
- Expresiones naturales cuando corresponde ("dale", "mirá", "buenísimo") sin forzarlas
- Sin emojis exagerados, uno o dos por mensaje está bien
- Sin asteriscos, sin guiones para listar, sin markdown — esto es WhatsApp
</IDENTIDAD>

<PRODUCTO_UNICO>
La empresa hace UNA SOLA COSA: chatbots de IA a medida.

Qué puede hacer un chatbot:
- Responder consultas automáticamente (como yo ahora)
- Capturar leads y calificar clientes
- Agendar turnos o reuniones
- Enviar notificaciones o seguimientos automáticos

Integraciones posibles con el chatbot:
- Google Calendar (turnos automáticos)
- Gmail / correo (notificaciones, seguimientos)
- Bases de datos o CRMs (registro de clientes)
- WhatsApp, Instagram, web — donde esté el cliente

IMPORTANTE: Las integraciones no son productos separados, son extensiones del chatbot. Si el cliente pregunta por "una web" o "algo más", aclarás que eso está fuera del alcance y lo redirigís al chatbot como solución.
</PRODUCTO_UNICO>

<MEMORIA_DEL_CLIENTE>
- Nombre: {nombre}
- Rubro: {rubro}
- Necesidad detectada: {necesidad}
- Etapa actual: {etapa_actual}
- Reunión coordinada: {reunion}
- Horario de reunión: {horario}
- Tipo de contacto: {tipo_contacto}
</MEMORIA_DEL_CLIENTE>

<REGLA_DE_ORO>
Nunca repreguntés algo que ya está en memoria.
Si el cliente ya dijo quién es, usá su nombre.
Si ya dijo su rubro, no lo volvás a preguntar.
Una sola pregunta por mensaje, siempre.
</REGLA_DE_ORO>

<HERRAMIENTA>
Usá sync_client_data_to_json SIEMPRE que:
- El cliente dé un dato nuevo (nombre, rubro, necesidad, horario)
- Detectés que cambió de etapa
- El cliente confirme querer contacto o reunión
</HERRAMIENTA>"""

    prompt_etapa = ""
    
    if str(etapa_actual) == "0":
        prompt_etapa = """<ETAPA_0: BIENVENIDA>
Objetivo: saludar y conseguir el nombre. Nada más.

- Presentate como SecretarIA de una empresa de chatbots de IA
- Preguntá con quién hablás
- Máximo 2-3 líneas, un emoji está bien
- Si el cliente ya se presentó o ya arrancó con una consulta, no lo frenés — respondé y usá la tool para pasar a etapa 1
</ETAPA_0>"""

    elif str(etapa_actual) == "1":
        prompt_etapa = """<ETAPA_1: DESCUBRIMIENTO>
Objetivo: entender a qué se dedica el cliente o qué problema tiene.

- Una sola pregunta por mensaje
- No vendas nada todavía
- Si pregunta qué hacemos: "Hacemos chatbots de IA a medida — pero para saber si te sirve, contame un poco de tu negocio. ¿A qué te dedicás?"
- Si el cliente ya está contando su situación, escuchá y mostrá que entendés
- Cuando tengas suficiente contexto para conectar con un chatbot, usá la tool para pasar a etapa 2
- Si está impaciente o pide precio sin dar contexto: etapa 2.1
</ETAPA_1>"""

    elif str(etapa_actual) in ("2", "2.0"):
        prompt_etapa = """<ETAPA_2: PROPUESTA Y CIERRE>
Objetivo: mostrar cómo un chatbot resuelve SU problema específico y coordinar contacto con Walter.

CÓMO PROPONER:
- Conectá lo que mencionó el cliente con lo que puede hacer un chatbot
- Sé concreta: "para lo tuyo, el chatbot podría hacer X y Y" — no hables en genérico
- Si mencionan integraciones (Calendar, Gmail, etc.): "sí, eso se puede integrar, es parte del chatbot"
- Si piden algo fuera del scope (web, diseño gráfico, etc.): "eso no lo hacemos, nos especializamos en chatbots — ¿tiene sentido para tu negocio?"

PARA CERRAR (cuando hay interés):
- Proponé una llamada o videollamada corta con Walter (15-20 min) para ver si encaja
- Horarios disponibles de Walter: lunes a miércoles antes de las 12hs o después de las 18hs, jueves y viernes cualquier horario razonable
- Confirmá horario y formato antes de cerrar: "entonces el jueves a las 15hs por videollamada, ¿bien?"
- Cuando el cliente confirme interés en contactar (aunque sea sin horario definido), usá la tool con reunion_coordinada: true y avisá a Walter
</ETAPA_2>"""

    elif str(etapa_actual) == "2.1":
        prompt_etapa = """<ETAPA_2.1: CLIENTE DIRECTO O IMPACIENTE>
El cliente quiere ir directo al precio o está siendo impaciente. Manejalo con criterio.

- No te achicás, no le das un número inventado
- Reconocés que quiere respuestas ya, pero explicás por qué necesitás contexto
- Si insiste: "Rango general: desde USD 500 aproximadamente, pero depende mucho de qué necesitás. Dos minutos de contexto me permiten darte algo real."
- Si el cliente dice que quiere hablar directamente con alguien: perfecto, usá la tool con reunion_coordinada: true y coordiná el contacto
</ETAPA_2.1>"""

    system_instructions = f"{system_prompt_base}\n{prompt_etapa}"

    # ===================================================================
    # DEFINICIÓN DE HERRAMIENTA DE ACTUALIZACIÓN (FUNCTION CALLING)
    # Permite a Claude actualizar el JSON si detecta nueva información
    # ===================================================================
    def sync_client_data_to_json(
        etapa_sugerida: float,
        nombre_contacto: str = None,
        rubro_empresa: str = None,
        necesidad_cliente: str = None,
        reunion_coordinada: bool = False,
        horario_reunion: str = None,
        tipo_contacto: str = None
    ):
        """
        Sincroniza la información extraída del cliente con la base de datos local (JSON). 
        Llama a esta función cada vez que detectes un dato nuevo o un cambio de etapa en la conversación.
        """
        if "datos_extraidos" not in cliente:
            cliente["datos_extraidos"] = {}
            
        # Actualizamos solo lo que Gemini nos mande
        if etapa_sugerida is not None:
            cliente["datos_extraidos"]["etapa"] = etapa_sugerida
        if nombre_contacto:
            cliente["datos_extraidos"]["nombre_contacto"] = nombre_contacto
        if rubro_empresa:
            cliente["datos_extraidos"]["rubro_empresa"] = rubro_empresa
        if necesidad_cliente:
            cliente["datos_extraidos"]["necesidad_cliente"] = necesidad_cliente
        if reunion_coordinada:
            cliente["datos_extraidos"]["reunion_coordinada"] = True
        if horario_reunion:
            cliente["datos_extraidos"]["horario_reunion"] = horario_reunion
        if tipo_contacto:
            cliente["datos_extraidos"]["tipo_contacto"] = tipo_contacto
            
        print(f"[FUNCTION CALL] Datos actualizados para {to_number}: {cliente['datos_extraidos']}")
        guardar_datos(datos)
        return {"status": "success", "message": "Datos actualizados correctamente"}

    # ===================================================================
    # ESQUEMA DE HERRAMIENTA (TOOL) PARA LA API DE CLAUDE
    # Formato Anthropic (diferente a Gemini u OpenAI)
    # ===================================================================
    claude_tool = {
        "name": "sync_client_data_to_json",
        "description": (
            "Sincroniza la información extraída del cliente con la base de datos local (JSON). "
            "Llamá a esta función cada vez que detectes un dato nuevo o un cambio de etapa en la conversación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "etapa_sugerida": {
                    "type": "number",
                    "description": "Nueva etapa del cliente (0, 1, 2, 2.1)"
                },
                "nombre_contacto": {
                    "type": "string",
                    "description": "Nombre del cliente si lo mencionó"
                },
                "rubro_empresa": {
                    "type": "string",
                    "description": "Rubro o industria del cliente"
                },
                "necesidad_cliente": {
                    "type": "string",
                    "description": "Necesidad o problema principal detectado"
                },
                "reunion_coordinada": {
                    "type": "boolean",
                    "description": "True si el cliente confirmó querer una reunión"
                },
                "horario_reunion": {
                    "type": "string",
                    "description": "Horario acordado para la reunión"
                },
                "tipo_contacto": {
                    "type": "string",
                    "description": "Tipo de contacto preferido (videollamada, llamada, etc.)"
                }
            },
            "required": ["etapa_sugerida"]
        }
    }

    # ===================================================================
    # LLAMADA A LA API DE CLAUDE Y PROCESAMIENTO DE HERRAMIENTAS
    # Envia el prompt, parsea tool_use si lo hay, y pide texto final
    # ===================================================================
    try:
        response = client_claude.messages.create(
            model="claude-haiku-4-5",  # Haiku es el modelo más rápido y económico
            max_tokens=1024,
            system=system_instructions,
            tools=[claude_tool],
            messages=mensajes_claude
        )

        ai_response = ""

        # Procesamos el contenido de la respuesta bloque por bloque
        for block in response.content:
            if block.type == "tool_use" and block.name == "sync_client_data_to_json":
                # Claude quiere llamar a nuestra función — la ejecutamos nosotros
                args = block.input
                sync_client_data_to_json(
                    etapa_sugerida=args.get("etapa_sugerida"),
                    nombre_contacto=args.get("nombre_contacto"),
                    rubro_empresa=args.get("rubro_empresa"),
                    necesidad_cliente=args.get("necesidad_cliente"),
                    reunion_coordinada=args.get("reunion_coordinada", False),
                    horario_reunion=args.get("horario_reunion"),
                    tipo_contacto=args.get("tipo_contacto")
                )
            elif block.type == "text":
                ai_response += block.text

        ai_response = ai_response.strip()

        # Si Claude sólo llamó al tool sin texto, le pedimos el texto final
        if not ai_response and response.stop_reason == "tool_use":
            follow_up = client_claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1024,
                system=system_instructions,
                messages=mensajes_claude + [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": next(
                                    b.id for b in response.content if b.type == "tool_use"
                                ),
                                "content": "Datos actualizados correctamente"
                            }
                        ]
                    }
                ],
                tools=[claude_tool]
            )
            for block in follow_up.content:
                if block.type == "text":
                    ai_response += block.text
            ai_response = ai_response.strip()

    except Exception as e:
        error_msg = str(e)
        if "529" in error_msg or "overloaded" in error_msg.lower():
            print(f"\n[⚠️ ALERTA API] Claude está sobrecargado por alta demanda.\nDetalles: {error_msg[:200]}\n")
            ai_response = "Disculpá, estoy recibiendo muchos mensajes juntos y mi sistema me pide que espere un momentito. 🙏 ¡Bancame un cachito y volveme a escribir!"
        elif "rate_limit" in error_msg.lower() or "429" in error_msg:
            print(f"\n[⚠️ ALERTA LÍMITE API] Rate limit de Claude alcanzado.\nDetalles: {error_msg[:200]}\n")
            ai_response = "Disculpá, estoy recibiendo muchos mensajes juntos y mi sistema me pide que espere 1 minuto para seguir respondiendo. 🙏 ¡Bancame un cachito y volveme a escribir!"
        else:
            print(f"\n[❌ ERROR CLAUDE]: {error_msg}\n")
            ai_response = "Ay, disculpame, se me tildó la compu un segundito. ¿Me repetís lo que me decías porfa?"

    # ===================================================================
    # ACTUALIZACIÓN POST-RESPUESTA Y RUTINAS DE CORRECCIÓN DE ESTADO
    # ===================================================================
    # Printeamos en consola lo que responde la IA
    print(f"[SECRETARIA]: {ai_response}\n")

    # Agregamos la respuesta de IA al historial
    cliente["historial"].append({"rol": "asistente", "texto": ai_response})
    
    # Salto forzado a Etapa 1 si estaba en Etapa 0 (para no estancarse en el saludo)
    if str(cliente.get("datos_extraidos", {}).get("etapa", 0)) == "0":
        cliente["datos_extraidos"]["etapa"] = 1
        print(f"[{to_number}] Transición forzada de Etapa 0 a 1.")

    # Guardamos el JSON de nuevo por las dudas
    guardar_datos(datos)

    # ===================================================================
    # ENVÍO DE NOTIFICACIÓN A WALTER SI HAY REUNIÓN COORDINADA
    # ===================================================================
    reunion_coordinada = cliente.get("datos_extraidos", {}).get("reunion_coordinada", False)
    notificacion_enviada = cliente.get("notificacion_enviada", False)
    
    if reunion_coordinada and not notificacion_enviada:
        numero_cliente = cliente.get("telefono", "")
        nombre_cliente = cliente.get("datos_extraidos", {}).get("nombre_contacto", "un cliente")
        
        mensaje_walter = f"Cliente interezado!\nHola Walter! 🥰 Te informo que el numero {{{numero_cliente}}} a nombre de {{{nombre_cliente}}} estaría interesado en contactarte. Háblale, suerte y saludos! 👋"
        
        payload_walter = {
            "messaging_product": "whatsapp",
            "to": "5491131720843", # o el numero con country code si es distinto (1131720843)
            "type": "text",
            "text": {"body": mensaje_walter}
        }
        
        try:
            url_walter = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
            headers_walter = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client_http: # usar un nuevo cliente asincrono
                await client_http.post(url_walter, json=payload_walter, headers=headers_walter)
                print("[✅ NOTIFICACIÓN] Se avisó a Walter sobre el nuevo cliente interesado.")
            
            # Marcamos como enviada y guardamos
            cliente["notificacion_enviada"] = True
            guardar_datos(datos)
        except Exception as e:
            print(f"[❌ ERROR NOTIFICANDO A WALTER]: {e}")

    # ===================================================================
    # ENVÍO DE LA RESPUESTA FINAL MEDIANTE WHATSAPP CLOUD API
    # ===================================================================
    # Envío a WhatsApp
    url = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": ai_response}
    }

    try:
        if not WPP_TOKEN or not PHONE_ID:
             print(f"[❌ ERROR CONFIG]: Falta el Token o el Phone ID en el .env. No se puede enviar mensaje.")
             return

        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers)
            
            # Si hay error (status 400, 401, etc.), mostramos el JSON detallado de Meta
            if r.status_code != 200:
                print(f"\n[❌ ERROR META {r.status_code}]: {r.text}")
            else:
                print(f"[✅ WPP ENVIADO]: Mensaje entregado a {to_number}")

    except Exception as e:
        print(f"\n[❌ ERROR CRÍTICO HTTPX]:")
        traceback.print_exc()

