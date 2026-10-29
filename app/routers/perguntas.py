"""Perguntas ao corpus: resposta com fontes (documento e página) ou recusa sem evidência."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.consulta import servico
from app.consulta.busca import ReindexacaoNecessaria
from app.consulta.config import ConsultaSettings, get_consulta_settings
from app.db import get_session
from app.llm.config import LLMSettings, get_llm_settings
from app.llm.fabrica import criar_gerador_de_resposta
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import ModeloIndisponivel
from app.routers.documentos import obter_embeddings
from app.security import exigir_chave

router = APIRouter(prefix="/perguntas", tags=["perguntas"], dependencies=[Depends(exigir_chave)])

Colecao = Annotated[str, StringConstraints(pattern=r"^[\w.-]+$", max_length=64)]


@lru_cache
def obter_gerador() -> GeradorDeResposta:
    return criar_gerador_de_resposta()


class PerguntaIn(BaseModel):
    pergunta: str = Field(min_length=3, max_length=2000)
    colecoes: list[Colecao] | None = Field(default=None, max_length=20)
    k: int | None = Field(default=None, ge=1, le=10)
    modo: Literal["hibrida", "vetorial", "textual"] = "hibrida"
    # Restringem a busca: seção cujo nome contém o texto, e faixa de páginas (inclusiva).
    secao: str | None = Field(default=None, min_length=1, max_length=120)
    pagina_de: int | None = Field(default=None, ge=1)
    pagina_ate: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validar_faixa(self) -> "PerguntaIn":
        if self.pagina_de and self.pagina_ate and self.pagina_de > self.pagina_ate:
            raise ValueError("pagina_de não pode ser maior que pagina_ate")
        return self


class AvaliacaoIn(BaseModel):
    ajudou: bool


@router.post("")
def perguntar(
    corpo: PerguntaIn,
    session: Annotated[Session, Depends(get_session)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(obter_gerador)],
    llm: Annotated[LLMSettings, Depends(get_llm_settings)],
    consulta: Annotated[ConsultaSettings, Depends(get_consulta_settings)],
) -> dict[str, object]:
    try:
        resultado = servico.perguntar(
            session,
            corpo.pergunta,
            embeddings=embeddings,
            gerador=gerador,
            llm=llm,
            consulta=consulta,
            k=corpo.k,
            colecoes=corpo.colecoes,
            modo=corpo.modo,
            secao=corpo.secao,
            pagina_de=corpo.pagina_de,
            pagina_ate=corpo.pagina_ate,
        )
    except ReindexacaoNecessaria as erro:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Há documentos indexados com outro modelo de embeddings "
            f"({', '.join(erro.documentos)}). Reprocese-os com "
            "POST /documentos/{id}/reprocessar antes de consultar.",
        ) from erro
    except servico.LimiteDiarioExcedido as erro:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(erro)) from erro
    except ModeloIndisponivel as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro

    return {
        "id": resultado.id,
        "resposta": resultado.resposta.texto,
        "recusou": resultado.resposta.recusou,
        "aviso": resultado.aviso,
        "fontes": resultado.fontes,
        "trechos_usados": resultado.trechos_usados,
        "modelo": resultado.modelo_geracao,
        "tokens_entrada": resultado.resposta.tokens_entrada,
        "tokens_saida": resultado.resposta.tokens_saida,
        "latencia_ms": resultado.latencia_ms,
    }


@router.post("/{consulta_id}/avaliacao")
def avaliar(
    consulta_id: int, corpo: AvaliacaoIn, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    achou = session.execute(
        text("UPDATE consulta SET ajudou = :a WHERE id = :id RETURNING id"),
        {"a": corpo.ajudou, "id": consulta_id},
    ).first()
    session.commit()
    if achou is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consulta não encontrada")
    return {"id": consulta_id, "ajudou": corpo.ajudou}
