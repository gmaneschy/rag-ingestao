"""Envio, listagem, detalhe e remoção de documentos."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session, get_sessionmaker
from app.ingestao import remocao, servico
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


@router.get("")
def listar_documentos(
    session: Annotated[Session, Depends(get_session)],
    colecao: Annotated[str | None, Query(max_length=64)] = None,
    estado: Annotated[Literal["pendente", "processado", "falhou"] | None, Query()] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    deslocamento: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """Documentos do mais recentemente atualizado ao mais antigo, com filtros opcionais."""
    filtros = {"colecao": colecao, "estado": estado}
    onde = (
        "WHERE (CAST(:colecao AS text) IS NULL OR d.colecao = :colecao) "
        "AND (CAST(:estado AS text) IS NULL OR d.estado = :estado)"
    )
    total = session.execute(text(f"SELECT count(*) FROM documento d {onde}"), filtros).scalar_one()
    itens = (
        session.execute(
            text(
                "SELECT d.id, d.nome, d.colecao, d.versao, d.estado, d.mensagem_erro, "
                "d.aviso, d.criado_em, d.atualizado_em, "
                "(SELECT count(*) FROM trecho t WHERE t.documento_id = d.id) AS trechos "
                f"FROM documento d {onde} "
                "ORDER BY d.atualizado_em DESC, d.id DESC LIMIT :limite OFFSET :deslocamento"
            ),
            {**filtros, "limite": limite, "deslocamento": deslocamento},
        )
        .mappings()
        .all()
    )
    return {"total": total, "itens": [dict(i) for i in itens]}


@router.get("/{documento_id}")
def ver_documento(
    documento_id: int,
    session: Annotated[Session, Depends(get_session)],
    amostra: Annotated[int, Query(ge=0, le=20, description="Trechos de amostra")] = 0,
) -> dict[str, object]:
    """Estado do documento; com `amostra=N`, N trechos espalhados do início ao fim."""
    linha = (
        session.execute(
            text(
                "SELECT d.id, d.nome, d.colecao, d.versao, d.estado, d.mensagem_erro, d.aviso, "
                "d.criado_em, d.atualizado_em, count(t.id) AS trechos, "
                "count(DISTINCT t.secao) AS secoes, "
                "min(t.modelo_embedding) AS modelo_embedding "
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
    detalhe: dict[str, object] = dict(linha)
    if amostra:
        total = int(linha["trechos"])
        n = min(amostra, total)
        indices = [i * total // n for i in range(n)]
        detalhe["amostra"] = [
            dict(t)
            for t in session.execute(
                text(
                    "SELECT posicao, pagina, secao, texto FROM ("
                    "SELECT posicao, pagina, secao, texto, "
                    "row_number() OVER (ORDER BY posicao) - 1 AS ordem "
                    "FROM trecho WHERE documento_id = :id) x "
                    "WHERE ordem = ANY(:indices) ORDER BY posicao"
                ),
                {"id": documento_id, "indices": indices},
            ).mappings()
        ]
    return detalhe


@router.get("/{documento_id}/secoes")
def ver_secoes(
    documento_id: int, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    """As seções detectadas, na ordem do documento, com quantos trechos e quais páginas."""
    if not session.execute(
        text("SELECT 1 FROM documento WHERE id = :id"), {"id": documento_id}
    ).first():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    linhas = session.execute(
        text(
            "SELECT secao, count(*) AS trechos, min(pagina) AS pagina_inicial, "
            "max(pagina) AS pagina_final, min(posicao) AS primeira "
            "FROM trecho WHERE documento_id = :id AND secao IS NOT NULL "
            "GROUP BY secao ORDER BY primeira"
        ),
        {"id": documento_id},
    ).mappings()
    sem_secao = session.execute(
        text("SELECT count(*) FROM trecho WHERE documento_id = :id AND secao IS NULL"),
        {"id": documento_id},
    ).scalar_one()
    return {
        "id": documento_id,
        "secoes": [{k: v for k, v in linha.items() if k != "primeira"} for linha in linhas],
        "trechos_sem_secao": sem_secao,
    }


@router.delete("/{documento_id}")
def remover_documento(
    documento_id: int,
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[IngestaoSettings, Depends(get_ingestao_settings)],
    consultas: Annotated[
        remocao.ModoConsultas,
        Query(
            description="'redigir' apaga os trechos citados nas respostas antigas; "
            "'apagar' apaga as consultas inteiras"
        ),
    ] = "redigir",
) -> dict[str, object]:
    """Apaga o documento, os trechos, o arquivo e os trechos citados em respostas antigas."""
    resultado = remocao.remover(session, documento_id, consultas=consultas, config=config)
    if resultado is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado")
    return {
        "id": resultado.documento_id,
        "nome": resultado.nome,
        "colecao": resultado.colecao,
        "trechos_removidos": resultado.trechos_removidos,
        "consultas_redigidas": resultado.consultas_redigidas,
        "consultas_apagadas": resultado.consultas_apagadas,
        "arquivo_removido": resultado.arquivo_removido,
    }
