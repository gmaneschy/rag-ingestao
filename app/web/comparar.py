"""Histórico de consultas e comparação de modelos de geração."""

import logging
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Form, Query, Request
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
from app.llm.fabrica import criar_gerador_de_resposta
from app.llm.protocolos import GeradorDeEmbeddings
from app.llm.tipos import ModeloIndisponivel
from app.routers import perguntas as api
from app.routers.documentos import obter_embeddings
from app.web import sessao
from app.web.telas import acao, pagina

logger = logging.getLogger("app")
protegido = APIRouter(
    prefix="/app", include_in_schema=False, dependencies=[Depends(sessao.exigir_sessao)]
)
Sessao = Annotated[Session, Depends(get_session)]
POR_PAGINA = 25


# --- histórico ---------------------------------------------------------------------------------


@protegido.get("/historico", response_class=HTMLResponse)
def historico(
    request: Request,
    session: Sessao,
    q: Annotated[str, Query(max_length=200)] = "",
    tipo: Literal["", "factual", "analise"] = "",
    avaliacao: Literal["", "sim", "nao", "sem"] = "",
    situacao: Literal["", "sem_fontes", "fonte_removida"] = "",
    pagina_atual: Annotated[int, Query(alias="pagina", ge=1, le=10_000)] = 1,
) -> HTMLResponse:
    onde, params = ["true"], {}
    if q.strip():
        onde.append("pergunta ILIKE :q")
        params["q"] = f"%{q.strip()}%"
    if tipo:
        onde.append("tipo = :tipo")
        params["tipo"] = tipo
    if avaliacao:
        onde.append(
            {"sim": "ajudou IS true", "nao": "ajudou IS false", "sem": "ajudou IS NULL"}[avaliacao]
        )
    if situacao == "sem_fontes":
        onde.append("jsonb_array_length(fontes) = 0")
    elif situacao == "fonte_removida":
        onde.append("fontes @> '[{\"removido\": true}]'::jsonb")
    filtro = " AND ".join(onde)
    total = session.execute(
        text(f"SELECT count(*) FROM consulta WHERE {filtro}"), params
    ).scalar_one()
    itens = [
        dict(i)
        for i in session.execute(
            text(
                "SELECT id, pergunta, tipo, modelo_geracao, latencia_ms, ajudou, criada_em, "
                "jsonb_array_length(fontes) AS fontes, "
                "(SELECT count(*) FROM jsonb_array_elements(fontes) f "
                " WHERE f->>'removido' = 'true') AS removidas "
                f"FROM consulta WHERE {filtro} ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": POR_PAGINA, "off": (pagina_atual - 1) * POR_PAGINA},
        ).mappings()
    ]
    return pagina(
        request,
        "historico.html",
        ativa="historico",
        itens=itens,
        total=total,
        pagina=pagina_atual,
        paginas=max(1, -(-total // POR_PAGINA)),
        q=q,
        tipo=tipo,
        avaliacao=avaliacao,
        situacao=situacao,
    )


# --- comparar modelos --------------------------------------------------------------------------


def modelos_disponiveis(llm: LLMSettings) -> tuple[list[str], str | None]:
    """Modelos que o Ollama tem instalados (mais o configurado) e, se falhou, o motivo."""
    atual = llm.llm_model.strip()
    if llm.llm_provider != "ollama":
        return ([atual] if atual else ["simulado"]), None
    try:
        resposta = httpx.get(f"{llm.llm_base_url.rstrip('/')}/api/tags", timeout=3)
        resposta.raise_for_status()
        nomes = sorted(m["name"] for m in resposta.json().get("models", []))
    except (httpx.HTTPError, ValueError, KeyError) as erro:
        return ([atual] if atual else []), f"Não consegui listar os modelos do Ollama ({erro})."
    return sorted({*nomes, *([atual] if atual else [])}), None


@protegido.get("/comparar", response_class=HTMLResponse)
def tela_de_comparar(
    request: Request, llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)]
) -> HTMLResponse:
    modelos, aviso = modelos_disponiveis(llm)
    return pagina(
        request,
        "comparar.html",
        ativa="comparar",
        modelos=modelos,
        atual=llm.llm_model,
        aviso=aviso,
    )


@protegido.post("/comparar", response_class=HTMLResponse, dependencies=acao)
def montar_comparacao(
    request: Request,
    pergunta: Annotated[str, Form()],
    modelo: Annotated[list[str] | None, Form()] = None,
    modo: Annotated[Literal["hibrida", "vetorial", "textual"], Form()] = "hibrida",
    tipo: Annotated[Literal["factual", "analise"], Form()] = "factual",
    k: Annotated[int | None, Form(ge=1, le=10)] = None,
    pensar: Annotated[Literal["", "nao", "sim"], Form()] = "",
) -> HTMLResponse:
    escolhidos = list(dict.fromkeys(m.strip() for m in modelo or [] if m.strip()))[:6]
    if len(pergunta.strip()) < 3 or not escolhidos:
        return pagina(
            request,
            "parciais/comparacao.html",
            erro="Escreva a pergunta e marque ao menos um modelo.",
        )
    return pagina(
        request,
        "parciais/comparacao.html",
        erro=None,
        pergunta=pergunta.strip()[:2000],
        modelos=escolhidos,
        modo=modo,
        tipo=tipo,
        k=k or "",
        pensar=pensar,
    )


@protegido.post("/comparar/gerar", response_class=HTMLResponse, dependencies=acao)
def gerar_com_um_modelo(
    request: Request,
    session: Sessao,
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(api.obter_reordenador)],
    pergunta: Annotated[str, Form(max_length=2000)],
    modelo: Annotated[str, Form(max_length=120)],
    posicao: Annotated[int, Form()] = 0,
    modo: Annotated[Literal["hibrida", "vetorial", "textual"], Form()] = "hibrida",
    tipo: Annotated[Literal["factual", "analise"], Form()] = "factual",
    k: Annotated[str, Form()] = "",
    pensar: Annotated[Literal["", "nao", "sim"], Form()] = "",
) -> HTMLResponse:
    """Uma resposta, de um modelo. Não entra no histórico. Cada cartão chama a sua."""
    contexto = {"posicao": posicao, "modelo": modelo}
    ajustes: dict[str, object] = {"llm_model": modelo}
    if pensar:
        ajustes["llm_think"] = pensar == "sim"
    try:
        gerador = criar_gerador_de_resposta(llm.model_copy(update=ajustes))
        resultado = servico.perguntar(
            session,
            pergunta.strip(),
            embeddings=embeddings,
            gerador=gerador,
            llm=llm,
            consulta=consulta,
            k=int(k) if k.isdigit() and 1 <= int(k) <= 10 else None,
            modo=modo,
            tipo=tipo,
            reordenador=reordenador,
            registrar=False,
        )
    except ReindexacaoNecessaria:
        return pagina(
            request, "parciais/cartao.html", erro="Há documentos a reprocessar antes.", **contexto
        )
    except (servico.LimiteDiarioExcedido, ModeloIndisponivel) as erro:
        return pagina(request, "parciais/cartao.html", erro=str(erro), **contexto)
    return pagina(request, "parciais/cartao.html", erro=None, r=resultado, **contexto)
