"""Orquestra a pergunta: limite de gasto, compatibilidade, busca, resposta e registro."""

import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.consulta import busca, reranker
from app.consulta.busca import Achado, Modo
from app.consulta.config import ConsultaSettings
from app.llm.config import LLMSettings
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA, Resposta, TipoResposta
from app.llm.verificacao import nomes_sem_suporte

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
    tipo: TipoResposta = "factual"
    analise: dict[str, object] | None = None


def _como_dict(numero: int, achado: Achado) -> dict[str, object]:
    return {
        "n": numero,
        "trecho_id": achado.trecho.id,
        "documento_id": achado.documento_id,
        "documento": achado.trecho.documento,
        "pagina": achado.trecho.pagina,
        "secao": achado.secao,
        "texto": achado.trecho.texto,
    }


AVISO_RESPOSTA_SEM_CITACAO = "A resposta não citou nenhum trecho; trate-a com cautela."
AVISO_AVALIACAO = (
    "A Avaliação é interpretação do modelo, não afirmação dos documentos: só as Evidências "
    "são sustentadas pelos trechos citados."
)
AVISO_SEM_SEPARACAO = (
    "O modelo não separou Evidências de Avaliação; trate todo o texto como interpretação."
)
AVISO_NOMES = (
    "Nomes da resposta que não aparecem no trecho citado: {nomes}. Pode ser só tradução; "
    "confira a fonte."
)
AVISO_SEM_CITACAO = "As Evidências não citaram nenhum trecho; trate a análise com cautela."


def _descrever_analise(
    resposta: Resposta, fontes: list[dict[str, object]], achados: list[Achado]
) -> tuple[dict[str, object], str]:
    """As duas partes da análise, a base em que ela se apoia e o aviso de transparência.

    A base diz quanto do documento a análise viu: ela só enxerga os trechos recuperados, não o
    documento inteiro.
    """
    separou = resposta.avaliacao is not None
    paginas = sorted({a.trecho.pagina for a in achados if a.trecho.pagina is not None})
    analise: dict[str, object] = {
        "evidencias": resposta.evidencias,
        "avaliacao": resposta.avaliacao,
        "separou": separou,
        "base_trechos": len(achados),
        "base_paginas": paginas,
    }
    if not separou:
        aviso = AVISO_SEM_SEPARACAO
    elif not fontes:
        aviso = f"{AVISO_SEM_CITACAO} {AVISO_AVALIACAO}"
    else:
        aviso = AVISO_AVALIACAO
    return analise, aviso


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
    secao: str | None = None,
    pagina_de: int | None = None,
    pagina_ate: int | None = None,
    tipo: TipoResposta = "factual",
    documento_ids: list[int] | None = None,
    incluir_tipos: list[str] | None = None,
    reordenador: reranker.Reordenador | None = None,
) -> Resultado:
    inicio = time.perf_counter()
    if llm.limite_diario_gasto and gasto_de_hoje(session) >= llm.limite_diario_gasto:
        raise LimiteDiarioExcedido(f"Limite diário de gasto atingido ({llm.limite_diario_gasto})")

    busca.verificar_modelo(session, embeddings.nome_modelo, embeddings.dimensao, colecoes)
    vetor = embeddings.gerar_consulta(pergunta) if modo != "textual" else None
    k_final = k or (consulta.consulta_k_analise if tipo == "analise" else consulta.consulta_k)
    achados = busca.buscar(
        session,
        pergunta,
        vetor,
        modo=modo,
        k=max(k_final, consulta.reranker_candidatos) if reordenador else k_final,
        candidatos=max(consulta.consulta_candidatos, consulta.reranker_candidatos)
        if reordenador
        else consulta.consulta_candidatos,
        rrf_k=consulta.consulta_rrf_k,
        colecoes=colecoes,
        secao=secao,
        pagina_de=pagina_de,
        pagina_ate=pagina_ate,
        documento_ids=documento_ids,
        tipos=(*busca.TIPOS_PADRAO, *(incluir_tipos or [])),
    )
    if reordenador and achados:
        achados = reranker.reordenar(pergunta, achados, reordenador)[:k_final]

    if reordenador and consulta.reranker_nota_minima is not None:
        ha_evidencia = reranker.tem_evidencia(achados, consulta.reranker_nota_minima)
    else:
        ha_evidencia = busca.tem_evidencia(achados, consulta.limiar_similaridade)
    if ha_evidencia:
        trechos = [a.trecho for a in achados]
        # O tipo só vai ao gerador quando é a análise: os geradores do tipo factual não mudam.
        resposta = (
            gerador.responder(pergunta, trechos, tipo=tipo)
            if tipo == "analise"
            else gerador.responder(pergunta, trechos)
        )
    else:  # sem evidência: nem chama o modelo (mais barato e sem risco de alucinar)
        resposta = Resposta(texto=MENSAGEM_SEM_EVIDENCIA, recusou=True)

    fontes = [_como_dict(n, achados[n - 1]) for n in resposta.fontes if n <= len(achados)]
    aviso = None
    if not resposta.recusou and not fontes:
        aviso = AVISO_RESPOSTA_SEM_CITACAO
    elif not achados:
        aviso = busca.explicar_filtros(session, colecoes, secao)

    if not resposta.recusou and fontes:
        # Na análise só as Evidências são afirmações dos documentos; a Avaliação é opinião.
        verificavel = resposta.evidencias if tipo == "analise" and resposta.evidencias else None
        nomes = nomes_sem_suporte(
            verificavel if verificavel is not None else resposta.texto,
            [a.trecho.texto for a in achados],
        )
        if nomes:
            aviso = " ".join(a for a in (aviso, AVISO_NOMES.format(nomes=", ".join(nomes))) if a)

    analise = None
    if tipo == "analise" and not resposta.recusou:
        analise, aviso_analise = _descrever_analise(resposta, fontes, achados)
        if aviso == AVISO_RESPOSTA_SEM_CITACAO:
            aviso = None  # a análise já traz o aviso próprio, mais específico
        aviso = " ".join(a for a in (aviso_analise, aviso) if a) or None
    latencia = round((time.perf_counter() - inicio) * 1000)

    consulta_id = session.execute(
        text(
            "INSERT INTO consulta (pergunta, resposta, fontes, modelo_geracao, tokens_entrada, "
            "tokens_saida, latencia_ms, custo, tipo) VALUES (:p, :r, CAST(:f AS jsonb), :m, "
            ":ti, :to, :l, :c, :tipo) RETURNING id"
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
            "tipo": tipo,
        },
    ).scalar_one()
    session.commit()
    logger.info(
        "pergunta respondida",
        extra={
            "consulta_id": consulta_id,
            "modo": modo,
            "tipo": tipo,
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
        tipo=tipo,
        analise=analise,
    )
