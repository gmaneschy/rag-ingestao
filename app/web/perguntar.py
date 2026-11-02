"""Telas de Perguntar e do detalhe de uma consulta."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import configuracao
from app.consulta import servico
from app.consulta.busca import ReindexacaoNecessaria
from app.consulta.config import ConsultaSettings
from app.consulta.reranker import Reordenador
from app.db import get_session
from app.llm.config import LLMSettings
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import ModeloIndisponivel
from app.routers import perguntas as api
from app.routers.documentos import obter_embeddings
from app.web import sessao
from app.web.telas import acao, pagina

protegido = APIRouter(
    prefix="/app", include_in_schema=False, dependencies=[Depends(sessao.exigir_sessao)]
)
Sessao = Annotated[Session, Depends(get_session)]
TIPOS_EXTRAS = [
    ("notas", "notas"),
    ("bibliografia", "bibliografia"),
    ("indice", "índice"),
    ("copyright", "copyright / ISBN"),
    ("fragmento", "fragmentos"),
]


def _colecoes_e_documentos(session: Session) -> tuple[list[str], list[dict[str, object]]]:
    colecoes = list(
        session.execute(
            text("SELECT DISTINCT colecao FROM documento WHERE estado = 'processado' ORDER BY 1")
        ).scalars()
    )
    docs = [
        dict(d)
        for d in session.execute(
            text(
                "SELECT id, nome, colecao FROM documento WHERE estado = 'processado' "
                "ORDER BY colecao, nome"
            )
        ).mappings()
    ]
    return colecoes, docs


@protegido.get("/perguntar", response_class=HTMLResponse)
def tela_de_perguntar(
    request: Request,
    session: Sessao,
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
) -> HTMLResponse:
    colecoes, docs = _colecoes_e_documentos(session)
    return pagina(
        request,
        "perguntar.html",
        ativa="perguntar",
        colecoes=colecoes,
        docs=docs,
        k_padrao=consulta.consulta_k,
        k_analise=consulta.consulta_k_analise,
        tipos_extras=TIPOS_EXTRAS,
    )


@protegido.post("/perguntar", response_class=HTMLResponse, dependencies=acao)
def responder(
    request: Request,
    session: Sessao,
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(api.obter_gerador)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(api.obter_reordenador)],
    pergunta: Annotated[str, Form()],
    modo: Annotated[Literal["hibrida", "vetorial", "textual"], Form()] = "hibrida",
    tipo: Annotated[Literal["factual", "analise"], Form()] = "factual",
    k: Annotated[int | None, Form(ge=1, le=10)] = None,
    colecao: Annotated[list[str] | None, Form()] = None,
    documento: Annotated[list[int] | None, Form()] = None,
    incluir: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse:
    pergunta = pergunta.strip()
    if len(pergunta) < 3:
        return pagina(request, "parciais/resposta.html", erro="Escreva uma pergunta.")
    tipos_validos = {t for t, _ in TIPOS_EXTRAS}
    try:
        resultado = servico.perguntar(
            session,
            pergunta[:2000],
            embeddings=embeddings,
            gerador=gerador,
            llm=llm,
            consulta=consulta,
            k=k,
            colecoes=[c for c in colecao or [] if c] or None,
            modo=modo,
            tipo=tipo,
            documento_ids=documento or None,
            incluir_tipos=[t for t in incluir or [] if t in tipos_validos] or None,
            reordenador=reordenador,
        )
    except ReindexacaoNecessaria as erro:
        mensagem = (
            "Há documentos indexados com outro modelo de embeddings "
            f"({', '.join(erro.documentos)}). Reprocesse-os antes de consultar."
        )
        return pagina(request, "parciais/resposta.html", erro=mensagem)
    except servico.LimiteDiarioExcedido as erro:
        return pagina(request, "parciais/resposta.html", erro=str(erro))
    except ModeloIndisponivel as erro:
        return pagina(request, "parciais/resposta.html", erro=f"Modelo indisponível: {erro}")
    return pagina(request, "parciais/resposta.html", r=resultado, erro=None, ajudou=None)


@protegido.post(
    "/consultas/{consulta_id}/avaliacao", response_class=HTMLResponse, dependencies=acao
)
def avaliar(
    request: Request, consulta_id: int, session: Sessao, ajudou: Annotated[bool, Form()]
) -> HTMLResponse:
    achou = session.execute(
        text("UPDATE consulta SET ajudou = :a WHERE id = :id RETURNING id"),
        {"a": ajudou, "id": consulta_id},
    ).first()
    session.commit()
    if achou is None:
        raise HTTPException(404, "Consulta não encontrada")
    return pagina(request, "parciais/avaliacao.html", cid=consulta_id, ajudou=ajudou)


@protegido.get("/consultas/{consulta_id}", response_class=HTMLResponse)
def detalhe_da_consulta(request: Request, consulta_id: int, session: Sessao) -> HTMLResponse:
    linha = (
        session.execute(
            text(
                "SELECT id, pergunta, resposta, fontes, modelo_geracao, tokens_entrada, "
                "tokens_saida, latencia_ms, custo, ajudou, tipo, criada_em "
                "FROM consulta WHERE id = :id"
            ),
            {"id": consulta_id},
        )
        .mappings()
        .first()
    )
    if linha is None:
        return pagina(request, "nao_encontrado.html", ativa="perguntar", o_que="consulta")
    return pagina(request, "consulta.html", ativa="perguntar", c=dict(linha))
