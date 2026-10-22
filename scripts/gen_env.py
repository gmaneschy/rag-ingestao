"""Cria o .env a partir do .env.example, com segredos aleatórios.

Uso: python scripts/gen_env.py
Não sobrescreve um .env existente.
"""

import re
import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SEGREDOS = ("POSTGRES_PASSWORD", "API_KEY", "N8N_DB_PASSWORD", "N8N_ENCRYPTION_KEY")


def valor_de(linhas: list[str], chave: str) -> str:
    for linha in linhas:
        m = re.match(rf"^{chave}=(\S*)", linha)
        if m:
            return m.group(1)
    return ""


def gerar(exemplo: str) -> str:
    linhas = exemplo.splitlines()
    novo: dict[str, str] = {}

    for chave in SEGREDOS:
        if re.search(rf"^{chave}=", exemplo, re.M):
            # token_urlsafe só usa letras, números, "-" e "_": seguro em URLs e no Compose.
            novo[chave] = secrets.token_urlsafe(32)

    usuario = valor_de(linhas, "POSTGRES_USER") or "app"
    banco = valor_de(linhas, "POSTGRES_DB")
    porta = valor_de(linhas, "DB_PORT") or "5432"
    novo["DATABASE_URL"] = (
        f"postgresql+psycopg://{usuario}:{novo['POSTGRES_PASSWORD']}@localhost:{porta}/{banco}"
    )

    saida = []
    for linha in linhas:
        m = re.match(r"^([A-Z0-9_]+)=(\s*)(#.*)?$", linha)
        if m and m.group(1) in novo:
            comentario = f"  {m.group(3)}" if m.group(3) else ""
            linha = f"{m.group(1)}={novo[m.group(1)]}{comentario}"
        saida.append(linha)
    return "\n".join(saida) + "\n"


def main() -> int:
    destino = RAIZ / ".env"
    if destino.exists():
        print(".env já existe; nada foi alterado.", file=sys.stderr)
        return 1
    destino.write_text(gerar((RAIZ / ".env.example").read_text(encoding="utf-8")), encoding="utf-8")
    destino.chmod(0o600)
    print(".env criado (permissão 600). Se usar n8n, guarde uma cópia da N8N_ENCRYPTION_KEY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
