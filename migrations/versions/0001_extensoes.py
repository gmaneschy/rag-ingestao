"""extensões do banco

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # As extensões ficam: removê-las apagaria colunas que dependem delas.
    pass
