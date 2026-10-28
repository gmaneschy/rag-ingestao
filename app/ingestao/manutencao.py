"""Tarefas da subida da aplicação: repetir limpezas e retomar a ingestão interrompida.

O processamento roda em segundo plano dentro do próprio processo (BackgroundTasks); se o servidor
reinicia no meio, o documento fica 'pendente' para sempre. Aqui ele é retomado.
Com vários processos (workers) cada um retomaria os mesmos documentos: a troca de trechos é
atômica e confere o hash, então o resultado é correto, só há trabalho repetido.
"""

import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.ingestao import remocao, servico
from app.ingestao.config import IngestaoSettings
from app.llm.protocolos import GeradorDeEmbeddings

logger = logging.getLogger("app.ingestao")


def retomar_pendentes(
    fabrica: sessionmaker[Session], embeddings: GeradorDeEmbeddings, config: IngestaoSettings
) -> int:
    """Processa, um por vez, os documentos que ficaram 'pendente'. Devolve quantos tentou."""
    with fabrica() as session:
        ids = [
            linha[0]
            for linha in session.execute(
                text("SELECT id FROM documento WHERE estado = 'pendente' ORDER BY id")
            )
        ]
    for documento_id in ids:
        servico.processar(fabrica, documento_id, embeddings, config)
    return len(ids)


def executar_na_subida(
    fabrica: sessionmaker[Session],
    obter_embeddings: Callable[[], GeradorDeEmbeddings],
    config: IngestaoSettings,
) -> None:
    """Roda em uma thread própria, para não atrasar a subida (o modelo pode demorar a carregar).
    Nunca levanta: banco fora do ar na subida não pode derrubar a aplicação."""
    try:
        limpezas = remocao.limpar_pendentes(fabrica, config)
        if limpezas:
            logger.info("limpezas pendentes concluídas", extra={"quantidade": limpezas})
        with fabrica() as session:
            pendentes = session.execute(
                text("SELECT count(*) FROM documento WHERE estado = 'pendente'")
            ).scalar_one()
        if pendentes:
            logger.info("retomando documentos pendentes", extra={"quantidade": pendentes})
            retomar_pendentes(fabrica, obter_embeddings(), config)
    except Exception:
        logger.exception("falha nas tarefas da subida")
