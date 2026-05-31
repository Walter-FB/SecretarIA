"""tablas_base — schema original antes de cualquier migración incremental

Revision ID: c0000000000a
Revises:
Create Date: 2026-05-31

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c0000000000a'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # empresas — sin columnas multi-tenant (las agrega 4faaa4399732)
    op.create_table(
        'empresas',
        sa.Column('id',                  sa.String(),  primary_key=True),
        sa.Column('nombre',              sa.String(),  nullable=False),
        sa.Column('telefono_bot',        sa.String(),  unique=True,  nullable=True),
        sa.Column('prompt_personalidad', sa.String(),  nullable=False),
        sa.Column('acepta_pagos',        sa.Boolean(), nullable=True),
        sa.Column('monto_sena',          sa.Integer(), nullable=True),
        sa.Column('alias_pago',          sa.String(),  nullable=True),
        sa.Column('cvu_pago',            sa.String(),  nullable=True),
        sa.Column('descripcion_pago',    sa.String(),  nullable=True),
        sa.Column('usa_calendar',        sa.Boolean(), nullable=True),
        sa.Column('usa_seguimiento',     sa.Boolean(), nullable=True),
    )

    # clientes — sin bot_activo ni profesional_id (los agregan migraciones posteriores)
    op.create_table(
        'clientes',
        sa.Column('id',               sa.String(),  primary_key=True),
        sa.Column('empresa_id',       sa.String(),  sa.ForeignKey('empresas.id'), nullable=True),
        sa.Column('telefono',         sa.String(),  nullable=False, index=True),
        sa.Column('es_confianza',     sa.Boolean(), nullable=True),
        sa.Column('mensajes_enviados', sa.Integer(), nullable=True),
        sa.Column('estado_agente',    sa.String(),  nullable=False),
        sa.Column('datos_extraidos',  sa.JSON(),    nullable=True),
        sa.Column('nombre_completo',  sa.String(),  nullable=True),
        sa.Column('dni',              sa.String(),  nullable=True),
        sa.Column('obra_social',      sa.String(),  nullable=True),
        sa.Column('numero_afiliado',  sa.String(),  nullable=True),
        sa.Column('fecha_nacimiento', sa.String(),  nullable=True),
        sa.Column('mail',             sa.String(),  nullable=True),
    )

    # mensajes — sin empresa_id (lo agrega 4faaa4399732)
    op.create_table(
        'mensajes',
        sa.Column('id',             sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cliente_id',     sa.String(),  sa.ForeignKey('clientes.id'), nullable=True),
        sa.Column('rol',            sa.String(),  nullable=False),
        sa.Column('texto',          sa.String(),  nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'cola_analisis',
        sa.Column('cliente_id',              sa.String(),   sa.ForeignKey('clientes.id'), primary_key=True),
        sa.Column('fecha_ultima_actividad',  sa.DateTime(), nullable=True),
    )

    op.create_table(
        'seguimientos',
        sa.Column('id',               sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cliente_id',       sa.String(),  sa.ForeignKey('clientes.id'), nullable=True),
        sa.Column('estado',           sa.String(),  nullable=True),
        sa.Column('fecha_programada', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'pagos',
        sa.Column('id',                 sa.String(),   primary_key=True),
        sa.Column('cliente_id',         sa.String(),   sa.ForeignKey('clientes.id'), nullable=True),
        sa.Column('monto',              sa.Integer(),  nullable=False),
        sa.Column('estado',             sa.String(),   nullable=True),
        sa.Column('mp_payment_id',      sa.String(),   nullable=True),
        sa.Column('nombre_pagador',     sa.String(),   nullable=True),
        sa.Column('detalle_turno',      sa.String(),   nullable=True),
        sa.Column('fecha_creacion',     sa.DateTime(), nullable=True),
        sa.Column('fecha_confirmacion', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('pagos')
    op.drop_table('seguimientos')
    op.drop_table('cola_analisis')
    op.drop_table('mensajes')
    op.drop_table('clientes')
    op.drop_table('empresas')
