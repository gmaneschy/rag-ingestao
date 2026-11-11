"""Ponto de entrada: `uvicorn app.main:app`."""

import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import RequestResponseEndpoint

from app.config import get_settings
from app.consulta.config import get_consulta_settings
from app.db import get_sessionmaker
from app.ingestao import manutencao
from app.ingestao.config import get_ingestao_settings
from app.llm.config import get_llm_settings
from app.logging_config import configure_logging
from app.routers import documentos, perguntas, saude
from app.web import comparar, conversas, painel, perguntar, sessao, telas

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Valida TODA a configuração na subida: um valor errado (ex.: LIMIAR_SIMILARIDADE=0,25)
    # derruba a aplicação aqui, e não na primeira pergunta.
    settings = get_settings()
    ingestao = get_ingestao_settings()
    get_llm_settings()
    get_consulta_settings()
    configure_logging(settings.log_level)
    logger.info("aplicação iniciada", extra={"app_env": settings.app_env})
    if ingestao.retomar_pendentes:
        threading.Thread(
            target=manutencao.executar_na_subida,
            args=(get_sessionmaker(), documentos.obter_embeddings, ingestao),
            name="tarefas-da-subida",
            daemon=True,
        ).start()
    yield
    logger.info("aplicação encerrada")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        # Em produção, a documentação interativa fica desligada.
        docs_url=None if settings.em_producao else "/docs",
        redoc_url=None,
        openapi_url=None if settings.em_producao else "/openapi.json",
    )

    @app.middleware("http")
    async def registrar_requisicao(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        id_requisicao = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        inicio = time.perf_counter()
        resposta = await call_next(request)
        resposta.headers["X-Request-ID"] = id_requisicao
        logger.info(
            "requisição",
            extra={
                "id_requisicao": id_requisicao,
                "metodo": request.method,
                "caminho": request.url.path,
                "status": resposta.status_code,
                "duracao_ms": round((time.perf_counter() - inicio) * 1000, 1),
            },
        )
        return resposta

    app.include_router(saude.router)
    app.include_router(documentos.router)
    app.include_router(perguntas.router)
    app.include_router(telas.router)
    app.include_router(telas.protegido)
    app.include_router(perguntar.protegido)
    app.include_router(conversas.protegido)
    app.include_router(comparar.protegido)
    app.include_router(painel.protegido)
    app.mount("/app/static", StaticFiles(directory=telas.RAIZ / "static"), name="estaticos")

    @app.get("/", include_in_schema=False)
    def raiz() -> RedirectResponse:
        return RedirectResponse("/app/documentos", status_code=307)

    @app.exception_handler(sessao.PrecisaEntrar)
    async def sem_sessao(request: Request, _: sessao.PrecisaEntrar) -> Response:
        if request.headers.get("hx-request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": sessao.ENTRAR})
        return RedirectResponse(sessao.ENTRAR, status_code=303)

    @app.exception_handler(OperationalError)
    async def banco_indisponivel(_: Request, erro: OperationalError) -> JSONResponse:
        # Banco fora do ar ou sem resposta: 503 com mensagem clara, não um 500 genérico.
        logger.error("banco indisponível", extra={"erro": type(erro.orig).__name__})
        return JSONResponse({"detail": "Banco de dados indisponível"}, status_code=503)

    return app


app = create_app()
