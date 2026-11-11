"""Telas do chat: conversas na barra lateral, perguntas em segundo plano, parar e editar."""

import threading
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import configuracao
from app.consulta.config import ConsultaSettings
from app.consulta.reranker import Reordenador
from app.conversa import servico
from app.db import get_session, get_sessionmaker
from app.llm.config import LLMSettings
from app.llm.prompt import separar_analise
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
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


class Parametros:
    """Os campos do formulário que escolhem como a pergunta é feita. Ficam gravados na conversa."""

    def __init__(
        self,
        modo: Annotated[Literal["hibrida", "vetorial", "textual"], Form()] = "hibrida",
        tipo: Annotated[Literal["factual", "analise"], Form()] = "factual",
        k: Annotated[int | None, Form(ge=1, le=10)] = None,
        colecao: Annotated[list[str] | None, Form()] = None,
        documento: Annotated[list[int] | None, Form()] = None,
        incluir: Annotated[list[str] | None, Form()] = None,
    ) -> None:
        validos = {t for t, _ in TIPOS_EXTRAS}
        self.config: dict[str, object] = {
            "modo": modo,
            "tipo": tipo,
            "k": k,
            "colecoes": [c for c in colecao or [] if c],
            "documento_ids": documento or [],
            "incluir": [t for t in incluir or [] if t in validos],
        }


def _preparar(t: dict[str, object]) -> dict[str, object]:
    """Acrescenta o que o template precisa e que não vem do banco."""
    t = dict(t)
    t["fontes"] = t.get("fontes") or []
    if t["estado"] == "gerando":
        t["etapa"], t["parcial"] = servico.situacao(int(t["id"]))
    elif t.get("tipo") == "analise" and t.get("resposta"):
        evidencias, avaliacao = separar_analise(str(t["resposta"]))
        t["partes"] = [("Evidências", evidencias)] + (
            [("Avaliação", avaliacao)] if avaliacao else []
        )
    return t


def _config_da_conversa(conversa: dict[str, object] | None) -> dict[str, object]:
    return dict(conversa["config"]) if conversa and conversa.get("config") else {}


def _execucao(
    embeddings: GeradorDeEmbeddings,
    gerador: GeradorDeResposta,
    llm: LLMSettings,
    consulta: ConsultaSettings,
    reordenador: Reordenador | None,
) -> servico.Execucao:
    return servico.Execucao(embeddings, gerador, llm, consulta, reordenador)


def _disparar(
    background: BackgroundTasks,
    turno_id: int,
    geracao: int,
    pergunta: str,
    config: dict[str, object],
    execucao: servico.Execucao,
) -> None:
    acompanhamento = servico.iniciar(turno_id, geracao)
    background.add_task(
        servico.executar,
        get_sessionmaker(),
        turno_id,
        geracao,
        acompanhamento,
        pergunta,
        config,
        execucao,
    )


def _tela_do_turno(request: Request, session: Session, conversa_id: int, turno_id: int) -> Response:
    t = servico.turno(session, conversa_id, turno_id)
    if t is None:
        return Response(status_code=404)
    return pagina(request, "parciais/turno.html", t=_preparar(t))


@protegido.get("/perguntar", response_class=HTMLResponse)
def nova_conversa(
    request: Request,
    session: Sessao,
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
) -> HTMLResponse:
    return _tela(request, session, consulta, None)


@protegido.get("/conversas/{conversa_id}", response_class=HTMLResponse)
def abrir_conversa(
    request: Request,
    conversa_id: int,
    session: Sessao,
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
) -> HTMLResponse:
    conversa = servico.ver(session, conversa_id)
    if conversa is None:
        return pagina(request, "nao_encontrado.html", ativa="perguntar", o_que="conversa")
    return _tela(request, session, consulta, conversa)


def _tela(
    request: Request,
    session: Session,
    consulta: ConsultaSettings,
    conversa: dict[str, object] | None,
) -> HTMLResponse:
    colecoes, docs = _colecoes_e_documentos(session)
    turnos = (
        [_preparar(t) for t in servico.turnos(session, int(conversa["id"]))] if conversa else []
    )
    return pagina(
        request,
        "conversa.html",
        ativa="perguntar",
        conversa=conversa,
        conversa_ativa=conversa["id"] if conversa else None,
        turnos=turnos,
        cfg=_config_da_conversa(conversa),
        colecoes=colecoes,
        docs=docs,
        k_padrao=consulta.consulta_k,
        k_analise=consulta.consulta_k_analise,
        tipos_extras=TIPOS_EXTRAS,
    )


