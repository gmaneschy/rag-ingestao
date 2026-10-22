"""Implementações simuladas: determinísticas, sem rede e sem custo. Servem aos testes e à CI."""

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence

from app.llm.tipos import MENSAGEM_SEM_EVIDENCIA, Resposta, TrechoRecuperado

_PALAVRA = re.compile(r"\w+")

# Palavras sem conteúdo: sem isto, "a" ou "de" bastariam para o simulado achar evidência.
_VAZIAS = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas por para com e ou que qual "
    "quais quem como quando onde se ao aos e é foi ser sao são".split()
)


def _palavras(texto: str) -> list[str]:
    """Minúsculas e sem acento, para 'Atenção' e 'atencao' contarem como a mesma palavra."""
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return _PALAVRA.findall(sem_acento)


class EmbeddingsSimulado:
    """Vetores por 'hashing trick': cada palavra soma em uma posição fixa do vetor.

    Textos que compartilham palavras ficam próximos, então a busca por similaridade tem
    comportamento realista nos testes, sem baixar modelo algum.
    """

    nome_modelo = "simulado-hash-v1"

    def __init__(self, dimensao: int) -> None:
        self.dimensao = dimensao

    def _vetor(self, texto: str) -> list[float]:
        vetor = [0.0] * self.dimensao
        for palavra in _palavras(texto):
            resumo = hashlib.sha256(palavra.encode()).digest()
            posicao = int.from_bytes(resumo[:4], "big") % self.dimensao
            sinal = 1.0 if resumo[4] % 2 == 0 else -1.0
            vetor[posicao] += sinal
        norma = math.sqrt(sum(v * v for v in vetor))
        if norma == 0:
            return vetor
        return [v / norma for v in vetor]

    def gerar_trechos(self, textos: Sequence[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    def gerar_consulta(self, texto: str) -> list[float]:
        return self._vetor(texto)


class GeradorDeRespostaSimulado:
    """Responde com a primeira frase do trecho que mais compartilha palavras com a pergunta.

    Recusa quando nenhum trecho compartilha palavra alguma: assim dá para testar o caminho de
    recusa e o de citação de ponta a ponta.
    """

    nome_modelo = "simulado-extrativo-v1"

    def responder(self, pergunta: str, trechos: Sequence[TrechoRecuperado]) -> Resposta:
        palavras_da_pergunta = set(_palavras(pergunta)) - _VAZIAS
        melhor_posicao, melhor_pontos = 0, 0
        for posicao, trecho in enumerate(trechos, start=1):
            pontos = len(palavras_da_pergunta & (set(_palavras(trecho.texto)) - _VAZIAS))
            if pontos > melhor_pontos:
                melhor_posicao, melhor_pontos = posicao, pontos

        tokens_entrada = len(_palavras(pergunta)) + sum(len(_palavras(t.texto)) for t in trechos)
        if melhor_pontos == 0:
            return Resposta(
                texto=MENSAGEM_SEM_EVIDENCIA,
                recusou=True,
                tokens_entrada=tokens_entrada,
                tokens_saida=len(_palavras(MENSAGEM_SEM_EVIDENCIA)),
            )

        primeira_frase = re.split(r"(?<=[.!?])\s+", trechos[melhor_posicao - 1].texto.strip())[0]
        texto = f"{primeira_frase} [{melhor_posicao}]"
        return Resposta(
            texto=texto,
            fontes=(melhor_posicao,),
            tokens_entrada=tokens_entrada,
            tokens_saida=len(_palavras(texto)),
        )
