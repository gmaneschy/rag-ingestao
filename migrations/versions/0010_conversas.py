"""conversas e turnos: o chat da interface

`turno` é uma pergunta com a sua resposta. A resposta mora em `consulta` (a mesma do histórico, da
avaliação "ajudou" e da remoção sem rastro); apagar a consulta apaga o turno junto, e o contrário
é feito pelo serviço (apagar a conversa apaga as consultas dos seus turnos).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversa (
            id BIGSERIAL PRIMARY KEY,
            titulo TEXT NOT NULL,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            criada_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            atualizada_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_conversa_atualizada_em ON conversa (atualizada_em DESC)")
    op.execute(
        """
        CREATE TABLE turno (
            id BIGSERIAL PRIMARY KEY,
            conversa_id BIGINT NOT NULL REFERENCES conversa (id) ON DELETE CASCADE,
            pergunta TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'gerando'
                CHECK (estado IN ('gerando', 'pronta', 'cancelada', 'erro')),
            geracao INTEGER NOT NULL DEFAULT 1,
            consulta_id BIGINT REFERENCES consulta (id) ON DELETE CASCADE,
            erro TEXT,
            aviso TEXT,
            diagnostico JSONB,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_turno_conversa ON turno (conversa_id, id)")


def downgrade() -> None:
    op.execute("DROP TABLE turno")
    op.execute("DROP TABLE conversa")
