"""Ponto de entrada: `uvicorn app.main:app`."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.config import get_settings
from app.logging_config import configure_logging
from app.routers import documentos, saude

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # valida a configuração na subida; falha rápido se estiver errada
    configure_logging(settings.log_level)
    logger.info("aplicação iniciada", extra={"app_env": settings.app_env})
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
    return app


app = create_app()
