from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
from database import Base
import uuid
import datetime

class Empresa(Base):
    __tablename__ = "empresas"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    nombre = Column(String, nullable=False)
    telefono_bot = Column(String, unique=True, index=True)
    prompt_personalidad = Column(String, nullable=False)
    
    # --- CONFIG DE SERVICIOS ---
    acepta_pagos = Column(Boolean, default=False)
    monto_sena = Column(Integer, nullable=True)              # Monto en pesos (ej: 1 para demo)
    alias_pago = Column(String, nullable=True)               # "Walter.mate3"
    cvu_pago = Column(String, nullable=True)                 # CVU para transferencias
    descripcion_pago = Column(String, nullable=True)         # "Seña para turno"
    usa_calendar = Column(Boolean, default=False)
    usa_seguimiento = Column(Boolean, default=True)
    
    clientes = relationship("Cliente", back_populates="empresa")

class Cliente(Base):
    __tablename__ = "clientes"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    empresa_id = Column(String, ForeignKey("empresas.id"))
    telefono = Column(String, index=True, nullable=False)
    es_confianza = Column(Boolean, default=False)
    mensajes_enviados = Column(Integer, default=0)
    
    # EL ENRUTADOR: 'principal', 'agendadora', 'agendar_y_pagar', 'esperando_pago', o 'manual'
    estado_agente = Column(String, default="principal", nullable=False)
    
    # La memoria consolidada por el Analista Background
    datos_extraidos = Column(JSON, default={}) 
    
    # Datos del paciente (MVP Clínica Abriness)
    nombre_completo = Column(String, nullable=True)
    dni = Column(String, nullable=True)
    obra_social = Column(String, nullable=True)
    numero_afiliado = Column(String, nullable=True)
    fecha_nacimiento = Column(String, nullable=True)
    mail = Column(String, nullable=True)
    
    empresa = relationship("Empresa", back_populates="clientes")
    mensajes = relationship("Mensaje", back_populates="cliente", cascade="all, delete-orphan")
    cola_analisis = relationship("ColaAnalisis", back_populates="cliente", uselist=False, cascade="all, delete-orphan")
    seguimientos = relationship("Seguimiento", back_populates="cliente", cascade="all, delete-orphan")
    pagos = relationship("Pago", back_populates="cliente", cascade="all, delete-orphan")

class Mensaje(Base):
    __tablename__ = "mensajes"
    
    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(String, ForeignKey("clientes.id"))
    rol = Column(String, nullable=False)
    texto = Column(String, nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.datetime.utcnow)
    
    cliente = relationship("Cliente", back_populates="mensajes")

class ColaAnalisis(Base):
    """Tabla para el job de las 21:00hs. Solo charlas activas del día."""
    __tablename__ = "cola_analisis"
    
    cliente_id = Column(String, ForeignKey("clientes.id"), primary_key=True)
    fecha_ultima_actividad = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    cliente = relationship("Cliente", back_populates="cola_analisis")

class Seguimiento(Base):
    """Sistema de remarketing y retención."""
    __tablename__ = "seguimientos"
    
    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(String, ForeignKey("clientes.id"))
    estado = Column(String, default="pendiente") # 'pendiente' o 'enviado'
    fecha_programada = Column(DateTime, nullable=False)
    
    cliente = relationship("Cliente", back_populates="seguimientos")

class Pago(Base):
    """Registro de pagos pendientes/confirmados via transferencia."""
    __tablename__ = "pagos"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cliente_id = Column(String, ForeignKey("clientes.id"))
    monto = Column(Integer, nullable=False)
    estado = Column(String, default="pendiente")             # pendiente / aprobado / cancelado
    mp_payment_id = Column(String, nullable=True)            # ID del pago cuando MP confirma
    nombre_pagador = Column(String, nullable=True)           # Nombre que devuelve MP
    detalle_turno = Column(String, nullable=True)            # "Reunión 30/04 14:00hs"
    fecha_creacion = Column(DateTime, default=datetime.datetime.utcnow)
    fecha_confirmacion = Column(DateTime, nullable=True)
    
    cliente = relationship("Cliente", back_populates="pagos")