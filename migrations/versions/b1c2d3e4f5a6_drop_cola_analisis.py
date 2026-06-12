"""drop_cola_analisis

Revision ID: b1c2d3e4f5a6
Revises: 71c64c55323d
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '71c64c55323d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('cola_analisis')


def downgrade() -> None:
    op.create_table(
        'cola_analisis',
        sa.Column('cliente_id', sa.String(), nullable=False),
        sa.Column('fecha_ultima_actividad', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['cliente_id'], ['clientes.id']),
        sa.PrimaryKeyConstraint('cliente_id'),
    )
