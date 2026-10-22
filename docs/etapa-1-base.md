# Etapa 1 — Base

O que esta etapa acrescenta ao projeto gerado pelo template:

- `migrations/versions/0002_modelo_de_dados.py`: tabelas `documento`, `trecho` (com `VECTOR(384)`,
  índice HNSW e índice GIN em português), `consulta` e `evento_documento`.
- `app/llm/`: interfaces `GeradorDeEmbeddings` e `GeradorDeResposta` (`typing.Protocol`),
  implementações simuladas e uma fábrica que escolhe o provedor por variável de ambiente.
- Testes unitários (sem banco) e de integração (com Postgres + pgvector).

## Decisões do modelo de dados

| Decisão | Motivo |
|---|---|
| `trecho.documento_id ... ON DELETE CASCADE` | Apagar um documento apaga tudo o que veio dele. |
| `UNIQUE (colecao, hash_conteudo)` | O mesmo conteúdo não entra duas vezes na coleção. |
| `UNIQUE (colecao, nome)` | Um nome é um documento; uma versão nova o substitui. |
| `consulta.fontes` é JSONB com o texto citado | Apagar o documento não quebra respostas antigas. |
| `evento_documento.documento_id` sem chave estrangeira | O registro de remoção sobrevive à remoção; não guarda conteúdo. |
| `modelo_embedding` e `dimensao` em cada trecho | Dá para saber de qual modelo veio cada vetor. |

## Variáveis de ambiente (todas opcionais)

| Variável | Padrão | Observação |
|---|---|---|
| `EMBEDDINGS_PROVIDER` | `simulado` | `simulado`, `local` ou `api` (local/api chegam na etapa 2) |
| `LLM_PROVIDER` | `simulado` | `simulado` ou `api` (etapas 3 e 5) |
| `EMBEDDINGS_MODEL` | `intfloat/multilingual-e5-small` | candidato; a escolha final sai da avaliação |
| `EMBEDDINGS_DIM` | `384` | deve bater com a coluna; outro valor é recusado na subida |
| `LLM_API_KEY` | — | obrigatória se algum provedor for `api` |
| `MAX_TOKENS_POR_PERGUNTA` | `2000` | aplicado na etapa 3 |
| `LIMITE_DIARIO_GASTO` | `1.0` | em dólares; aplicado na etapa 5 |

## Rodar os testes

```powershell
uv sync
uv run pytest                      # unitários; os de banco são ignorados
docker compose up -d --wait db
$env:TEST_DATABASE_URL = "postgresql+psycopg://app:SUA_SENHA@localhost:5432/rag_ingestao"
uv run pytest                      # inclui o modelo de dados
```

Atenção: `test_modelo_de_dados.py` recria o schema `public` do banco apontado. Use um banco de teste.
