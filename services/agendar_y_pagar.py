# ===================================================================
# SECRETARIA AGENDAR Y PAGAR
# Misma lógica que agendadora.py pero al confirmar fecha,
# envía datos de pago (Alias/CVU) y cambia a "esperando_pago".
# También contiene el handler para cuando el cliente está esperando.
# ===================================================================
from database import SessionLocal
from models import Cliente, Mensaje, Pago, Empresa
from services.secretaria_principal import enviar_mensaje_wpp, marcar_leido_wpp, client_claude
from datetime import datetime, timedelta
import os

# ===================================================================
# PROMPT DE LA AGENDADORA CON PAGO (corto, solo agenda)
# ===================================================================
SYSTEM_PROMPT_AGENDAR_PAGAR = """Sos el módulo de agenda de SecretarIA. Tu ÚNICO objetivo es coordinar una reunión con Walter.

REGLAS:
1. Consultá la disponibilidad con la tool consultar_calendar
2. Proponé al cliente UNA fecha y hora concreta
3. Si acepta, confirmá el turno con la tool confirmar_turno
4. Despedite brevemente diciendo que le vas a enviar los datos de pago

ESTILO: Directo, breve, formato WhatsApp. Máximo 2 oraciones por mensaje.
NO vendas, NO asesores, NO hagas preguntas fuera del tema de agenda."""

# ===================================================================
# TOOLS DE LA AGENDADORA CON PAGO
# ===================================================================
TOOLS_AGENDAR_PAGAR = [
    {
        "name": "consultar_calendar",
        "description": "Consulta la disponibilidad de Walter en Google Calendar para los próximos días.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_desde": {"type": "string", "description": "Fecha desde (YYYY-MM-DD)"},
                "dias_a_consultar": {"type": "integer", "description": "Cantidad de días (default: 3)"}
            },
            "required": ["fecha_desde"]
        }
    },
    {
        "name": "confirmar_turno",
        "description": "Confirma el turno cuando el cliente acepta la fecha y hora propuesta. Después de esto se le pedirá el pago.",
        "input_schema": {
            "type": "object",
            "properties": {
                "titulo": {"type": "string", "description": "Título del evento (ej: 'Reunión con Juan')"},
                "fecha_hora": {"type": "string", "description": "Fecha y hora confirmada (YYYY-MM-DDTHH:MM)"},
                "duracion_minutos": {"type": "integer", "description": "Duración en minutos (default: 30)"}
            },
            "required": ["titulo", "fecha_hora"]
        }
    }
]


# ===================================================================
# STUBS DE GOOGLE CALENDAR — TODO: Implementar con API real
# ===================================================================
async def _stub_consultar_calendar(fecha_desde: str, dias: int = 3) -> str:
    print(f"[📅 CALENDAR STUB] Consultando desde {fecha_desde} ({dias} días)")
    return f"Walter tiene disponibilidad mañana a las 10:00, 14:00 y 16:00. Pasado mañana a las 11:00 y 15:00."

async def _stub_crear_evento(titulo: str, fecha_hora: str, duracion: int = 30) -> str:
    print(f"[📅 CALENDAR STUB] Evento creado: '{titulo}' el {fecha_hora} ({duracion} min)")
    return f"Evento creado: {titulo} — {fecha_hora}"


