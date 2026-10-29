"""Divisão do texto em trechos: por parágrafo, com sobreposição, sem atravessar páginas.

Um trecho nunca mistura páginas, para a citação "documento, página N" ser sempre verdadeira,
nem seções, para ter uma seção só.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.ingestao.extracao import Pagina
from app.ingestao.secoes import Titulo

_FRASE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TrechoNovo:
    posicao: int
    pagina: int | None
    texto: str
    secao: str | None = None  # ex.: "Parte I > Capítulo 2"


def _quebrar_longo(paragrafo: str, tamanho: int) -> list[str]:
    """Quebra um parágrafo maior que o tamanho: primeiro por frase, depois por palavra."""
    pedacos: list[str] = []
    atual = ""
    for frase in _FRASE.split(paragrafo):
        for parte in _por_palavras(frase, tamanho):
            if atual and len(atual) + 1 + len(parte) > tamanho:
                pedacos.append(atual)
                atual = parte
            else:
                atual = f"{atual} {parte}".strip()
    if atual:
        pedacos.append(atual)
    return pedacos


def _por_palavras(texto: str, tamanho: int) -> list[str]:
    if len(texto) <= tamanho:
        return [texto]
    partes: list[str] = []
    atual = ""
    for palavra in texto.split(" "):
        while len(palavra) > tamanho:  # palavra gigante (URL, base64): corte duro
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(palavra[:tamanho])
            palavra = palavra[tamanho:]
        if atual and len(atual) + 1 + len(palavra) > tamanho:
            partes.append(atual)
            atual = palavra
        else:
            atual = f"{atual} {palavra}".strip()
    if atual:
        partes.append(atual)
    return partes


def _cauda(texto: str, sobreposicao: int) -> str:
    """Os últimos `sobreposicao` caracteres, começando em uma palavra inteira."""
    if sobreposicao <= 0 or len(texto) <= sobreposicao:
        return "" if sobreposicao <= 0 else texto
    corte = texto[-sobreposicao:]
    espaco = corte.find(" ")
    return corte[espaco + 1 :] if espaco != -1 else corte


def _agrupar(paragrafos: Iterable[str], tamanho: int, sobreposicao: int) -> list[str]:
    unidades: list[str] = []
    for paragrafo in paragrafos:
        if len(paragrafo) > tamanho:
            unidades.extend(_quebrar_longo(paragrafo, tamanho))
        else:
            unidades.append(paragrafo)

    trechos: list[str] = []
    atual = ""
    for unidade in unidades:
        if atual and len(atual) + 2 + len(unidade) > tamanho:
            trechos.append(atual)
            cauda = _cauda(atual, sobreposicao)
            # Só carrega a sobreposição se o trecho novo ainda couber com ela.
            if cauda and len(cauda) + 2 + len(unidade) <= tamanho:
                atual = f"{cauda}\n\n{unidade}"
            else:
                atual = unidade
        else:
            atual = f"{atual}\n\n{unidade}" if atual else unidade
    if atual:
        trechos.append(atual)
    return trechos


def _caminho(pilha: list[tuple[int, str]]) -> str | None:
    return " > ".join(texto for _, texto in pilha)[:300] or None


def _aplicar(pilha: list[tuple[int, str]], titulo: Titulo) -> None:
    """Um título de nível n encerra os que estavam abertos com nível n ou maior."""
    while pilha and pilha[-1][0] >= titulo.nivel:
        pilha.pop()
    pilha.append((titulo.nivel, titulo.texto))


def dividir(paginas: list[Pagina], tamanho: int = 500, sobreposicao: int = 80) -> list[TrechoNovo]:
    if tamanho < 1 or not 0 <= sobreposicao < tamanho:
        raise ValueError("tamanho deve ser positivo e a sobreposição menor que o tamanho")
    resultado: list[TrechoNovo] = []
    pilha: list[tuple[int, str]] = []  # a seção em vigor, que atravessa as páginas
    for pagina in paginas:
        paragrafos = [p.strip() for p in pagina.texto.split("\n\n") if p.strip()]
        if not paragrafos:
            continue
        # Os títulos da página, por parágrafo onde começam (sem localização = começo da página).
        marcas: dict[int, list[Titulo]] = {}
        for titulo in pagina.titulos:
            indice = min(titulo.paragrafo or 0, len(paragrafos) - 1)
            marcas.setdefault(indice, []).append(titulo)
        limites = sorted({0, *marcas}) + [len(paragrafos)]
        for inicio, fim in zip(limites, limites[1:], strict=False):
            for titulo in marcas.get(inicio, []):
                _aplicar(pilha, titulo)
            secao = _caminho(pilha)
            for texto in _agrupar(paragrafos[inicio:fim], tamanho, sobreposicao):
                resultado.append(
                    TrechoNovo(
                        posicao=len(resultado), pagina=pagina.numero, texto=texto, secao=secao
                    )
                )
    return resultado
