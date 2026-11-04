"""Telas de Configuração, Saúde e Logs."""

import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import configuracao
from app.consulta import servico
from app.consulta.config import get_consulta_settings
from app.db import get_session
from app.llm.config import get_llm_settings
from app.logging_config import MEMORIA
from app.routers import perguntas as api
from app.web import sessao
from app.web.comparar import modelos_disponiveis
from app.web.telas import acao, pagina

protegido = APIRouter(
    prefix="/app", include_in_schema=False, dependencies=[Depends(sessao.exigir_sessao)]
)
Sessao = Annotated[Session, Depends(get_session)]


# --- configuração ------------------------------------------------------------------------------


def _contexto_da_configuracao(
    session: Session, digitado: dict[str, str] | None = None, erros: list[str] | None = None
) -> dict[str, object]:
    guardado = configuracao.ler(session)
    base = {"consulta": get_consulta_settings(), "llm": get_llm_settings()}
    llm = get_llm_settings()
    modelos, aviso_modelos = modelos_disponiveis(llm)
    grupos: dict[str, list[dict[str, object]]] = {}
    for c in configuracao.CAMPOS:
        padrao = getattr(base[c.alvo], c.chave)
        grupos.setdefault(c.grupo, []).append(
            {
                "campo": c,
                "valor": (digitado or guardado).get(c.chave, ""),
                "no_banco": c.chave in guardado,
                "padrao": "—"
                if padrao in (None, "")
                else dict(c.opcoes).get(str(padrao), configuracao.texto_do_valor(c, padrao)),
            }
        )
    return {
        "grupos": grupos,
        "erros": erros or [],
        "modelos": modelos,
        "aviso_modelos": aviso_modelos,
        "fixos": [
            ("Provedor de geração", llm.llm_provider),
            ("Provedor de embeddings", llm.embeddings_provider),
            ("Modelo de embeddings", llm.embeddings_model),
            ("Servidor do modelo", llm.llm_base_url or "—"),
            ("Reordenador", get_consulta_settings().reranker_model or "desligado"),
        ],
    }


@protegido.get("/configuracao", response_class=HTMLResponse)
def tela_de_configuracao(request: Request, session: Sessao) -> HTMLResponse:
    return pagina(
        request,
        "configuracao.html",
        ativa="configuracao",
        salvo=False,
        **_contexto_da_configuracao(session),
    )


@protegido.post("/configuracao", response_class=HTMLResponse, dependencies=acao)
async def salvar_configuracao(request: Request, session: Sessao) -> HTMLResponse:
    formulario = await request.form()
    digitado = {c.chave: str(formulario.get(c.chave, "")) for c in configuracao.CAMPOS}
    erros = configuracao.salvar(session, digitado)
    return pagina(
        request,
        "parciais/configuracao_form.html",
        salvo=not erros,
        **_contexto_da_configuracao(session, digitado if erros else None, erros),
    )


# --- saúde -------------------------------------------------------------------------------------


def _um(session: Session, sql: str) -> object:
    return session.execute(text(sql)).scalar_one_or_none()


@protegido.get("/saude", response_class=HTMLResponse)
def tela_de_saude(request: Request, session: Sessao) -> HTMLResponse:
    llm = get_llm_settings()
    inicio = time.perf_counter()
    session.execute(text("SELECT 1"))
    banco_ms = round((time.perf_counter() - inicio) * 1000, 1)

    estados = dict(
        session.execute(text("SELECT estado, count(*) FROM documento GROUP BY estado")).all()
    )
    modelos_de_vetor = [
        r[0]
        for r in session.execute(
            text("SELECT DISTINCT modelo_embedding FROM trecho WHERE modelo_embedding IS NOT NULL")
        )
    ]
    ollama: dict[str, object] | None = None
    if llm.llm_provider == "ollama":
        modelos, motivo = modelos_disponiveis(llm)
        ollama = {
            "ok": motivo is None,
            "motivo": motivo,
            "modelos": modelos,
            "instalado": motivo is None and llm.llm_model in modelos,
        }
    consulta = get_consulta_settings()
    return pagina(
        request,
        "saude.html",
        ativa="saude",
        banco_ms=banco_ms,
        revisao=_um(session, "SELECT version_num FROM alembic_version"),
        pgvector=_um(session, "SELECT extversion FROM pg_extension WHERE extname = 'vector'"),
        estados=estados,
        trechos=_um(session, "SELECT count(*) FROM trecho"),
        modelos_de_vetor=modelos_de_vetor,
        modelo_atual=llm.embeddings_model,
        consultas=_um(session, "SELECT count(*) FROM consulta"),
        consultas_hoje=_um(
            session, "SELECT count(*) FROM consulta WHERE criada_em >= date_trunc('day', now())"
        ),
        gasto=servico.gasto_de_hoje(session),
        limite=llm.limite_diario_gasto,
        limpezas=_um(session, "SELECT count(*) FROM evento_documento WHERE limpeza_pendente"),
        ollama=ollama,
        provedor=llm.llm_provider,
        modelo_llm=llm.llm_model or "simulado",
        reranker=consulta.reranker_model,
        reranker_carregado=api.obter_reordenador.cache_info().currsize > 0,
    )


# --- logs --------------------------------------------------------------------------------------

NIVEIS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def _eventos(nivel: str, q: str, requisicoes: bool) -> list[dict[str, object]]:
    minimo = NIVEIS[nivel]
    saida = []
    for e in reversed(list(MEMORIA.eventos)):
        if NIVEIS.get(str(e.get("nivel")), 20) < minimo:
            continue
        if not requisicoes and e.get("msg") == "requisição":
            continue
        if q and q.lower() not in json.dumps(e, ensure_ascii=False).lower():
            continue
        extras = {k: v for k, v in e.items() if k not in ("ts", "nivel", "logger", "msg", "erro")}
        saida.append({**e, "extras": extras})
        if len(saida) >= 200:
            break
    return saida


@protegido.get("/logs", response_class=HTMLResponse)
def tela_de_logs(request: Request) -> HTMLResponse:
    return pagina(request, "logs.html", ativa="logs", **_logs_ctx("INFO", "", False))


def _logs_ctx(nivel: str, q: str, requisicoes: bool) -> dict[str, object]:
    return {
        "eventos": _eventos(nivel, q, requisicoes),
        "nivel": nivel,
        "q": q,
        "requisicoes": requisicoes,
        "guardados": len(MEMORIA.eventos),
    }


@protegido.get("/logs/lista", response_class=HTMLResponse)
def lista_de_logs(
    request: Request,
    nivel: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    q: Annotated[str, Query(max_length=100)] = "",
    requisicoes: bool = False,
) -> HTMLResponse:
    return pagina(request, "parciais/logs_lista.html", **_logs_ctx(nivel, q, requisicoes))