@protegido.post("/conversas", dependencies=acao)
def criar_conversa(
    request: Request,
    session: Sessao,
    background: BackgroundTasks,
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(api.obter_gerador)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(api.obter_reordenador)],
    params: Annotated[Parametros, Depends()],
    pergunta: Annotated[str, Form()],
) -> Response:
    pergunta = pergunta.strip()
    if len(pergunta) < 3:
        return pagina(request, "parciais/aviso.html", erro="Escreva uma pergunta.")
    conversa_id = servico.criar_conversa(session, pergunta, params.config)
    turno_id = servico.novo_turno(session, conversa_id, pergunta)
    _disparar(
        background,
        turno_id,
        1,
        pergunta,
        params.config,
        _execucao(embeddings, gerador, llm, consulta, reordenador),
    )
    return Response(status_code=200, headers={"HX-Redirect": f"/app/conversas/{conversa_id}"})


@protegido.post("/conversas/{conversa_id}/turnos", response_class=HTMLResponse, dependencies=acao)
def nova_pergunta(
    request: Request,
    conversa_id: int,
    session: Sessao,
    background: BackgroundTasks,
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(api.obter_gerador)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(api.obter_reordenador)],
    params: Annotated[Parametros, Depends()],
    pergunta: Annotated[str, Form()],
) -> Response:
    if servico.ver(session, conversa_id) is None:
        return Response(status_code=404)
    pergunta = pergunta.strip()
    if len(pergunta) < 3:
        return pagina(request, "parciais/aviso.html", erro="Escreva uma pergunta.")
    servico.salvar_config(session, conversa_id, params.config)
    turno_id = servico.novo_turno(session, conversa_id, pergunta)
    _disparar(
        background,
        turno_id,
        1,
        pergunta,
        params.config,
        _execucao(embeddings, gerador, llm, consulta, reordenador),
    )
    return _tela_do_turno(request, session, conversa_id, turno_id)


@protegido.get("/conversas/{conversa_id}/turnos/{turno_id}", response_class=HTMLResponse)
def ver_turno(request: Request, conversa_id: int, turno_id: int, session: Sessao) -> Response:
    return _tela_do_turno(request, session, conversa_id, turno_id)


@protegido.post(
    "/conversas/{conversa_id}/turnos/{turno_id}/cancelar",
    response_class=HTMLResponse,
    dependencies=acao,
)
def parar(request: Request, conversa_id: int, turno_id: int, session: Sessao) -> Response:
    servico.cancelar(session, conversa_id, turno_id)
    # Dá um instante para a thread notar o pedido; senão a tela mostra o turno ainda gerando
    # e o próximo ciclo da consulta repetida o corrige.
    for _ in range(20):
        t = servico.turno(session, conversa_id, turno_id)
        if t is None or t["estado"] != "gerando":
            break
        threading.Event().wait(0.1)
        session.rollback()
    return _tela_do_turno(request, session, conversa_id, turno_id)


@protegido.post(
    "/conversas/{conversa_id}/turnos/{turno_id}/refazer",
    response_class=HTMLResponse,
    dependencies=acao,
)
def refazer(
    request: Request,
    conversa_id: int,
    turno_id: int,
    session: Sessao,
    background: BackgroundTasks,
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(api.obter_gerador)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(api.obter_reordenador)],
    pergunta: Annotated[str | None, Form()] = None,
) -> Response:
    """Gera de novo o turno: 'tentar de novo' (sem texto) ou 'editar' (com a pergunta nova)."""
    conversa = servico.ver(session, conversa_id)
    novo_texto = pergunta.strip() if pergunta else None
    if novo_texto is not None and len(novo_texto) < 3:
        return pagina(request, "parciais/aviso.html", erro="Escreva uma pergunta.")
    geracao = (
        servico.reiniciar_turno(session, conversa_id, turno_id, novo_texto) if conversa else None
    )
    if geracao is None:
        return Response(status_code=404)
    atual = servico.turno(session, conversa_id, turno_id)
    _disparar(
        background,
        turno_id,
        geracao,
        str(atual["pergunta"]),
        _config_da_conversa(conversa),
        _execucao(embeddings, gerador, llm, consulta, reordenador),
    )
    return _tela_do_turno(request, session, conversa_id, turno_id)


@protegido.post("/conversas/{conversa_id}/config", dependencies=acao)
def gravar_config(
    conversa_id: int, session: Sessao, params: Annotated[Parametros, Depends()]
) -> Response:
    """Grava os parâmetros e documentos escolhidos, ao fechar um módulo."""
    if servico.ver(session, conversa_id) is None:
        return Response(status_code=404)
    servico.salvar_config(session, conversa_id, params.config)
    return Response(status_code=204)


@protegido.post("/conversas/{conversa_id}/renomear", dependencies=acao)
def renomear(conversa_id: int, session: Sessao, titulo: Annotated[str, Form()] = "") -> Response:
    if not titulo.strip():
        return Response(status_code=422)
    if not servico.renomear(session, conversa_id, titulo):
        return Response(status_code=404)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@protegido.delete("/conversas/{conversa_id}", dependencies=acao)
def apagar(conversa_id: int, session: Sessao) -> Response:
    servico.apagar(session, conversa_id)
    return Response(status_code=200, headers={"HX-Redirect": "/app/perguntar"})
