"""Tipos trocados entre o RAG e os provedores de embeddings e de resposta."""

from dataclasses import dataclass

# Dimensão da coluna trecho.vetor (migração 0002). Mudar exige nova migração e reindexação.
DIMENSAO_VETOR = 384

MENSAGEM_SEM_EVIDENCIA = "Não encontrei evidência nos documentos fornecidos para responder."


@dataclass(frozen=True)
class TrechoRecuperado:
    """Um trecho que a busca devolveu e que pode ser citado na resposta."""

    id: int
    documento: str
    pagina: int | None
    texto: str


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
