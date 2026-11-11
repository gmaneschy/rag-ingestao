# RAG com ingestão de documentos

Documentos que chegam a uma pasta ou a um envio pela tela são processados sozinhos e passam a
responder perguntas **com citação da fonte**. O valor do projeto não está em chamar um LLM, e sim em
três coisas que dá para conferir:

1. **Ingerir bem**: PDF, EPUB, FB2, DOCX, ODT, HTML, MOBI/AZW3, DjVu, TXT e MD, com seções, páginas,
   deduplicação e reprocessamento. Detalhes em [docs/formatos.md](docs/formatos.md).
2. **Remover sem deixar rastro**: apagar um documento tira trechos, vetores, arquivo e texto citado
   das respostas antigas, e há um comando que procura o que sobrou. Veja
   [a prova](docs/remocao-sem-rastro.md).
3. **Provar com medição que as respostas estão certas**: conjunto de referência, recall e MRR,
   recusa correta, comparação de modelos de embeddings, de reordenador e de geração. Veja
   [Resultados medidos](#resultados-medidos).

## Como funciona

```
arquivo ─▶ extração ─▶ seções/páginas ─▶ trechos ─▶ embeddings ─▶ Postgres (pgvector + tsvector)
                                                                       │
pergunta ─▶ busca híbrida (vetor + texto, RRF) ─▶ reordenador ─▶ portão de evidência
                                                                       │
                                          sem evidência: recusa ◀──────┴──────▶ LLM com [n] citações
```

- **Busca híbrida**: vetorial (pgvector, HNSW, cosseno) e textual (tsvector em português), unidas por
  RRF. Com filtro de documento, coleção, seção ou página a busca vetorial é exata: o índice HNSW
  filtraria depois e devolveria pouco ou nada.
- **Portão de evidência**: a nota do reordenador abaixo do mínimo recusa a pergunta *sem chamar o
  modelo*. A recusa é rápida e não inventa.
- **Citações**: cada `[n]` na resposta aponta para uma fonte com documento, página ou seção e o texto.
- **Tudo ajustável sem reiniciar**: Configuração na interface (banco vence `.env`; o parâmetro da
  própria pergunta vence o banco).

## Requisitos

- Python 3.12+ e [uv](https://docs.astral.sh/uv/)
- Docker com Compose (Postgres com pgvector)
- [Ollama](https://ollama.com) para respostas reais (CPU basta; veja os modelos testados abaixo)
- Opcional: [DjVuLibre](https://djvu.sourceforge.net) para `.djvu` fora do Docker

## Começando (local)

```bash
make env        # cria o .env com segredos aleatórios (ou: uv run python scripts/gen_env.py)
make install    # uv sync
make up         # sobe o banco (e o n8n, conforme COMPOSE_PROFILES)
make migrate    # uv run alembic upgrade head
uv run python scripts/baixar_modelo.py   # baixa o modelo de embeddings (~1,2 GB, uma vez)
make dev        # http://127.0.0.1:8000/app (a chave é o API_KEY do .env)
```

No `.env`: `EMBEDDINGS_PROVIDER=local`, `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5:7b` (ou outro:
`ollama pull qwen2.5:7b`). Use `127.0.0.1` e não `localhost` em `DATABASE_URL` no Windows. Com a API
em contêiner e o Ollama no host: `LLM_BASE_URL=http://host.docker.internal:11434`.

Subir tudo no Compose: `COMPOSE_PROFILES=app` (e `proxy`, `n8n`) e `docker compose up -d --build`.

## Usando

**Interface** (`/app`): Documentos (envio por arrastar, filtros, reprocessar e mover em lote),
Perguntar (com citações e diagnóstico de por que recusou), Histórico, Comparar modelos lado a lado,
Configuração, Saúde e Logs. Veja [docs/interface.md](docs/interface.md).

**Pasta de entrada**: um fluxo do n8n observa `dados/entrada` e envia cada arquivo novo. Veja a
seção [n8n](#fluxos-do-n8n-no-git).

**API** (cabeçalho `X-API-Key`; documentação interativa em `/docs`):

| Rota | Para que serve |
|---|---|
| `POST /documentos` | envia um arquivo (`arquivo`, `colecao`); 202 com `novo`, `nova_versao` ou `duplicado` |
| `GET /documentos` · `GET /documentos/{id}` | lista com filtros; detalhe, seções e amostra de trechos |
| `POST /documentos/{id}/reprocessar` · `POST /documentos/reprocessar` | reprocessa um, ou vários (`ids` ou `todos` + filtros) |
| `POST /documentos/mover` | troca a coleção sem reindexar |
| `DELETE /documentos/{id}?consultas=redigir\|apagar` | remove sem rastro |
| `POST /perguntas` | pergunta: `colecoes`, `documento_ids`, `k`, `modo`, `tipo` (`factual` ou `analise`), filtros de seção e página |
| `POST /perguntas/{id}/avaliacao` | marca se a resposta ajudou |
| `GET /saude` | estado do banco (sem chave) |

Exemplo:

```bash
curl -H "X-API-Key: $API_KEY" -F "arquivo=@livro.pdf" -F "colecao=romance" http://127.0.0.1:8000/documentos
curl -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
     -d '{"pergunta": "Onde Lígia viveu?"}' http://127.0.0.1:8000/perguntas
```

No PowerShell use `curl.exe` e defina antes `$env:API_KEY` (o PowerShell não lê o `.env`).

## Remoção sem rastro

```bash
uv run python -m app.avaliacao rastros "frase rara do documento"   # antes: lista onde aparece
# remova o documento (interface, ou DELETE /documentos/{id}?consultas=apagar)
uv run python -m app.avaliacao rastros "frase rara do documento"   # depois: nenhum rastro
```

A varredura olha todas as tabelas, a pasta de arquivos e os logs em memória; os limites (nome do
arquivo no registro de auditoria, linhas mortas até o `VACUUM`, backups) estão em
[docs/remocao-sem-rastro.md](docs/remocao-sem-rastro.md). Resultado medido na seção abaixo.

## Avaliação

```bash
uv run python -m app.avaliacao inspecionar "pergunta"     # acha o trecho que responde
uv run python -m app.avaliacao rodar --modos hibrida,vetorial,textual
uv run python -m app.avaliacao rodar --gerar --rotulo qwen2.5-7b     # inclui o LLM
uv run python -m app.avaliacao falhas avaliacao/resultados/<rodada>.json
uv run python -m app.avaliacao embeddings --modelos "google/embeddinggemma-300m,..."
```

Como montar o conjunto de referência e ler as métricas: [docs/etapa-5a-avaliacao.md](docs/etapa-5a-avaliacao.md).
Experimento de embeddings: [docs/embeddings.md](docs/embeddings.md).

## Resultados medidos

> Conjunto de referência: 52 perguntas (português e inglês; 24 com resposta e 28 sem resposta no
> corpus, várias "próximas do corpus" para testar a recusa), 22 documentos. Hardware: Ryzen 5 2600X,
> 32 GB, só CPU. As tabelas de embeddings abaixo vêm do conjunto anterior, de 32 perguntas.

### Embeddings (busca híbrida, recall de recuperação)

| Modelo | dim | R@5 | R@20 | MRR@10 | trechos/s |
|---|---|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 (antes) | 384 | 65% | 83% | 0,545 | — |
| **embeddinggemma-300m (adotado)** | 768 | **87%** | **91%** | **0,675** | 3,7 |
| paraphrase-multilingual-mpnet-base-v2 | 768 | 74% | 91% | 0,602 | 4,9 |
| multilingual-e5-large | 1024 | 74% | 87% | 0,576 | 1,4 |
| Qwen3-Embedding-0.6B-Q | 1024 | 65% | 74% | 0,477 | 0,9 |

### Reordenador e portão de evidência

Busca híbrida com o reordenador `jina-reranker-v2-base-multilingual` sobre 20 candidatos:

| | sem reordenador | com reordenador |
|---|---|---|
| R@1 | 61% | **74%** |
| R@5 | 83% | **87%** |
| MRR@20 | 0,70 | **0,79** |
| busca (ms) | ~900 | ~5400 |

O reordenador melhora a posição do trecho certo, a um custo de latência de cerca de 4,5 s por
pergunta. O portão de evidência recusa quando a nota do 1º trecho fica abaixo de um mínimo:

| Nota mínima | Recusa correta | Recusa indevida |
|---|---|---|
| -0,080 | 69% | 17% |
| **0,05 (adotada)** | 79% | 22% |
| 0,083 | 83% | 22% |
| 0,451 | 86% | 30% |
| 0,516 | 93% | 39% |

Escolhi 0,05: subir mais troca cada recusa correta a mais por várias respostas boas recusadas, e
para um sistema que promete responder com fonte recusar demais custa mais que deixar passar uma
pergunta limítrofe. O portão por similaridade, sem reordenador, não recusava nada no conjunto novo.
Ressalva: o gabarito de duas perguntas ainda aceita só um dos documentos que respondem, o que
subestima a recuperação (a nota mínima não depende do gabarito).

### Geração (qualidade e latência por modelo)

| Modelo | Recusa correta | Recusa indevida | Fundamentação* | Latência média | p95 |
|---|---|---|---|---|---|
| qwen2.5:7b (adotado) | 93% | 17% | 68% | 45,6 s | 62,7 s |

*Das respostas dadas, as que citam o trecho correto; subestimada onde o gabarito é estreito.
O qwen3.5:4b (`think=false`) ficou em 63–88 s por pergunta contra ~35 s do qwen2.5:7b numa rodada
anterior, e por isso não foi adotado. Pendente: phi4:14b.

### Extração de PDF (pypdf × pdfplumber × PyMuPDF)

`python -m app.avaliacao extratores`, sobre os 9 PDFs do corpus (até 30 páginas cada):

| Extrator | ms/página | palavras coladas (por mil) | concordância com o pypdf |
|---|---|---|---|
| **pypdf (adotado)** | 68 | 0,5 | — |
| PyMuPDF | 2,7 | 0,2 | 98% das palavras, 99% da ordem |
| pdfplumber (amostra de 5 PDFs) | 149 | 0,7 | 97% das palavras, 96% da ordem |

O PyMuPDF é mais rápido e um pouco mais limpo, mas é AGPL; a extração é pequena diante do custo dos
vetores, e o pypdf (BSD) mantém a licença simples. O pdfplumber foi o mais lento e o pior em
palavras coladas. Defeitos conhecidos do pypdf: dígitos separados ("3 5 8") e alguma palavra
partida em alfabeto cirílico, e o marcador `<UN>` de fonte sem mapeamento.

### Tamanho do trecho

Não medimos 300 × 500 × 800: o tamanho depende do tipo de documento (um artigo de lei pede trechos
diferentes de um romance), então ele é um ajuste do usuário, por perfil (curto, médio, longo ou
personalizado), com explicações na tela. Só o perfil curto tem nota mínima calibrada. Detalhes em
[docs/configuracao-da-ingestao.md](docs/configuracao-da-ingestao.md).

### Remoção sem rastro

*A preencher com a saída dos dois comandos `rastros` (antes e depois) de uma remoção real.*

## Fluxos do n8n no git

Os fluxos ficam em `n8n/workflows/*.json`, um arquivo por fluxo (o nome do arquivo é o id do fluxo).

```bash
make n8n-export   # n8n em execução -> n8n/workflows/ (rode antes de commitar mudanças feitas no editor)
make n8n-import   # n8n/workflows/ -> n8n (em outra máquina ou num n8n limpo; reinicia o n8n)
```

- Credenciais **não** são exportadas: o JSON guarda só a referência. Recrie-as no editor de cada ambiente.
- Fluxos marcados como `"active": true` no JSON são publicados na importação.
- Importar sobrescreve fluxos com o mesmo id.
- `n8n/workflows/ExemploSaude0001.json` é um exemplo: um webhook `GET /webhook/saude` que consulta
  `http://api:8000/saude`. Para ele responder, a API precisa estar no ar no Compose (profile `app`).
  Teste com `curl http://localhost:5678/webhook/saude`, uns 20 segundos depois de importar: logo após
  o reinício o n8n já responde, mas ainda devolve 404 até terminar de registrar os webhooks.
- Num n8n novo, a primeira visita ao editor (http://localhost:5678) pede para criar a conta de
  dono ("Set up owner account"). É normal e acontece uma vez por volume; os fluxos importados já
  estão lá, e os webhooks funcionam mesmo antes de você criar a conta.

## Profiles do Compose

O `COMPOSE_PROFILES` do `.env` escolhe o que sobe junto com o banco (que sobe sempre):

| profile | serviço |
|---------|---------|
| `app` | a API em contêiner (aplica as migrações ao iniciar) |
| `proxy` | Caddy, com HTTPS automático |
| `n8n` | n8n |

No servidor, use `COMPOSE_PROFILES=app,proxy,n8n`, `APP_ENV=production`,
`DOMAIN` com o seu domínio e `N8N_URL=https://n8n.SEU-DOMINIO`, `N8N_SECURE_COOKIE=true`.
Só o Caddy publica portas (80 e 443); banco e n8n ficam presos em 127.0.0.1.

## CI

`.github/workflows/ci.yml` roda a cada push na `main` e em pull requests, em três jobs: lint e testes
(com um Postgres de verdade e as migrações), Compose com a imagem da API em amd64, e build da imagem
em arm64 (a arquitetura da instância gratuita da Oracle). O Dependabot avisa quando as actions
ficam desatualizadas.

## Estrutura

```
app/
  ingestao/   extração por formato, seções, trechos, remoção, varredura de rastros
  consulta/   busca híbrida, reordenador, portão, serviço de consulta
  llm/        embeddings (fastembed), geradores (Ollama), prompts, perfis de modelo
  web/        interface (FastAPI + Jinja2 + HTMX)
  avaliacao/  conjunto de referência, métricas, experimentos
  routers/    API HTTP
migrations/   Alembic (0001–0008)
tests/        pytest, com Postgres de verdade
docs/         uma página por etapa
infra/        Caddy e scripts da primeira subida do banco
n8n/workflows fluxos exportados em JSON
scripts/      enviar.py, baixar_modelo.py, n8n.py, gen_env.py
```

## Testes

```bash
make test       # precisa de TEST_DATABASE_URL (um banco separado: os testes apagam as tabelas)
make lint
```

Sem `TEST_DATABASE_URL`, os testes que precisam de banco são pulados.

## Licenças dos modelos

Embeddings: `embeddinggemma-300m` sob os termos do Gemma; `multilingual-e5-large` MIT. O
`jina-embeddings-v3` não foi usado por ser não comercial. Confira a licença do modelo de geração
que você escolher.
