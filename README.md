# RAG com ingestão de documentos

API FastAPI

Gerado a partir do template `template-api-n8n` (Copier).

## Requisitos

- Python 3.12+ e [uv](https://docs.astral.sh/uv/)
- Docker com Compose

## Começando (local)

```bash
make env        # cria o .env com segredos aleatórios
make install
make up         # sobe o banco e o n8n em segundo plano
make migrate
make test
make dev        # http://localhost:8000/saude
```

O n8n fica em http://localhost:5678. Ele usa um database próprio (`n8n`) no mesmo Postgres da
aplicação. Esse database é criado automaticamente na primeira subida do banco. Se o volume do
banco já existia antes de você ligar o n8n, rode `make n8n-db`.

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

## Sem `make` (Windows)

Cada alvo do `Makefile` é um atalho. Os equivalentes:

| make | comando |
|------|---------|
| `make env` | `uv run python scripts/gen_env.py` |
| `make install` | `uv sync` |
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f --tail=100` |
| `make migrate` | `uv run alembic upgrade head` |
| `make test` | `uv run pytest` |
| `make lint` | `uv run ruff check .` e `uv run ruff format --check .` |
| `make dev` | `uv run uvicorn app.main:app --reload` |
| `make n8n-export` | `uv run python scripts/n8n.py export` |
| `make n8n-import` | `uv run python scripts/n8n.py import` |

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

## Estrutura

```
app/                 # API (main, config, db, security, logs, routers, services)
migrations/          # Alembic
tests/
infra/
  caddy/Caddyfile    # proxy reverso e HTTPS
  postgres/init/     # scripts da primeira subida do banco
n8n/workflows/       # fluxos do n8n exportados em JSON
scripts/n8n.py       # exporta/importa os fluxos
scripts/gen_env.py   # gera o .env
compose.yaml
Dockerfile
```

## CI

`.github/workflows/ci.yml` roda a cada push na `main` e em pull requests, em três jobs: lint e testes
(com um Postgres de verdade e as migrações), Compose com a imagem da API em amd64, e build da imagem
em arm64 (a arquitetura da instância gratuita da Oracle). O Dependabot avisa quando as actions
ficam desatualizadas.

## Atualizando a partir do template

```bash
copier update
```
