"""Reordenação dos candidatos da busca por um modelo que lê pergunta e trecho juntos.

A busca (vetorial e textual) compara a pergunta com cada trecho por separado e acha bons
candidatos, mas ordena mal e não diz se o trecho de fato responde. O reordenador (cross-encoder)
lê os dois juntos: mais lento, por isso só olha os poucos candidatos que a busca já separou.
É opcional (RERANKER_MODEL) e a etapa 5 mede se compensa.
"""

import dataclasses
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.consulta.busca import Achado
from app.llm.simulado import _palavras
from app.llm.tipos import ModeloIndisponivel


class Reordenador(Protocol):
    nome_modelo: str

    def pontuar(self, pergunta: str, textos: Sequence[str]) -> list[float]: ...


class ReordenadorLocal:
    """Cross-encoder local (fastembed, ONNX, CPU). Baixa o modelo na primeira vez."""

    def __init__(self, nome_modelo: str, cache_dir: Path | None = None) -> None:
        self.nome_modelo = nome_modelo
        self._cache_dir = cache_dir
        self._modelo = None
        self._trava = threading.Lock()

    def _carregar(self):
        with self._trava:
            if self._modelo is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                try:
                    self._modelo = TextCrossEncoder(
                        model_name=self.nome_modelo,
                        cache_dir=str(self._cache_dir) if self._cache_dir else None,
                    )
                except Exception as erro:
                    raise ModeloIndisponivel(
                        f"Não consegui carregar o reordenador {self.nome_modelo}: "
                        f"{type(erro).__name__}"
                    ) from erro
        return self._modelo

    def pontuar(self, pergunta: str, textos: Sequence[str]) -> list[float]:
        if not textos:
            return []
        return [float(n) for n in self._carregar().rerank(pergunta, list(textos), batch_size=16)]


class ReordenadorSimulado:
    """Fração das palavras da pergunta que o trecho contém: determinístico, para testes."""

    nome_modelo = "simulado-sobreposicao"

    def pontuar(self, pergunta: str, textos: Sequence[str]) -> list[float]:
        palavras = set(_palavras(pergunta))
        if not palavras:
            return [0.0 for _ in textos]
        return [len(palavras & set(_palavras(t))) / len(palavras) for t in textos]


def criar_reordenador(nome_modelo: str, cache_dir: Path | None = None) -> Reordenador | None:
    """None se RERANKER_MODEL está vazio (reordenação desligada)."""
    nome = nome_modelo.strip()
    if not nome:
        return None
    if nome == "simulado":
        return ReordenadorSimulado()
    return ReordenadorLocal(nome, cache_dir)


def tem_evidencia(achados: list[Achado], nota_minima: float) -> bool:
    """Há evidência se o primeiro trecho, já reordenado, tem nota de pelo menos `nota_minima`.

    A nota do cross-encoder responde "este trecho serve para esta pergunta?", o que a similaridade
    do embedding não distingue: nas medições da etapa 5 ela separou as perguntas sem resposta,
    e a similaridade não. A escala da nota depende do modelo: calibre com `avaliacao rodar`.
    """
    return bool(achados) and (achados[0].nota_reranker or float("-inf")) >= nota_minima


def reordenar(pergunta: str, achados: list[Achado], reordenador: Reordenador) -> list[Achado]:
    """Os mesmos achados, com `nota_reranker`, do mais ao menos relevante (estável no empate)."""
    notas = reordenador.pontuar(pergunta, [a.trecho.texto for a in achados])
    pontuados = [
        dataclasses.replace(a, nota_reranker=n) for a, n in zip(achados, notas, strict=True)
    ]
    return sorted(pontuados, key=lambda a: -(a.nota_reranker or 0.0))
