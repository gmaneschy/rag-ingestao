.PHONY: env install dev test lint fmt migrate revision up down logs ps n8n-db n8n-export n8n-import

env:
	python3 scripts/gen_env.py

install:
	uv sync

# API local com recarga automática; precisa do banco no ar (make up).
dev:
	uv run uvicorn app.main:app --reload

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

# Docker Compose (os profiles vêm do COMPOSE_PROFILES no .env)
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

# Cria o database do n8n num volume que já existia antes de o n8n ser ligado.
n8n-db:
	docker compose exec -T db sh /docker-entrypoint-initdb.d/01-n8n.sh

# Fluxos do n8n <-> n8n/workflows (versionados no git)
n8n-export:
	python3 scripts/n8n.py export

n8n-import:
	python3 scripts/n8n.py import
