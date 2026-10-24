"""Envio de documentos e consulta do estado da ingestão.

Listagem, detalhe com amostra de trechos e remoção chegam na etapa 4.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session, get_sessionmaker
from app.ingestao import servico
from app.ingestao.config import IngestaoSettings, get_ingestao_settings
from app.llm.fabrica import criar_embeddings
from app.llm.protocolos import GeradorDeEmbeddings
from app.security import exigir_chave

router = APIRouter(prefix="/documentos", tags=["documentos"], dependencies=[Depends(exigir_chave)])


@lru_cache
def obter_embeddings() -> GeradorDeEmbeddings:
    """Uma instância só: o modelo local é carregado uma vez e reaproveitado."""
    return criar_embeddings()


def _ler_limitado(arquivo: UploadFile, limite: int) -> bytes:
    """Lê no máximo limite+1 bytes: um envio gigante é recusado sem ocupar a memória."""
    dados = arquivo.file.read(limite + 1)
    if len(dados) > limite:
        raise HTTPException(413, "Arquivo grande demais")
    return dados


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def enviar_documento(
    background: BackgroundTasks,
    arquivo: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    colecao: Annotated[str, Form(min_length=1, max_length=64, pattern=r"^[\w.-]+$")] = "geral",
) -> JSONResponse:
    conteudo = _ler_limitado(arquivo, config.tamanho_max_bytes)
    try:
        registro = servico.registrar(
            session,
            nome=arquivo.filename or "",
            conteudo=conteudo,
            colecao=colecao,
            config=config,
        )
    except servico.ArquivoInvalido as erro:
        raise HTTPException(422, str(erro)) from erro

    if registro.precisa_processar:
        background.add_task(
            servico.processar, get_sessionmaker(), registro.documento_id, embeddings, config
        )
    corpo = {
        "id": registro.documento_id,
        "situacao": registro.situacao,
        "estado": registro.estado,
    }
    codigo = 200 if registro.situacao == "duplicado" and not registro.precisa_processar else 202
    return JSONResponse(corpo, status_code=codigo)


@router.post("/{documento_id}/reprocessar", status_code=status.HTTP_202_ACCEPTED)
def reprocessar_documento(
    documento_id: int,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
) -> dict[str, object]:
    """Refaz extração, divisão e vetores a partir do arquivo guardado (útil ao mudar o modelo)."""
    if not servico.marcar_para_reprocessar(session, documento_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    background.add_task(servico.processar, get_sessionmaker(), documento_id, embeddings, config)
    return {"id": documento_id, "estado": "pendente"}


@router.get("/{documento_id}")
def ver_documento(
    documento_id: int, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    linha = (
        session.execute(
            text(
                "SELECT d.id, d.nome, d.colecao, d.versao, d.estado, d.mensagem_erro, "
                "d.criado_em, d.atualizado_em, count(t.id) AS trechos "
                "FROM documento d LEFT JOIN trecho t ON t.documento_id = d.id "
                "WHERE d.id = :id GROUP BY d.id"
            ),
            {"id": documento_id},
        )
        .mappings()
        .first()
    )
    if linha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    return dict(linha)
