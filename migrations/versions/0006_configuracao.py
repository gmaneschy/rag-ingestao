"""configuracao: ajustes feitos pela interface, que valem entre o .env e o parâmetro da pergunta

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE configuracao (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL,
            atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS configuracao")
