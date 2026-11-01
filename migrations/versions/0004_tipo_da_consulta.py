"""tipo da consulta: factual ou analise

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guardar o tipo permite medir separado (a avaliação só mede o factual) e filtrar o histórico.
    op.execute(
        "ALTER TABLE consulta ADD COLUMN tipo TEXT NOT NULL DEFAULT 'factual' "
        "CHECK (tipo IN ('factual', 'analise'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE consulta DROP COLUMN IF EXISTS tipo")
