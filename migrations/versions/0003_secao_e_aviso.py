"""seção de cada trecho e aviso do documento

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Título da seção a que o trecho pertence (ex.: "Parte I > Capítulo 2"); vazio se o documento
    # não tem estrutura detectável. Detectada na ingestão por heurística, então pode errar.
    op.execute("ALTER TABLE trecho ADD COLUMN secao TEXT")
    # Algo que o usuário deve saber sobre a ingestão que, mesmo assim, deu certo
    # (ex.: páginas sem texto extraível).
    op.execute("ALTER TABLE documento ADD COLUMN aviso TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE documento DROP COLUMN IF EXISTS aviso")
    op.execute("ALTER TABLE trecho DROP COLUMN IF EXISTS secao")
