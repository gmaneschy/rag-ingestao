"""Orquestra a pergunta: limite de gasto, compatibilidade, busca, resposta e registro."""

import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.consulta import busca
from app.consulta.busca import Achado, Modo
from app.consulta.config import ConsultaSettings
from app.llm.config import LLMSettings
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA, Resposta

logger = logging.getLogger("app.consulta")


class LimiteDiarioExcedido(Exception):
    """O gasto do dia chegou ao teto configurado (LIMITE_DIARIO_GASTO)."""


@dataclass(frozen=True)
class Resultado:
    id: int
    resposta: Resposta
    fontes: list[dict[str, object]]
    trechos_usados: list[dict[str, object]]
    modelo_geracao: str
    latencia_ms: int
    aviso: str | None


def _como_dict(numero: int, achado: Achado) -> dict[str, object]:
    return {
        "n": numero,
        "trecho_id": achado.trecho.id,
        "documento_id": achado.documento_id,
        "documento": achado.trecho.documento,
        "pagina": achado.trecho.pagina,
        "texto": achado.trecho.texto,
    }


def gasto_de_hoje(session: Session) -> float:
    return float(
        session.execute(
            text(
                "SELECT coalesce(sum(custo), 0) FROM consulta "
                "WHERE criada_em >= date_trunc('day', now())"
            )
        ).scalar_one()
    )


def perguntar(
    session: Session,
    pergunta: str,
    *,
    embeddings: GeradorDeEmbeddings,
    gerador: GeradorDeResposta,
    llm: LLMSettings,
    consulta: ConsultaSettings,
    k: int | None = None,
    colecoes: list[str] | None = None,
    modo: Modo = "hibrida",
) -> Resultado:
    inicio = time.perf_counter()
    if llm.limite_diario_gasto and gasto_de_hoje(session) >= llm.limite_diario_gasto:
        raise LimiteDiarioExcedido(f"Limite diário de gasto atingido ({llm.limite_diario_gasto})")

    busca.verificar_modelo(session, embeddings.nome_modelo, embeddings.dimensao, colecoes)
    vetor = embeddings.gerar_consulta(pergunta) if modo != "textual" else None
    achados = busca.buscar(
        session,
        pergunta,
        vetor,
        modo=modo,
        k=k or consulta.consulta_k,
        candidatos=consulta.consulta_candidatos,
        rrf_k=consulta.consulta_rrf_k,
        colecoes=colecoes,
    )

    if busca.tem_evidencia(achados, consulta.limiar_similaridade):
        resposta = gerador.responder(pergunta, [a.trecho for a in achados])
    else:  # sem evidência: nem chama o modelo (mais barato e sem risco de alucinar)
        resposta = Resposta(texto=MENSAGEM_SEM_EVIDENCIA, recusou=True)

    fontes = [_como_dict(n, achados[n - 1]) for n in resposta.fontes if n <= len(achados)]
    aviso = None
    if not resposta.recusou and not fontes:
        aviso = "A resposta não citou nenhum trecho; trate-a com cautela."
    latencia = round((time.perf_counter() - inicio) * 1000)

    consulta_id = session.execute(
        text(
            "INSERT INTO consulta (pergunta, resposta, fontes, modelo_geracao, tokens_entrada, "
            "tokens_saida, latencia_ms, custo) VALUES (:p, :r, CAST(:f AS jsonb), :m, :ti, :to, "
            ":l, :c) RETURNING id"
        ),
        {
            "p": pergunta,
            "r": resposta.texto,
            "f": json.dumps(fontes, ensure_ascii=False),
            "m": getattr(gerador, "nome_modelo", None),
            "ti": resposta.tokens_entrada,
            "to": resposta.tokens_saida,
            "l": latencia,
            "c": resposta.custo,
        },
    ).scalar_one()
    session.commit()
    logger.info(
        "pergunta respondida",
        extra={
            "consulta_id": consulta_id,
            "modo": modo,
            "trechos": len(achados),
            "recusou": resposta.recusou,
            "fontes": len(fontes),
            "latencia_ms": latencia,
        },
    )
    return Resultado(
        id=consulta_id,
        resposta=resposta,
        fontes=fontes,
        trechos_usados=[_como_dict(n, a) for n, a in enumerate(achados, start=1)],
        modelo_geracao=getattr(gerador, "nome_modelo", ""),
        latencia_ms=latencia,
        aviso=aviso,
    )
