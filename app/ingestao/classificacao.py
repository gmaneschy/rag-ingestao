"""Classifica cada trecho: corpo do texto ou material de apoio (notas, bibliografia, índice...).

Bibliografia, notas, página de copyright e legendas respondem bem a perguntas sobre elas mesmas
("qual o ISBN?") e mal a todas as outras: um trecho com "(Moscow: Gos. Izd., 1928), 70" ou uma
lista de ISBNs parece relevante para quase qualquer pergunta sobre o assunto e ocupa o lugar de
conteúdo de verdade. Por isso a busca padrão usa só o `corpo`; os demais tipos continuam no
banco e entram quando o pedido os inclui (`incluir_tipos`).

Nada é apagado: classificar é reversível, e é heurística (erra), então a API mostra o tipo.
"""

import re
from collections import Counter
from dataclasses import replace

from app.ingestao.divisao import TrechoNovo
from app.ingestao.secoes import normalizar

TIPOS = ("corpo", "notas", "bibliografia", "indice", "copyright", "fragmento")
# Trechos de apoio que formam blocos contíguos (notas e bibliografia): o suavizador os completa.
_BLOCO_DE_REFERENCIAS = {"notas", "bibliografia"}

# Abaixo disso, e sem terminar em pontuação de frase, é título solto, legenda ou folha de rosto
# ("Erotic Utopia", "Kornei Chukovsky at his desk"): não há o que responder. Uma frase curta
# completa ("O prazo é de trinta dias.") continua sendo conteúdo.
TAMANHO_MIN = 60
_FIM_DE_FRASE = (".", "!", "?", "…")

_TITULOS = {
    "notas": {"notes", "notas", "endnotes", "footnotes", "notasdefim", "notesandreferences"},
    "bibliografia": {
        "bibliography", "references", "referencias", "bibliografia", "workscited", "sources",
        "fontes", "furtherreading", "selectedbibliography",
    },
    "indice": {
        "index", "indice", "indiceremissivo", "contents", "tableofcontents", "toc", "sumario",
        "listofillustrations", "illustrations", "ilustracoes", "listofabbreviations",
    },
    "copyright": {"copyright", "colophon", "creditos", "ficha", "fichacatalografica"},
}  # fmt: skip

_ANO = re.compile(r"\b(?:1[5-9]\d\d|20[0-2]\d)\b")
# "(Moscow: Novoe literaturnoe obozrenie, 1994)" ou "[Garden City, N.J.: Doubleday, 1966]"
_EDICAO = re.compile(r"[(\[][A-Z][^()\[\]]{2,60}:\s*[^()\[\]]{3,80}\b(?:1[5-9]\d\d|20[0-2]\d)\b")
_ABREVIATURA = re.compile(
    r"\b(?:ibid|op\.\s*cit|cf|pp|vol|trans|eds?|comp|org|izd|idem)\.|\bPress,\s*(?:1[5-9]|20)\d\d",
    re.IGNORECASE,
)
# ", 260." ou "(1972): 156" ou ", 169-70;": a página citada no fim de uma nota
_PAGINA_CITADA = re.compile(r"[,:]\s*\d{1,4}(?:[-–]\d{1,4})?(?=\.|;|\)|\s*$|\s+\d{1,3}\.)")
# Índice remissivo: "Blok, Alexander, 12, 45–46, 120; Bely, Andrei, 7" (muitos números de página)
_NUMERO_DE_INDICE = re.compile(r",\s*\d{1,3}(?:\s?[–-]\s?\d{1,3})?(?=[,;]|\s|$)")
MIN_NUMEROS_DE_INDICE = 6
_NOTA_NUMERADA = re.compile(r"(?:^|\s)\d{1,3}\.\s?[A-ZÀ-Ý«“\"']")

