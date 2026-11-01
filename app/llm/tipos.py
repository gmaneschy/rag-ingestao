"""Tipos trocados entre o RAG e os provedores de embeddings e de resposta."""

from dataclasses import dataclass
from typing import Literal

# Dimensão da coluna trecho.vetor (migração 0002). Mudar exige nova migração e reindexação.
DIMENSAO_VETOR = 384

# "factual" responde só com o que os trechos dizem; "analise" também interpreta e opina, mas separa
# o que está nos trechos (Evidências) do que é do modelo (Avaliação).
TipoResposta = Literal["factual", "analise"]

MENSAGEM_SEM_EVIDENCIA = "Não encontrei evidência nos documentos fornecidos para responder."


class ModeloIndisponivel(RuntimeError):
    """O modelo não pôde ser carregado (download falhou, sem rede, limite de requisições)."""


@dataclass(frozen=True)
class TrechoRecuperado:
    """Um trecho que a busca devolveu e que pode ser citado na resposta."""

    id: int
    documento: str
    pagina: int | None
    texto: str
    secao: str | None = None


@dataclass(frozen=True)
class Resposta:
    """Resposta gerada, com o que é preciso para citar e para medir custo."""

    texto: str
    # Posições (começando em 1) dos trechos citados, na ordem em que foram enviados ao modelo.
    fontes: tuple[int, ...] = ()
    recusou: bool = False
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo: float = 0.0
    # Só no tipo "analise": o texto dividido nas duas partes (avaliacao é None se o modelo
    # não as separou).
    evidencias: str | None = None
    avaliacao: str | None = None
