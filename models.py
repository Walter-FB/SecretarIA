from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, JSON, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base
import uuid
import datetime


class Empresa(Base):
    __tablename__ = "empresas"

    id                  = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    nombre              = Column(String, nullable=False)
    telefono_bot        = Column(String, unique=True, index=True)
    prompt_personalidad = Column(String, nullable=False)

    acepta_pagos      = Column(Boolean, default=False)
    monto_sena        = Column(Integer, nullable=True)
    alias_pago        = Column(String, nullable=True)
    cvu_pago          = Column(String, nullable=True)
    descripcion_pago  = Column(String, nullable=True)
    usa_calendar      = Column(Boolean, default=False)
    usa_seguimiento   = Column(Boolean, default=True)

    # Multi-tenant / control
    bot_activo           = Column(Boolean, default=True)
    numero_walter        = Column(String,  nullable=True)        # Número WA del admin (notificaciones y /mute)
    tools_habilitadas    = Column(JSON,    nullable=True)        # None = todas; lista = solo esas
    phone_number_id      = Column(String,  unique=True, nullable=True)  # Meta PHONE_NUMBER_ID del bot
    webhook_verify_token = Column(String,  nullable=True)        # token de verificación por empresa (futuro)
    calendar_id          = Column(String,  nullable=True)        # Google Calendar ID (fallback: env CALENDAR_ID)

    clientes      = relationship("Cliente",      back_populates="empresa")
    profesionales = relationship("Profesional",  back_populates="empresa")


class Profesional(Base):
    """Cada profesional de la clínica con su propia tarifa y (futuro) calendario."""
    __tablename__ = "profesionales"

    id                 = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    empresa_id         = Column(String, ForeignKey("empresas.id"))
    nombre             = Column(String, nullable=False)   # "Lic. Renals", "Dr. Barros"
    especialidad       = Column(String, nullable=False)   # "psicologo", "psiquiatra"
    tarifa_particular  = Column(Integer, nullable=False)
    tarifa_obra_social = Column(Integer, nullable=False)
    calendar_id        = Column(String, nullable=True)    # null → usa env var CALENDAR_ID
    activo             = Column(Boolean, default=True)

    empresa   = relationship("Empresa",  back_populates="profesionales")
    clientes  = relationship("Cliente",  back_populates="profesional")


class Cliente(Base):
    __tablename__ = "clientes"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    empresa_id    = Column(String, ForeignKey("empresas.id"))
    profesional_id = Column(String, ForeignKey("profesionales.id"), nullable=True)
    telefono      = Column(String, index=True, nullable=False)
    es_confianza      = Column(Boolean, default=False)
    bot_activo        = Column(Boolean, default=True)    # False = bot ignora mensajes de este cliente
    mensajes_enviados = Column(Integer, default=0)

    # EL ENRUTADOR: 'principal', 'agendadora', 'esperando_mail', 'manual' (desenchufado, solo manual desde BD)
    estado_agente = Column(String, default="principal", nullable=False)

    # Memoria AI (solo resúmenes — datos reales viven en columnas directas)
    datos_extraidos = Column(JSON, default={})

    # Datos del paciente
    nombre_completo  = Column(String, nullable=True)
    dni              = Column(String, nullable=True)
    obra_social      = Column(String, nullable=True)
    numero_afiliado  = Column(String, nullable=True)
    fecha_nacimiento = Column(String, nullable=True)
    mail             = Column(String, nullable=True)

    empresa      = relationship("Empresa",      back_populates="clientes")
    profesional  = relationship("Profesional",  back_populates="clientes")
    mensajes     = relationship("Mensaje",      back_populates="cliente", cascade="all, delete-orphan")
    cola_analisis = relationship("ColaAnalisis", back_populates="cliente", uselist=False, cascade="all, delete-orphan")
    seguimientos = relationship("Seguimiento",  back_populates="cliente", cascade="all, delete-orphan")
    pagos        = relationship("Pago",         back_populates="cliente", cascade="all, delete-orphan")


class Mensaje(Base):
    __tablename__ = "mensajes"

    id            = Column(Integer, primary_key=True, index=True)
    cliente_id    = Column(String, ForeignKey("clientes.id"))
    empresa_id    = Column(String, ForeignKey("empresas.id"), nullable=True)
    rol           = Column(String, nullable=False)
    agente        = Column(String, nullable=True)   # "principal" | "agendadora" | None (legacy)
    texto         = Column(String, nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.datetime.utcnow)

    cliente = relationship("Cliente", back_populates="mensajes")


class ColaAnalisis(Base):
    """Clientes con conversación activa pendiente de análisis nocturno."""
    __tablename__ = "cola_analisis"

    cliente_id             = Column(String, ForeignKey("clientes.id"), primary_key=True)
    fecha_ultima_actividad = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    cliente = relationship("Cliente", back_populates="cola_analisis")


class Seguimiento(Base):
    """Cola de mensajes de remarketing pendientes."""
    __tablename__ = "seguimientos"

    id               = Column(Integer, primary_key=True, index=True)
    cliente_id       = Column(String, ForeignKey("clientes.id"))
    estado           = Column(String, default="pendiente")  # pendiente / enviado
    fecha_programada = Column(DateTime, nullable=False)

    cliente = relationship("Cliente", back_populates="seguimientos")


class Turno(Base):
    """Turno reservado para profesionales que usan motor local (sin Google Calendar)."""
    __tablename__ = "turnos"

    id                = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    profesional_id    = Column(String, ForeignKey("profesionales.id"), nullable=False)
    cliente_id        = Column(String, ForeignKey("clientes.id"), nullable=True)
    empresa_id        = Column(String, ForeignKey("empresas.id"), nullable=False)
    fecha_hora_inicio = Column(DateTime, nullable=False)
    fecha_hora_fin    = Column(DateTime, nullable=False)
    estado            = Column(String, default="reservado")  # reservado | cancelado

    __table_args__ = (
        UniqueConstraint("profesional_id", "fecha_hora_inicio", name="uq_turno_profesional_hora"),
    )


class Pago(Base):
    """Registro de pagos por transferencia bancaria."""
    __tablename__ = "pagos"

    id                 = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    cliente_id         = Column(String, ForeignKey("clientes.id"))
    monto              = Column(Integer, nullable=False)
    estado             = Column(String, default="pendiente")   # pendiente / aprobado / cancelado
    mp_payment_id      = Column(String, nullable=True)
    nombre_pagador     = Column(String, nullable=True)
    detalle_turno      = Column(String, nullable=True)
    fecha_creacion     = Column(DateTime, default=datetime.datetime.utcnow)
    fecha_confirmacion = Column(DateTime, nullable=True)

    cliente = relationship("Cliente", back_populates="pagos")
