"""Avaliação de uma resposta e detalhe de uma consulta (o chat mora em conversas.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.web import sessao
from app.web.telas import acao, pagina

protegido = APIRouter(
    prefix="/app", include_in_schema=False, dependencies=[Depends(sessao.exigir_sessao)]
)
Sessao = Annotated[Session, Depends(get_session)]


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
