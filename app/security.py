"""Chave de acesso simples, por cabeçalho X-API-Key.

Fecha por padrão: sem API_KEY configurada, nenhuma rota protegida abre.
Uso: `dependencies=[Depends(exigir_chave)]` na rota ou no router.
"""

import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

_cabecalho = APIKeyHeader(name="X-API-Key", auto_error=False)


def exigir_chave(
    chave: str | None = Security(_cabecalho),
    settings: Settings = Depends(get_settings),
) -> None:
    esperada = settings.api_key.get_secret_value() if settings.api_key else None
    if not esperada or not chave or not secrets.compare_digest(chave.encode(), esperada.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de acesso inválida ou ausente",
            headers={"WWW-Authenticate": "ApiKey"},
        )
