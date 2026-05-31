"""multi_tenant_campos

Revision ID: 4faaa4399732
Revises: e085c6052f22
Create Date: 2026-05-30 12:39:56.459650

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4faaa4399732'
down_revision: Union[str, None] = 'e085c6052f22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nuevos campos multi-tenant en Empresa
    op.add_column('empresas', sa.Column('bot_activo',           sa.Boolean(), nullable=True))
    op.add_column('empresas', sa.Column('numero_walter',        sa.String(),  nullable=True))
    op.add_column('empresas', sa.Column('tools_habilitadas',    sa.JSON(),    nullable=True))
    op.add_column('empresas', sa.Column('phone_number_id',      sa.String(),  nullable=True))
    op.add_column('empresas', sa.Column('webhook_verify_token', sa.String(),  nullable=True))
    op.add_column('empresas', sa.Column('calendar_id',          sa.String(),  nullable=True))
    op.create_unique_constraint('uq_empresas_phone_number_id', 'empresas', ['phone_number_id'])

    # bot_activo en Cliente
    op.add_column('clientes', sa.Column('bot_activo', sa.Boolean(), nullable=True))

    # empresa_id en Mensaje
    op.add_column('mensajes', sa.Column('empresa_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_mensajes_empresa_id', 'mensajes', 'empresas', ['empresa_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_mensajes_empresa_id', 'mensajes', type_='foreignkey')
    op.drop_column('mensajes', 'empresa_id')
    op.drop_column('clientes', 'bot_activo')
    op.drop_constraint('uq_empresas_phone_number_id', 'empresas', type_='unique')
    op.drop_column('empresas', 'calendar_id')
    op.drop_column('empresas', 'webhook_verify_token')
    op.drop_column('empresas', 'phone_number_id')
    op.drop_column('empresas', 'tools_habilitadas')
    op.drop_column('empresas', 'numero_walter')
    op.drop_column('empresas', 'bot_activo')
