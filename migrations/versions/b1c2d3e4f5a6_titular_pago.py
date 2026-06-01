"""titular_pago en empresas

Revision ID: b1c2d3e4f5a6
Revises: 4faaa4399732
Create Date: 2026-06-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '4faaa4399732'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('empresas', sa.Column('titular_pago', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('empresas', 'titular_pago')
