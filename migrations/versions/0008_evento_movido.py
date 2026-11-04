"""evento_documento passa a aceitar o tipo 'movido' (documento trocado de coleção)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE evento_documento DROP CONSTRAINT evento_documento_tipo_check")
    op.execute(
        "ALTER TABLE evento_documento ADD CONSTRAINT evento_documento_tipo_check "
        "CHECK (tipo IN ('ingerido', 'removido', 'reprocessado', 'movido'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM evento_documento WHERE tipo = 'movido'")
    op.execute("ALTER TABLE evento_documento DROP CONSTRAINT evento_documento_tipo_check")
    op.execute(
        "ALTER TABLE evento_documento ADD CONSTRAINT evento_documento_tipo_check "
        "CHECK (tipo IN ('ingerido', 'removido', 'reprocessado'))"
    )