_COPYRIGHT = (
    re.compile(r"\be?ISBN\b", re.IGNORECASE),
    re.compile(r"all rights reserved|todos os direitos reservados", re.IGNORECASE),
    re.compile(r"library of congress|cataloging[- ]in[- ]publication|ficha catalogr", re.I),
    re.compile(r"\bprinted in\b|\bimpresso (?:no|em)\b", re.IGNORECASE),
    re.compile(r"©|\(c\)\s*\d{4}"),
    re.compile(r"first (?:published|edition)|published by|\bpublisher\b|primeira edi", re.I),
    re.compile(r"includes bibliographical references|hardback|paperback|alk\. paper"),
    re.compile(r"\bcover (?:design|painting|photo)", re.IGNORECASE),
    re.compile(r"(?:^|\s)\d\.\s+[A-Z][\w,. ’'-]{3,40}[—–]|(?:^|\s)I\.\s+Title\b"),
)
_LISTA_DE_ILUSTRACOES = re.compile(
    r"\b(?:frontispiece|list of illustrations|photographs? courtesy|illustrations?)\b",
    re.IGNORECASE,
)


def _por_titulo(secao: str | None) -> str | None:
    if not secao:
        return None
    for parte in secao.split(" > "):
        chave = normalizar(parte)
        for tipo, nomes in _TITULOS.items():
            if chave in nomes:
                return tipo
    return None


def _parece_referencia(texto: str) -> str | None:
    """ "notas" ou "bibliografia" se o trecho é feito de citações, senão None."""
    edicoes = len(_EDICAO.findall(texto))
    abreviaturas = len(_ABREVIATURA.findall(texto))
    numeradas = len(_NOTA_NUMERADA.findall(texto))
    anos = len(_ANO.findall(texto))
    paginas = len(_PAGINA_CITADA.findall(texto))
    # Cada sinal vale um ponto; dois bastam. Um ano ou uma citação isolados são comuns no corpo,
    # e uma lista numerada sozinha também (passos de um manual): por isso ela não basta.
    pontos = min(edicoes, 2) + (abreviaturas >= 1) + (numeradas >= 2) + (anos >= 3) + (paginas >= 3)
    if pontos < 2:
        return None
    return "notas" if numeradas >= 1 or _NOTA_NUMERADA.match(texto) else "bibliografia"


def classificar_texto(texto: str, secao: str | None = None) -> str:
    """O tipo de um trecho isolado, olhando só o texto e o título da seção."""
    texto = texto.strip()
    if len(texto) < TAMANHO_MIN and not texto.endswith(_FIM_DE_FRASE):
        return "fragmento"
    por_titulo = _por_titulo(secao)
    if por_titulo:
        return por_titulo
    if sum(bool(m.search(texto)) for m in _COPYRIGHT) >= 2:
        return "copyright"
    if len(_NUMERO_DE_INDICE.findall(texto)) >= MIN_NUMEROS_DE_INDICE:
        return "indice"
    if _LISTA_DE_ILUSTRACOES.search(texto) and len(_ANO.findall(texto)) >= 2:
        return "indice"
    return _parece_referencia(texto) or "corpo"


def classificar(trechos: list[TrechoNovo]) -> list[TrechoNovo]:
    """Atribui `tipo` a cada trecho e acerta o conjunto: uma página de notas é toda de notas.

    O texto de um trecho de nota às vezes não tem sinal nenhum (uma citação longa, uma frase de
    comentário). Se a maior parte da página é de referências, o resto também é; e uma página
    isolada entre duas de notas é de notas.
    """
    tipos = [classificar_texto(t.texto, t.secao) for t in trechos]

    por_pagina: dict[int, list[int]] = {}
    for i, trecho in enumerate(trechos):
        if trecho.pagina is not None:
            por_pagina.setdefault(trecho.pagina, []).append(i)

    tipo_da_pagina: dict[int, str] = {}
    for pagina, indices in por_pagina.items():
        contagem = Counter(tipos[i] for i in indices if tipos[i] in _BLOCO_DE_REFERENCIAS)
        if contagem and sum(contagem.values()) * 2 >= len(indices):
            tipo_da_pagina[pagina] = contagem.most_common(1)[0][0]
    for pagina in sorted(por_pagina):  # página isolada entre duas de referências
        if pagina not in tipo_da_pagina:
            antes, depois = tipo_da_pagina.get(pagina - 1), tipo_da_pagina.get(pagina + 1)
            if antes and depois:
                tipo_da_pagina[pagina] = antes

    for pagina, tipo in tipo_da_pagina.items():
        for i in por_pagina[pagina]:
            if tipos[i] in {"corpo", *_BLOCO_DE_REFERENCIAS}:
                tipos[i] = tipo
    return [replace(t, tipo=tipo) for t, tipo in zip(trechos, tipos, strict=True)]
