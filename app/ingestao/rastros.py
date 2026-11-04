"""Procura de rastros: depois de remover um documento, onde ainda aparece um trecho do texto?

É a prova do requisito "remover sem deixar rastro": varre todas as tabelas (linha inteira, como
texto: colunas, JSON e vetores), os arquivos em disco e os logs guardados em memória. O resultado
é uma lista de lugares onde o termo ainda existe; vazia significa que não sobrou nada.

Limite: não olha os blocos físicos do Postgres (linhas mortas até o próximo VACUUM) nem backups.
"""

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.logging_config import MEMORIA


def _tabelas(session: Session) -> list[str]:
    return list(
        session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        ).scalars()
    )


def procurar_rastros(session: Session, termo: str, pasta_de_arquivos: Path) -> list[str]:
    """Lugares onde `termo` (sem diferenciar maiúsculas) ainda aparece. Vazio = nenhum rastro."""
    termo = termo.strip()
    if len(termo) < 4:
        raise ValueError("use um termo de pelo menos 4 caracteres, para não achar tudo")
    achados: list[str] = []
    padrao = f"%{termo.replace(chr(92), chr(92) * 2).replace('%', chr(92) + '%')}%"
    for tabela in _tabelas(session):
        n = session.execute(
            text(f'SELECT count(*) FROM "{tabela}" t WHERE t::text ILIKE :p'), {"p": padrao}
        ).scalar_one()
        if n:
            achados.append(f"tabela {tabela}: {n} linha(s)")

    minusculo = termo.lower()
    bruto = minusculo.encode()
    if pasta_de_arquivos.exists():
        for caminho in sorted(pasta_de_arquivos.rglob("*")):
            if minusculo in str(caminho.name).lower():
                achados.append(f"nome de arquivo: {caminho}")
            elif caminho.is_file() and bruto in caminho.read_bytes().lower():
                achados.append(f"conteúdo do arquivo: {caminho}")

    for evento in MEMORIA.eventos:
        if minusculo in json.dumps(evento, ensure_ascii=False).lower():
            achados.append(f"log em memória: {evento.get('message', evento.get('mensagem', ''))}")
    return achados