# ===================================================================
# SECRETARIA AGENDAR Y PAGAR — Función principal
# ===================================================================
async def secretaria_agendar_y_pagar(user_text: str, to_number: str, msg_id: str = None):
    """Agendadora + cobro. Cuando confirma fecha, envía datos de pago y pasa a 'esperando_pago'."""
    
    await marcar_leido_wpp(msg_id)
    
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
        if not cliente:
            print(f"[⚠️ AGENDAR_PAGAR] Cliente {to_number} no encontrado.")
            return
        
        cliente.mensajes_enviados += 1
        print(f"\n[AGENDAR_PAGAR - {to_number}]: {user_text}")
        
        # Historial corto (3 mensajes)
        mensajes_recientes = db.query(Mensaje).filter(
            Mensaje.cliente_id == cliente.id
        ).order_by(Mensaje.fecha_creacion.desc()).limit(20).all()
        
        historial_claude = []
        for m in reversed(mensajes_recientes):
            historial_claude.append({
                "role": "user" if m.rol == "usuario" else "assistant",
                "content": m.texto
            })
        historial_claude.append({"role": "user", "content": user_text})
        
        nuevo_msg = Mensaje(cliente_id=cliente.id, rol="usuario", texto=user_text)
        db.add(nuevo_msg)
        
        # Inyectar nombre del cliente
        datos = cliente.datos_extraidos or {}
        nombre = datos.get("nombre_contacto", "")
        contexto_extra = f"\nEl cliente se llama {nombre}." if nombre else ""
        system_final = SYSTEM_PROMPT_AGENDAR_PAGAR + contexto_extra
        
        # Llamar a Claude
        response = client_claude.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system=system_final,
            tools=TOOLS_AGENDAR_PAGAR,
            messages=historial_claude
        )
        
        texto_respuesta = ""
        tools_a_ejecutar = []
        for block in response.content:
            if block.type == "text":
                texto_respuesta += block.text.strip()
            elif block.type == "tool_use":
                tools_a_ejecutar.append({"name": block.name, "input": block.input})
        
        if texto_respuesta:
            print(f"[AGENDAR_PAGAR]: {texto_respuesta}\n")
            await enviar_mensaje_wpp(to_number, texto_respuesta)
            resp_msg = Mensaje(cliente_id=cliente.id, rol="asistente", texto=texto_respuesta)
            db.add(resp_msg)
        
        # Procesar tools
        for tool in tools_a_ejecutar:
            if tool["name"] == "consultar_calendar":
                resultado = await _stub_consultar_calendar(
                    tool["input"].get("fecha_desde", datetime.utcnow().strftime("%Y-%m-%d")),
                    tool["input"].get("dias_a_consultar", 3)
                )
                await enviar_mensaje_wpp(to_number, resultado)
            
            elif tool["name"] == "confirmar_turno":
                titulo = tool["input"].get("titulo", "Reunión con cliente")
                fecha_hora = tool["input"].get("fecha_hora", "")
                duracion = tool["input"].get("duracion_minutos", 30)
                
                # 1. Crear evento en Calendar (stub)
                await _stub_crear_evento(titulo, fecha_hora, duracion)
                
                # 2. Obtener datos de pago de la empresa
                empresa = None
                if cliente.empresa_id:
                    empresa = db.query(Empresa).filter(Empresa.id == cliente.empresa_id).first()
                
                monto = empresa.monto_sena if empresa and empresa.monto_sena else 1
                alias = empresa.alias_pago if empresa and empresa.alias_pago else "Walter.mate3"
                cvu = empresa.cvu_pago if empresa and empresa.cvu_pago else "0000003100000162812352"
                
                # 3. Crear registro de pago pendiente
                detalle = f"{titulo} — {fecha_hora}"
                pago = Pago(
                    cliente_id=cliente.id,
                    monto=monto,
                    detalle_turno=detalle
                )
                db.add(pago)
                
                # 4. Enviar datos de pago al cliente (mensaje fijo, no IA)
                mensaje_pago = (
                    f"Para continuar con la reserva solicito que abone una seña "
                    f"de ${monto} para confirmar la reservación.\n\n"
                    f"Podés transferir mediante estos medios:\n"
                    f"Alias: {alias}\n"
                    f"CVU: {cvu}\n\n"
                    f"Cuando hayas transferido, escribime 'ya pagué' y lo verifico al toque 😊"
                )
                await enviar_mensaje_wpp(to_number, mensaje_pago)
                
                # 5. Cambiar estado a esperando_pago
                cliente.estado_agente = "esperando_pago"
                print(f"[🔄 SWITCH] Cliente {to_number} → estado_agente = 'esperando_pago'")
        
        db.commit()
        
    except Exception as e:
        print(f"\n[❌ ERROR AGENDAR_PAGAR]: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# ===================================================================
# HANDLER ESPERANDO PAGO — Bot simple sin IA
# El cliente escribe, Python responde:
#   - "ya pagué" → chequea MP → confirma o rechaza
#   - "cancelar" → cancela y vuelve a principal
#   - otra cosa → "Aguardo el abono"
# ===================================================================
async def handler_esperando_pago(user_text: str, to_number: str, msg_id: str = None):
    """Bot simple sin IA. Espera confirmación de pago o cancela."""
    
    await marcar_leido_wpp(msg_id)
    
    texto_lower = user_text.lower().strip()
    
    # --- CANCELAR ---
    if any(palabra in texto_lower for palabra in ["cancelar", "cancelo", "no quiero", "anular"]):
        db = SessionLocal()
        try:
            cliente = db.query(Cliente).filter(Cliente.telefono == to_number).first()
            if cliente:
                pago = db.query(Pago).filter(
                    Pago.cliente_id == cliente.id, Pago.estado == "pendiente"
                ).first()
                if pago:
                    pago.estado = "cancelado"
                
                cliente.estado_agente = "principal"
                db.commit()
                print(f"[🔄 SWITCH] Cliente {to_number} → 'principal' (pago cancelado)")
            
            await enviar_mensaje_wpp(to_number, "Listo, cancelé el pago pendiente. Si necesitás algo más, acá estoy 😊")
        finally:
            db.close()
        return
    
    # --- YA PAGUÉ → Verificar con MP ---
    if any(palabra in texto_lower for palabra in ["pagué", "pague", "transferí", "transferi", "ya pague", "listo", "pagado", "abonado", "aboné"]):
        from services.confirmadora_pagos import verificar_pago_cliente
        await verificar_pago_cliente(to_number)
        return
    
    # --- CUALQUIER OTRA COSA ---
    await enviar_mensaje_wpp(to_number,
        "Aguardo la confirmación del abono para confirmar tu turno 😊\n\n"
        "Si ya transferiste, escribime 'ya pagué'.\n"
        "Si necesitás cancelar, decime 'cancelar'.")
