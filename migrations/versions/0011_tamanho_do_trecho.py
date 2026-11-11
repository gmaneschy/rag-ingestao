"""documento.tamanho_trecho e sobreposicao_trecho: com que tamanho o documento foi dividido

Permite avisar, na Configuração, quantos documentos foram processados com um tamanho diferente do
atual. Os documentos existentes recebem 500 e 80, os padrões usados até aqui.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE documento ADD COLUMN tamanho_trecho INTEGER, "
        "ADD COLUMN sobreposicao_trecho INTEGER"
    )
    op.execute(
        "UPDATE documento SET tamanho_trecho = 500, sobreposicao_trecho = 80 "
        "WHERE estado = 'processado'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE documento DROP COLUMN tamanho_trecho, DROP COLUMN sobreposicao_trecho")
