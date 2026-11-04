"""Perguntas ao corpus: resposta com fontes (documento e página) ou recusa sem evidência."""

from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import configuracao
from app.consulta import servico
from app.consulta.busca import ReindexacaoNecessaria
from app.consulta.config import ConsultaSettings, get_consulta_settings
from app.consulta.reranker import Reordenador, criar_reordenador
from app.db import get_session
from app.ingestao.config import get_ingestao_settings
from app.llm.config import LLMSettings, get_llm_settings
from app.llm.fabrica import criar_gerador_de_resposta
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import ModeloIndisponivel
from app.routers.documentos import obter_embeddings
from app.security import exigir_chave

router = APIRouter(prefix="/perguntas", tags=["perguntas"], dependencies=[Depends(exigir_chave)])

Colecao = Annotated[str, StringConstraints(pattern=r"^[\w.-]+$", max_length=64)]
TipoDeTrecho = Literal["notas", "bibliografia", "indice", "copyright", "fragmento"]


@lru_cache
def obter_reordenador() -> Reordenador | None:
    return criar_reordenador(
        get_consulta_settings().reranker_model, get_ingestao_settings().modelos_dir
    )


_GERADORES: dict[tuple[object, ...], GeradorDeResposta] = {}


def obter_gerador(
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)] = None,  # type: ignore[assignment]
) -> GeradorDeResposta:
    """Um gerador por combinação de ajustes: mudar o modelo na tela não exige reiniciar."""
    if not isinstance(llm, LLMSettings):  # chamada direta, fora do FastAPI
        llm = get_llm_settings()
    chave = (
        llm.llm_provider,
        llm.llm_model,
        llm.llm_think,
        llm.llm_idioma,
        llm.llm_max_tokens_saida,
        llm.llm_max_tokens_analise,
        llm.max_tokens_por_pergunta,
    )
    if chave not in _GERADORES:
        _GERADORES[chave] = criar_gerador_de_resposta(llm)
    return _GERADORES[chave]


obter_gerador.cache_clear = _GERADORES.clear  # type: ignore[attr-defined]


class PerguntaIn(BaseModel):
    # Campo desconhecido é erro (422), não é ignorado em silêncio: `secao` e `pagina_*` saíram.
    model_config = ConfigDict(extra="forbid")

    pergunta: str = Field(min_length=3, max_length=2000)
    colecoes: list[Colecao] | None = Field(default=None, max_length=20)
    # Só consulta estes documentos (os ids vêm de GET /documentos). Vazio = todos.
    documento_ids: list[int] | None = Field(default=None, max_length=50)
    k: int | None = Field(default=None, ge=1, le=10)
    modo: Literal["hibrida", "vetorial", "textual"] = "hibrida"
    # "analise" também interpreta e opina, separando Evidências (citadas) de Avaliação.
    tipo: Literal["factual", "analise"] = "factual"
    # A busca usa só o corpo do texto. Para perguntar sobre notas, bibliografia, índice ou a
    # página de copyright ("qual o ISBN?"), inclua o tipo aqui.
    incluir_tipos: list[TipoDeTrecho] | None = Field(default=None, max_length=5)


class AvaliacaoIn(BaseModel):
    ajudou: bool


@router.post("")
def perguntar(
    corpo: PerguntaIn,
    session: Annotated[Session, Depends(get_session)],
    embeddings: Annotated[GeradorDeEmbeddings, Depends(obter_embeddings)],
    gerador: Annotated[GeradorDeResposta, Depends(obter_gerador)],
    llm: Annotated[LLMSettings, Depends(configuracao.llm_efetivo)],
    consulta: Annotated[ConsultaSettings, Depends(configuracao.consulta_efetiva)],
    reordenador: Annotated[Reordenador | None, Depends(obter_reordenador)],
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
            tipo=corpo.tipo,
            documento_ids=corpo.documento_ids,
            incluir_tipos=corpo.incluir_tipos,
            reordenador=reordenador,
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
        "tipo": resultado.tipo,
        "analise": resultado.analise,
        "fontes": resultado.fontes,
        "trechos_usados": resultado.trechos_usados,
        "modelo": resultado.modelo_geracao,
        "tokens_entrada": resultado.resposta.tokens_entrada,
        "tokens_saida": resultado.resposta.tokens_saida,
        "latencia_ms": resultado.latencia_ms,
        "diagnostico": resultado.diagnostico,
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
