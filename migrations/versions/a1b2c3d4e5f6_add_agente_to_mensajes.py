"""add_agente_to_mensajes

Revision ID: a1b2c3d4e5f6
Revises: dd44e5dbe40a
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'dd44e5dbe40a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('mensajes', sa.Column('agente', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('mensajes', 'agente')
