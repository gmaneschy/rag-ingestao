"""Métricas puras (sem banco, sem modelo): recuperação, recusa, fundamentação e latência."""

import re
import statistics
import unicodedata
from collections.abc import Sequence

from app.avaliacao.conjunto import Suporte
from app.llm.tipos import TrechoRecuperado


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e com espaços simples: 'Lídia  X' casa com 'lidia x'."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return " ".join(sem_acento.casefold().split())


def normalizar_nome(nome: str) -> str:
    """Nome de arquivo comparável: sem acento, sem pontuação ("Título: subtítulo" casa com
    "Título subtítulo.pdf", já que o Windows não aceita ":" em nomes de arquivo)."""
    return " ".join(re.sub(r"[\W_]+", " ", normalizar(nome)).split())


def documento_casa(suporte: Suporte, trecho: TrechoRecuperado) -> bool:
    return normalizar_nome(suporte.documento) in normalizar_nome(trecho.documento)


def casa(suporte: Suporte, trecho: TrechoRecuperado) -> bool:
    if not documento_casa(suporte, trecho):
        return False
    if suporte.paginas:
        if trecho.pagina is None or not suporte.paginas[0] <= trecho.pagina <= suporte.paginas[1]:
            return False
    texto = normalizar(trecho.texto)
    if not all(normalizar(termo) in texto for termo in suporte.contem):
        return False
    return not suporte.contem_algum or any(
        normalizar(termo) in texto for termo in suporte.contem_algum
    )


def acerta(suportes: Sequence[Suporte], trecho: TrechoRecuperado) -> bool:
    return any(casa(s, trecho) for s in suportes)


def primeiro_acerto(suportes: Sequence[Suporte], trechos: Sequence[TrechoRecuperado]) -> int | None:
    """Posição (1 = primeiro) do primeiro trecho correto, ou None."""
    for posicao, trecho in enumerate(trechos, start=1):
        if acerta(suportes, trecho):
            return posicao
    return None


def recall_em_k(posicoes: Sequence[int | None], k: int) -> float:
    """Fração das perguntas cujo trecho correto aparece entre os k primeiros."""
    if not posicoes:
        return 0.0
    return sum(1 for p in posicoes if p is not None and p <= k) / len(posicoes)


def mrr(posicoes: Sequence[int | None], k: int) -> float:
    """Média de 1/posição do primeiro acerto (0 se fora dos k primeiros)."""
    if not posicoes:
        return 0.0
    return sum(1 / p for p in posicoes if p is not None and p <= k) / len(posicoes)


def razao(parte: int, total: int) -> float | None:
    return parte / total if total else None


def percentil(valores: Sequence[float], p: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, round(p * (len(ordenados) - 1)))
    return ordenados[indice]


def media(valores: Sequence[float]) -> float | None:
    return statistics.fmean(valores) if valores else None
