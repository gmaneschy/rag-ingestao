"""Verificação de saúde para o orquestrador (Compose, Caddy) e o monitoramento."""

import logging

from fastapi import APIRouter, Response, status

from app.db import check_database

logger = logging.getLogger(__name__)

router = APIRouter(tags=["saúde"])


@router.get("/saude")
def saude(response: Response) -> dict[str, str]:
    try:
        check_database()
        banco = "ok"
    except Exception:
        logger.warning("banco indisponível na verificação de saúde", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        banco = "falhou"
    return {"status": "ok" if banco == "ok" else "degradado", "banco": banco}
