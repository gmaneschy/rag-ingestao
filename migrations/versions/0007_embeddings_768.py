"""trecho.vetor passa de 384 para 768 dimensões (google/embeddinggemma-300m)

A coluna tem dimensão fixa, então os vetores antigos não servem: os trechos são apagados e todo
documento processado volta a 'pendente'. Ao subir, o app reprocessa os pendentes a partir dos
arquivos originais (que ficam em dados/). Respostas antigas não mudam: a consulta guarda o texto
citado, não uma referência ao trecho.

Antes de aplicar, ajuste o .env: EMBEDDINGS_MODEL=google/embeddinggemma-300m e EMBEDDINGS_DIM=768
(ou apague as duas linhas, que são o padrão agora).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _trocar_dimensao(dimensao: int) -> None:
    op.execute("DROP INDEX IF EXISTS idx_trecho_vetor")
    op.execute("DELETE FROM trecho")
    op.execute(f"ALTER TABLE trecho ALTER COLUMN vetor TYPE VECTOR({dimensao})")
    op.execute("CREATE INDEX idx_trecho_vetor ON trecho USING hnsw (vetor vector_cosine_ops)")
    op.execute(
        "UPDATE documento SET estado = 'pendente', mensagem_erro = NULL, atualizado_em = now() "
        "WHERE estado = 'processado'"
    )


def upgrade() -> None:
    _trocar_dimensao(768)


def downgrade() -> None:
    _trocar_dimensao(384)
