"""Entrada na interface: a mesma chave da API, trocada por um cookie.

O navegador não manda o cabeçalho X-API-Key, então a pessoa digita a chave uma vez e recebe um
cookie. O cookie NÃO é a chave: é um HMAC dela, que não serve para chamar a API e que deixa de
valer se a chave mudar. HttpOnly e SameSite=Strict; as ações que alteram dados exigem o cabeçalho
HX-Request, que um formulário de outro site não consegue enviar.
"""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings

NOME_DO_COOKIE = "rag_sessao"
DURACAO_S = 7 * 24 * 3600
ENTRAR = "/app/entrar"


class PrecisaEntrar(Exception):
    """A pessoa não tem sessão válida; o tratador redireciona para a tela de entrada."""


def _chave(settings: Settings) -> bytes | None:
    return settings.api_key.get_secret_value().encode() if settings.api_key else None


def gerar_token(settings: Settings) -> str | None:
    chave = _chave(settings)
    return hmac.new(chave, b"rag-ui-v1", hashlib.sha256).hexdigest() if chave else None


def chave_confere(informada: str, settings: Settings) -> bool:
    esperada = _chave(settings)
    return bool(esperada) and secrets.compare_digest(informada.encode(), esperada or b"")


def exigir_sessao(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    esperado = gerar_token(settings)
    recebido = request.cookies.get(NOME_DO_COOKIE, "")
    if not esperado or not secrets.compare_digest(recebido.encode(), esperado.encode()):
        raise PrecisaEntrar


def exigir_htmx(request: Request) -> None:
    """Ações que alteram dados só valem se vierem do HTMX (defesa contra CSRF, junto com o cookie
    SameSite=Strict)."""
    if request.headers.get("hx-request") != "true":
        raise HTTPException(403, "Ação não permitida fora da interface")
