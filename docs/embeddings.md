# Experimento de modelos de embeddings

```powershell
uv sync
uv run python -m app.avaliacao embeddings --modelos "intfloat/multilingual-e5-large"
```

O comando lê do banco os trechos `corpo` dos documentos citados em `avaliacao/perguntas.json`, vetoriza-os
na memória com cada modelo e mede a recuperação (R@1, 3, 5, 10, 20 e MRR@10) em busca vetorial e híbrida.
Não altera o banco nem a coluna `trecho.vetor`. O modelo em uso (`EMBEDDINGS_MODEL` do `.env`) é lido do
banco, sem recalcular. Os vetores ficam em `avaliacao/embeddings_cache`; se interromper, rode de novo e
ele continua de onde parou. Ao fim, imprime e salva `avaliacao/resultados/AAAAMMDD-HHMMSS-embeddings.md`,
com a posição do trecho certo por pergunta e por modelo.

Candidatos (todos no fastembed, ver `app/llm/perfis.py`; o prefixo de pergunta/trecho de cada um é aplicado):

| Modelo | dim | tamanho | observação |
|---|---|---|---|
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384 | 0,22 GB | o atual; enxerga ~128 tokens |
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 768 | 1,0 GB | mesma família, maior |
| `intfloat/multilingual-e5-large` | 1024 | 2,24 GB | feito para busca; MIT |
| `google/embeddinggemma-300m` | 768 | 1,24 GB | 2048 tokens; licença Gemma |
| `Qwen/Qwen3-Embedding-0.6B-Q` | 1024 | 1,12 GB | quantizado; Apache-2.0 |
| `jinaai/jina-embeddings-v3` | 1024 | 2,29 GB | licença **não comercial** |

## Resultado e decisão

Busca híbrida, 32 perguntas, 5274 trechos: o `embeddinggemma-300m` fez R@5 87% / R@20 91% / MRR 0,675,
contra 65% / 83% / 0,545 do MiniLM; o e5-large ficou em 74% / 87% / 0,576 e é 2,6x mais lento
(1,4 trechos/s contra 3,7); mpnet 74% / 91% / 0,602; Qwen3-Q, 65% / 74% / 0,477. **Adotado: o
`embeddinggemma-300m`, 768 dimensões** (migração `0007`).

Casos que nenhum modelo achou (Tolstói em Astápovo, rotina de escrita de Balzac) e a pergunta de lista
(processos por obscenidade, posição 10–12 em todos) não são limite de embedding: investigar com
`conferir`/`inspecionar`.

## Migração

`alembic upgrade head` troca `trecho.vetor` para 768 dimensões, apaga os trechos e devolve os documentos
processados ao estado `pendente`; ao subir, o app os reprocessa a partir dos arquivos originais. As
respostas do histórico não mudam. Apague `EMBEDDINGS_MODEL`/`EMBEDDINGS_DIM` antigos do `.env`.
