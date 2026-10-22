#!/bin/sh
# Cria o usuário e o database do n8n dentro do mesmo servidor Postgres da aplicação.
# Roda automaticamente na primeira subida do banco. Pode ser rodado de novo com segurança
# (`make n8n-db`), por exemplo se o n8n for ligado depois que o volume já existia.
set -eu

psql -v ON_ERROR_STOP=1 -v pw="$N8N_DB_PASSWORD" \
    --username "$POSTGRES_USER" --dbname postgres <<'SQL'
SELECT format('CREATE ROLE n8n LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'n8n') \gexec

SELECT 'CREATE DATABASE n8n OWNER n8n'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n') \gexec
SQL
