"""tipo de cada trecho: corpo, notas, bibliografia, indice, copyright ou fragmento

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trechos já ingeridos ficam como 'corpo' até o documento ser reprocessado
    # (POST /documentos/{id}/reprocessar), que é quando a classificação roda.
    op.execute(
        "ALTER TABLE trecho ADD COLUMN tipo TEXT NOT NULL DEFAULT 'corpo' "
        "CHECK (tipo IN ('corpo', 'notas', 'bibliografia', 'indice', 'copyright', 'fragmento'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE trecho DROP COLUMN IF EXISTS tipo")
