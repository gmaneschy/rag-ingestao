"""documento.progresso, etapa e processando_desde: andamento da ingestão

Só existem enquanto o documento está sendo processado; ao terminar (ou falhar) voltam a NULL.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documento ADD COLUMN progresso SMALLINT "
        "CHECK (progresso BETWEEN 0 AND 100), "
        "ADD COLUMN etapa TEXT, ADD COLUMN processando_desde TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE documento DROP COLUMN progresso, DROP COLUMN etapa, "
        "DROP COLUMN processando_desde"
    )
