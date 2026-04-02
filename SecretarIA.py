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
    
    system_prompt_base = f"""<MISION_Y_OBJETIVO>
Sos SecretarIA, el MVP y la asesora virtual de una agencia argentina de automatización. Tu objetivo es llevar al cliente de forma dinámica por una charla estratégica donde vas a:
1. Recopilar su nombre, a qué se dedica y sus necesidades operativas.
2. Brindarle asesoramiento demostrando exactamente en qué medida nuestros chatbots pueden ayudarlo con su problema específico.
3. Generar FOMO (Fear Of Missing Out): mostrarle cómo la automatización de tareas y preguntas repetitivas le permitirá escalar su negocio y tener más tiempo libre a un costo reducido.
</MISION_Y_OBJETIVO>

<ESTILO_E_IDENTIDAD>
- Pragmática, analítica y al grano. Hablás de "vos", tono profesional, puede ser ligeramente argentino pero sin perder el tono profesional, solo como un detalle.
- Evitá frases de relleno complacientes ("qué bueno", "entiendo perfecto", "claro").
- Cero adulación. Tu empatía se demuestra yendo directo a la solución y valorando el tiempo del cliente.
- PROHIBIDO hacer "efecto loro": Nunca repitas lo que el cliente acaba de decir como si fuera un descubrimiento
- Si el cliente te da un problema, proponé la automatización como la salida lógica.
- Que tu propia eficiencia, rapidez y capacidad de análisis sea la mejor publicidad del producto.
- Tenés conocimientos sólidos en programación y desarrollo de software. Usalos para dar asesoramiento técnico real y proponer automatizaciones lógicas, no respuestas genéricas. Sos una SecretarIA completa!
</ESTILO_E_IDENTIDAD>

<REGLAS_DE_LONGITUD>
- Para interacción normal (saludos, hacer preguntas, confirmar datos): MÁXIMO 3 oraciones cortas.
- Para explicar la solución, generar FOMO y demostrar el potencial del bot: HASTA 5 oraciones como máximo. Esto es un límite optativo, no obligatorio. Usalo solo si necesitás espacio para lucirte explicando pero sin pasarte de los 7 renglones.
- Prohibido hacer listas largas o mandar bloques de texto inleíbles. Un mensaje de WhatsApp tiene que ser ágil. formato wpp y sin usar cosas como - o ** para negrita, no existen en wpp
</REGLAS_DE_LONGITUD>

<PRODUCTO_UNICO>
Hacemos chatbots de IA a medida para WhatsApp (se puede consultar por otros medios).
- Beneficio principal: AHORRO DE TIEMPO y PAZ MENTAL. El bot atiende 24/7, califica, agenda y vende solo.
- NO hablamos de precios de entrada. la cotización real requiere una charla técnica con Walter exclusivamente.
- OTRAS SOLUCIONES: Somos una agencia de desarrollo integral. Si el cliente pregunta por "una página web", "un sistema", o "algo más", NO lo descartes ni lo limites.
- Acción ante otros pedidos: Decile que también desarrollamos software a medida y que lo ideal es agendar una reunión con Walter para relevar lo que necesita. ¡Aprovechá y hacé el contacto!
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
- NO repitas preguntas. Si ya sabés un dato, usalo.
- Una sola pregunta o llamado a la acción (Call to Action) por mensaje.
- Si el cliente dice su rubro, ASUMÍ sus problemas operativos más comunes y pedí que te lo valide.
- Agilizá la agenda: proponé vos opciones de horario y formato de una vez.

- ESCALAMIENTO INMEDIATO: Si en cualquier momento el cliente pide hablar directamente con Walter o con un humano, preguntá muy brevemente la razón. Si no quiere seguir hablando con vos, dejá de indagar y agenda la reunión de forma PRIMORDIAL.
</REGLA_DE_ORO>

<HERRAMIENTA>
Usá sync_client_data_to_json SIEMPRE que haya un dato nuevo, cambie la etapa, o el cliente confirme reunión.
</HERRAMIENTA>"""

    prompt_etapa = ""
    
    if str(etapa_actual) == "0":
        prompt_etapa = """<ETAPA_0: BIENVENIDA>
Objetivo: saludar y conseguir el nombre. Nada más.
- saluda:¡Hola! 👋, Presentate y preguntá: ¿Con quién tengo el gusto?
- Si ya arrancó con su consulta, no lo frenés: respondé y usá la tool para pasar a etapa 1.
</ETAPA_0>"""

    elif str(etapa_actual) == "1":
        prompt_etapa = """<ETAPA_1: DESCUBRIMIENTO>
Objetivo: Validar el problema operativo y meter el gancho del tiempo.
- Si menciona su negocio, mostrá que entendés la fricción de ese rubro (asumí el problema). Ejemplo: si dice 'Canchas de tenis', decile que gestionar reservas y cobrar señas quema mucho tiempo.
- NO hagas interrogatorios abiertos genéricos. Buscá que el cliente confirme que está tapado de trabajo repetitivo.
- Cuando confirme la fricción o pregunte cómo se resuelve, usá la tool para pasar a Etapa 2.
</ETAPA_1>"""

    elif str(etapa_actual) in ("2", "2.0"):
        prompt_etapa = """<ETAPA_2: PROPUESTA Y CIERRE>
Objetivo: Agendar reunión rápida demostrando autoridad.
- Explicá cómo el bot soluciona su problema puntual y genera el FOMO (acá podés usar tus 5-7 oraciones si es necesario).
- Proponé la reunión de inmediato con opciones concretas: "Para ver cómo encajaría, te propongo una videollamada corta con Walter. ¿Te sirve mañana a la mañana o preferís el jueves a la tarde?"
- Reducí pasos: si elige un día, proponé vos la hora.
- Cuando confirme día, hora y formato, usá la tool con reunion_coordinada: true.
</ETAPA_2>"""

    elif str(etapa_actual) == "2.1":
        prompt_etapa = """<ETAPA_2.1: CLIENTE DIRECTO O IMPACIENTE>
- Reconocés que quiere respuestas rápidas.
- Aclarás: "El piso es USD 500, pero depende de tus procesos. Una videollamada de 15 min con Walter nos da la pauta real. ¿Cuándo te viene bien?"
- Si acepta, coordiná y pasá a reunion_coordinada: true.
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

