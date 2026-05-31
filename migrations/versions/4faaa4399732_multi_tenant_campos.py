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
    # Usar ADD COLUMN IF NOT EXISTS para que sea idempotente.
    # Necesario porque create_all() ya pudo haber creado algunas de estas columnas
    # en el primer deploy antes de que existiera esta migración.
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS bot_activo           BOOLEAN")
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS numero_walter        VARCHAR")
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS tools_habilitadas    JSON")
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS phone_number_id      VARCHAR")
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS webhook_verify_token VARCHAR")
    op.execute("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS calendar_id          VARCHAR")

    # Crear el unique constraint solo si no existe ya
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_empresas_phone_number_id'
            ) THEN
                ALTER TABLE empresas
                ADD CONSTRAINT uq_empresas_phone_number_id UNIQUE (phone_number_id);
            END IF;
        END $$;
    """)

    op.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS bot_activo  BOOLEAN")
    op.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS empresa_id  VARCHAR")

    # FK en mensajes → empresas, solo si no existe
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_mensajes_empresa_id'
            ) THEN
                ALTER TABLE mensajes
                ADD CONSTRAINT fk_mensajes_empresa_id
                FOREIGN KEY (empresa_id) REFERENCES empresas(id);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE mensajes DROP CONSTRAINT IF EXISTS fk_mensajes_empresa_id")
    op.execute("ALTER TABLE mensajes  DROP COLUMN IF EXISTS empresa_id")
    op.execute("ALTER TABLE clientes  DROP COLUMN IF EXISTS bot_activo")
    op.execute("ALTER TABLE empresas  DROP CONSTRAINT IF EXISTS uq_empresas_phone_number_id")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS calendar_id")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS webhook_verify_token")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS phone_number_id")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS tools_habilitadas")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS numero_walter")
    op.execute("ALTER TABLE empresas  DROP COLUMN IF EXISTS bot_activo")
