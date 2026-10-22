"""modelo de dados do RAG: documento, trecho, consulta e evento_documento

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# A dimensão do vetor depende do modelo de embeddings. Trocar por um modelo de outra dimensão
# exige uma nova migração e reindexar o corpus (veja app/llm/config.py, que recusa a configuração
# incompatível em vez de misturar vetores de modelos diferentes).
DIMENSAO_VETOR = 384


def upgrade() -> None:
    # O documento é o arquivo e o seu estado. Duas restrições de unicidade evitam duplicatas:
    # o mesmo nome na coleção é o mesmo documento (uma versão nova o substitui) e o mesmo
    # conteúdo (hash) não entra duas vezes na coleção.
    op.execute(
        """
        CREATE TABLE documento (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            hash_conteudo TEXT NOT NULL CHECK (length(hash_conteudo) = 64),
            versao INTEGER NOT NULL DEFAULT 1 CHECK (versao >= 1),
            colecao TEXT NOT NULL DEFAULT 'geral',
            estado TEXT NOT NULL DEFAULT 'pendente'
                CHECK (estado IN ('pendente', 'processado', 'falhou')),
            caminho_arquivo TEXT NOT NULL,
            mensagem_erro TEXT,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (colecao, nome),
            UNIQUE (colecao, hash_conteudo)
        )
        """
    )

    # O trecho é o texto com o seu vetor. ON DELETE CASCADE é o que garante que apagar um
    # documento apague também tudo o que veio dele.
    op.execute(
        f"""
        CREATE TABLE trecho (
            id BIGSERIAL PRIMARY KEY,
            documento_id BIGINT NOT NULL REFERENCES documento(id) ON DELETE CASCADE,
            posicao INTEGER NOT NULL,
            pagina INTEGER,
            texto TEXT NOT NULL,
            vetor VECTOR({DIMENSAO_VETOR}) NOT NULL,
            modelo_embedding TEXT NOT NULL,
            dimensao INTEGER NOT NULL,
            UNIQUE (documento_id, posicao)
        )
        """
    )
    op.execute("CREATE INDEX idx_trecho_vetor ON trecho USING hnsw (vetor vector_cosine_ops)")
    # A consulta de busca textual precisa usar exatamente esta expressão para aproveitar o índice.
    op.execute(
        "CREATE INDEX idx_trecho_texto ON trecho USING gin (to_tsvector('portuguese', texto))"
    )

    # A consulta guarda o texto citado, e não uma referência ao trecho: apagar um documento
    # não pode quebrar respostas antigas.
    op.execute(
        """
        CREATE TABLE consulta (
            id BIGSERIAL PRIMARY KEY,
            pergunta TEXT NOT NULL,
            resposta TEXT NOT NULL,
            fontes JSONB NOT NULL DEFAULT '[]'::jsonb,
            modelo_geracao TEXT,
            tokens_entrada INTEGER NOT NULL DEFAULT 0,
            tokens_saida INTEGER NOT NULL DEFAULT 0,
            latencia_ms INTEGER,
            custo NUMERIC(12, 6) NOT NULL DEFAULT 0,
            ajudou BOOLEAN,
            criada_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_consulta_criada_em ON consulta (criada_em)")

    # O evento sobrevive à remoção do documento, por isso documento_id NÃO é chave estrangeira.
    # Não guarda o conteúdo. Se a remoção do arquivo falhar, limpeza_pendente fica ligada e
    # caminho_arquivo diz o que apagar na próxima tentativa.
    op.execute(
        """
        CREATE TABLE evento_documento (
            id BIGSERIAL PRIMARY KEY,
            documento_id BIGINT NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('ingerido', 'removido', 'reprocessado')),
            ator TEXT NOT NULL DEFAULT 'sistema',
            limpeza_pendente BOOLEAN NOT NULL DEFAULT false,
            caminho_arquivo TEXT,
            em TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_evento_documento_id ON evento_documento (documento_id)")
    op.execute("CREATE INDEX idx_evento_limpeza ON evento_documento (id) WHERE limpeza_pendente")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evento_documento")
    op.execute("DROP TABLE IF EXISTS consulta")
    op.execute("DROP TABLE IF EXISTS trecho")
    op.execute("DROP TABLE IF EXISTS documento")
