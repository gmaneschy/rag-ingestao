"""Envia um arquivo à API, igual ao que o fluxo do n8n faz. Útil para testar sem o n8n.

Uso:
    uv run python scripts/enviar.py caminho/arquivo.pdf [--colecao geral] [--url http://127.0.0.1:8000]

A chave vem de API_KEY (variável de ambiente ou arquivo .env). Depois do envio, acompanha o estado
do documento até 'processado' ou 'falhou'.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent


def ler_chave() -> str:
    chave = os.environ.get("API_KEY", "")
    env = RAIZ / ".env"
    if not chave and env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            if linha.startswith("API_KEY="):
                chave = linha.partition("=")[2].split("#")[0].strip()
    return chave


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--colecao", default="geral")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    chave = ler_chave()
    if not chave:
        print("API_KEY não encontrada (variável de ambiente ou .env)", file=sys.stderr)
        return 2
    cabecalhos = {"X-API-Key": chave}
    with args.arquivo.open("rb") as f:
        resposta = httpx.post(
            f"{args.url}/documentos",
            files={"arquivo": (args.arquivo.name, f)},
            data={"colecao": args.colecao},
            headers=cabecalhos,
            timeout=60,
        )
    if resposta.status_code not in (200, 202):
        print(f"Recusado ({resposta.status_code}): {resposta.text}", file=sys.stderr)
        return 1
    corpo = resposta.json()
    print(f"documento {corpo['id']}: {corpo['situacao']}, estado {corpo['estado']}")

    for _ in range(120):
        detalhe = httpx.get(f"{args.url}/documentos/{corpo['id']}", headers=cabecalhos).json()
        if detalhe["estado"] != "pendente":
            print(f"estado final: {detalhe['estado']}, {detalhe['trechos']} trechos")
            if detalhe["mensagem_erro"]:
                print(f"motivo: {detalhe['mensagem_erro']}")
            return 0 if detalhe["estado"] == "processado" else 1
        time.sleep(1)
    print("ainda processando; consulte GET /documentos/{id} depois", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
