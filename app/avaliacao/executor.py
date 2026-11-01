"""Roda o conjunto de referência contra o corpus já ingerido e junta o que há para medir.

Não grava nada: a busca e a geração são chamadas diretamente, sem passar pela tabela `consulta`,
para que a avaliação não suje o histórico de perguntas.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.avaliacao import metricas
from app.avaliacao.conjunto import Caso
from app.consulta import busca, reranker
from app.consulta.busca import Achado, Modo
from app.consulta.reranker import Reordenador
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA
from app.llm.verificacao import nomes_sem_suporte


@dataclass
class ResultadoCaso:
    caso: Caso
    posicao: int | None  # do primeiro trecho correto entre os recuperados
    melhor_similaridade: float | None
    ha_lexical: bool  # alguma palavra da pergunta apareceu em algum trecho recuperado
    tem_evidencia: bool  # o que o sistema decidiu, com o limiar usado
    latencia_busca_ms: float
    melhor_nota_reranker: float | None = None  # nota do primeiro colocado após reordenar
    topo: list[dict[str, Any]] = field(default_factory=list)
    resposta: dict[str, Any] | None = None


@dataclass
class RelatorioModo:
    rotulo: str
    modo: str
    k: int
    limiar: float
    casos: list[ResultadoCaso]
    modelo_geracao: str | None = None
    nota_minima: float | None = None  # portão pela nota do reordenador (None = similaridade)
    reordenou: bool = False


def _resumo(a: Achado, caso: Caso) -> dict[str, Any]:
    return {
        "documento": a.trecho.documento,
        "pagina": a.trecho.pagina,
        "secao": a.secao,
        "similaridade": None if a.similaridade is None else round(a.similaridade, 4),
        "nota_reranker": None if a.nota_reranker is None else round(a.nota_reranker, 4),
        "correto": metricas.acerta(caso.suporte, a.trecho),
        "texto": a.trecho.texto[:200],
    }


def _gerar(
    gerador: GeradorDeResposta,
    caso: Caso,
    achados: list[Achado],
    tem_evidencia: bool,
    k_resposta: int,
) -> dict[str, Any]:
    usados = achados[:k_resposta]
    inicio = time.perf_counter()
    if not tem_evidencia:  # igual à consulta real: sem evidência nem chama o modelo
        resposta_texto, recusou, fontes = MENSAGEM_SEM_EVIDENCIA, True, ()
        entrada = saida = 0
    else:
        resposta = gerador.responder(caso.pergunta, [a.trecho for a in usados])
        resposta_texto, recusou, fontes = resposta.texto, resposta.recusou, resposta.fontes
        entrada, saida = resposta.tokens_entrada, resposta.tokens_saida
    latencia = (time.perf_counter() - inicio) * 1000
    validas = [n for n in fontes if 1 <= n <= len(usados)]
    citou_correto = any(metricas.acerta(caso.suporte, usados[n - 1].trecho) for n in validas)
    nomes = (
        []
        if recusou or not validas
        else nomes_sem_suporte(resposta_texto, [a.trecho.texto for a in usados])
    )
    return {
        "texto": resposta_texto,
        "recusou": recusou,
        "fontes": list(validas),
        "citou_trecho_correto": citou_correto,
        "nomes_sem_suporte": nomes,
        "tokens_entrada": entrada,
        "tokens_saida": saida,
        "latencia_geracao_ms": round(latencia),
    }


def avaliar(
    session: Session,
    embeddings: GeradorDeEmbeddings,
    casos: list[Caso],
    *,
    rotulo: str,
    modo: Modo = "hibrida",
    k: int = 10,
    k_resposta: int = 5,
    candidatos: int = 20,
    rrf_k: int = 60,
    limiar: float = 0.25,
    colecoes: list[str] | None = None,
    gerador: GeradorDeResposta | None = None,
    reordenador: Reordenador | None = None,
    candidatos_reranker: int = 20,
    nota_minima: float | None = None,
) -> RelatorioModo:
    """Recupera k trechos por pergunta; com `gerador`, também gera as respostas (usa k_resposta)."""
    resultados: list[ResultadoCaso] = []
    for caso in casos:
        inicio = time.perf_counter()
        vetor = embeddings.gerar_consulta(caso.pergunta) if modo != "textual" else None
        achados = busca.buscar(
            session,
            caso.pergunta,
            vetor,
            modo=modo,
            k=max(k, candidatos_reranker) if reordenador else k,
            candidatos=max(candidatos, k, candidatos_reranker if reordenador else 0),
            rrf_k=rrf_k,
            colecoes=caso.colecoes or colecoes,
        )
        if reordenador and achados:
            achados = reranker.reordenar(caso.pergunta, achados, reordenador)
        achados = achados[:k]
        latencia_busca = (time.perf_counter() - inicio) * 1000
        gate = achados[:k_resposta]  # como na consulta real: o portão olha o que iria ao modelo
        if reordenador and nota_minima is not None:
            evidencia = reranker.tem_evidencia(achados, nota_minima)
        else:
            evidencia = busca.tem_evidencia(gate, limiar)
        similaridades = [a.similaridade for a in gate if a.similaridade is not None]
        resultado = ResultadoCaso(
            caso=caso,
            posicao=metricas.primeiro_acerto(caso.suporte, [a.trecho for a in achados])
            if caso.responde
            else None,
            melhor_similaridade=max(similaridades) if similaridades else None,
            ha_lexical=any(a.rank_textual is not None for a in gate),
            tem_evidencia=evidencia,
            latencia_busca_ms=round(latencia_busca, 1),
            melhor_nota_reranker=achados[0].nota_reranker if achados else None,
            topo=[_resumo(a, caso) for a in achados[:5]],
        )
        if gerador is not None:
            resultado.resposta = _gerar(gerador, caso, achados, evidencia, k_resposta)
        resultados.append(resultado)
    return RelatorioModo(
        rotulo=rotulo,
        modo=modo,
        k=k,
        limiar=limiar,
        casos=resultados,
        modelo_geracao=getattr(gerador, "nome_modelo", None) if gerador else None,
        nota_minima=nota_minima if reordenador else None,
        reordenou=reordenador is not None,
    )
