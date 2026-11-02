"""Detecção de seções: títulos e capítulos de qualquer documento, sem supor um formato.

Duas fontes, nesta ordem de confiança:
  1. o índice (bookmarks) embutido no PDF, quando existe;
  2. linhas que parecem título: `# Markdown`, "Capítulo 5", "Parte II", "Seção 2.1", "Prólogo",
     "1.2 Título numerado" e linhas curtas em CAIXA ALTA.

É heurística: erra. Por isso cada trecho guarda a seção detectada e a API a mostra, para o erro
ser visível. Títulos que se repetem em muitas páginas são cabeçalho corrido, não seção.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Origem = Literal[
    "indice", "markdown", "palavra", "decimal", "caixa_alta", "romano", "cabecalho", "estrutura"
]

TAMANHO_MAX_TITULO = 120
# Um título idêntico em tantas páginas é cabeçalho ou rodapé corrido. Em CAIXA ALTA bastam duas:
# carimbo de biblioteca, nome da editora; título de capítulo em caixa alta aparece uma vez só.
PAGINAS_PARA_SER_CABECALHO = 3
PAGINAS_PARA_SER_CARIMBO = 2
# Uma página com mais "títulos" que isso é sumário, índice, lista de notas ou de ilustrações: as
# linhas são entradas de lista, não começos de seção. (Só vale para documentos com páginas.)
MAX_TITULOS_POR_PAGINA = 2
# Números romanos soltos só são capítulos se o documento tiver vários (um "I" sozinho é só uma
# letra que a quebra de linha deixou sozinha).
MIN_ROMANOS = 3


@dataclass(frozen=True)
class Titulo:
    texto: str  # o que vai para `trecho.secao`
    nivel: int  # 1 = o mais alto; um título de nível n encerra os de nível >= n
    origem: Origem
    linha: str = ""  # a linha como aparece no texto (para achar o parágrafo)
    paragrafo: int | None = None  # índice do parágrafo da página; None = começo da página


_NUMERO = (
    r"(?:\d{1,3}(?:\.\d{1,2}){0,3}|[ivxlcdm]{1,8}|uma?|duas?|dois|tr[eê]s|quatro|cinco|seis|sete"
    r"|oito|nove|dez|onze|doze|treze|[qc]atorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte"
    r"|primeir[oa]|segund[oa]|terceir[oa]|quart[oa]|quint[oa]|sext[oa]|s[eé]tim[oa]|oitav[oa]"
    r"|non[oa]|d[eé]cim[oa]|one|two|three|four|five|six|seven|eight|nine|ten|first|second|third"
    r"|fourth|fifth)"
)
_NIVEL_DA_PALAVRA = {
    "parte": 1, "livro": 1, "part": 1, "book": 1, "anexo": 1, "apendice": 1, "appendix": 1,
    "capitulo": 2, "chapter": 2,
    "secao": 3, "section": 3,
}  # fmt: skip
_COM_NUMERO = re.compile(
    r"^(?P<palavra>cap[ií]tulo|se[cç][aã]o|parte|livro|anexo|ap[eê]ndice|chapter|section|part"
    r"|book|appendix)\s+" + _NUMERO + r"\b\s*(?:[-–—:.)]\s*.{1,70})?\.?$",
    re.IGNORECASE,
)
_SOZINHAS = re.compile(
    r"^(?:pr[óo]logo|ep[íi]logo|pref[áa]cio|introdu[çc][ãa]o|conclus[ãa]o|agradecimentos"
    r"|resumo|abstract|refer[êe]ncias|bibliografia|sum[áa]rio|prologue|epilogue|preface"
    r"|introduction|conclusion|acknowledg(?:e)?ments|references)\.?$",
    re.IGNORECASE,
)
_MARKDOWN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_DECIMAL = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+[A-ZÀ-ÝÇ].{2,70}$")
_ROMANO = re.compile(
    r"^(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\.?$"
)
# Linha de nota ou de referência bibliográfica, que não é título mesmo começando com "12.".
_REFERENCIA = re.compile(
    r"-$|[()\[\]\"“”]|\b(?:1[0-9]|20)\d\d\b|\bibid\b|\bop\.\s*cit|\bpp?\.\s*\d", re.IGNORECASE
)
# Início de nota de rodapé ("36. Charles Bernheimer, Figures of Ill Repute: ... in"): vírgula
# numa linha longa, ou linha cortada em preposição/artigo, é citação quebrada, não título.
_LINHA_CORTADA = re.compile(
    r"\b(?:in|of|the|and|to|for|on|with|at|by|a|an|de|da|do|das|dos|em|e|para|com)$"
)


def _frase_numerada(linha: str) -> bool:
    """ "26. Andrei Bely wrote in ... that heroes": nota em frase corrida, não título em Title Case.

    Título longo usa só preposições e artigos em minúscula (curtos); verbo e substantivo comum em
    minúscula, com 4 letras ou mais, denunciam uma frase.
    """
    palavras = linha.split()
    if len(palavras) < 8 or not re.match(r"\d+\.\s", linha):
        return False
    return any(len(p) >= 4 and p[:1].islower() for p in palavras[1:])


# Endereço ("3 Henrietta Street") e instituição ("BURLINGAME PUBLIC LIBRARY") não são título.
_ENDERECO = re.compile(
    r"\b(?:street|road|avenue|square|lane|drive|rua|avenida|pra[çc]a|rodovia)\s*\.?$", re.I
)
_INSTITUICAO = re.compile(
    r"\b(?:library|university|press|publish\w*|editora|biblioteca|universidade|copyright|isbn)\b",
    re.IGNORECASE,
)
_CAIXA_ALTA = re.compile(r"^[A-ZÀ-ÝÇ0-9][A-ZÀ-ÝÇ0-9 ,.:;'’\-–—&()/]{3,70}$")


def normalizar(texto: str) -> str:
    """Sem acento, sem pontuação, em minúsculas: para comparar títulos."""
    sem_acento = unicodedata.normalize("NFKD", texto.casefold())
    return re.sub(r"[\W_]+", "", "".join(c for c in sem_acento if not unicodedata.combining(c)))


def _limitar(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto if len(texto) <= TAMANHO_MAX_TITULO else texto[: TAMANHO_MAX_TITULO - 1] + "…"


def titulo_da_linha(linha: str) -> Titulo | None:
    """O título que a linha representa, ou None se for texto corrido."""
    linha = linha.strip()
    if not linha or len(linha) > 90:
        return None

    md = _MARKDOWN.match(linha)
    if md:
        return Titulo(_limitar(md.group(2)), len(md.group(1)), "markdown", linha)

    com_numero = _COM_NUMERO.match(linha)
    # Frase que a quebra de linha deixou começando em "chapter 7)." não é título: título abre
    # com maiúscula.
    if com_numero and linha[0].isupper():
        palavra = normalizar(com_numero.group("palavra"))
        return Titulo(_limitar(linha), _NIVEL_DA_PALAVRA[palavra], "palavra", linha)
    if _SOZINHAS.match(linha):
        return Titulo(_limitar(linha), 1, "palavra", linha)

    if linha.endswith((".", "!", "?", ",", ";")):
        return None  # título não termina em pontuação de frase
    if _ROMANO.match(linha) and len(linha.rstrip(".")) <= 8:
        return Titulo(_limitar(linha.rstrip(".")), 2, "romano", linha)
    decimal = _DECIMAL.match(linha)
    if (
        decimal
        and len(linha.split()) <= 10
        and not _REFERENCIA.search(linha)
        and not _ENDERECO.search(linha)
        and not _LINHA_CORTADA.search(linha)
        and not ("," in linha and len(linha.split()) >= 6)
        and not _frase_numerada(linha)
    ):
        return Titulo(_limitar(linha), decimal.group(1).count(".") + 1, "decimal", linha)

    letras = [c for c in linha if c.isalpha()]
    if (
        _CAIXA_ALTA.match(linha)
        and len(letras) >= 4
        and len(letras) / len(linha) >= 0.7
        and len(linha.split()) <= 10
        and not _INSTITUICAO.search(linha)
        and not _ENDERECO.search(linha)
    ):
        return Titulo(_limitar(linha), 1, "caixa_alta", linha)
    return None


def detectar_no_texto(paginas: list[str]) -> list[list[tuple[int, Titulo]]]:
    """Por página, os pares (índice da linha, título) que parecem título.

    Depois da detecção, descarta o que se repete em muitas páginas (cabeçalho corrido):
    caixa alta, decimal e Markdown repetidos somem por completo; "Capítulo N" repetido fica só
    na primeira página em que aparece.
    """
    achados = [
        [(i, t) for i, linha in enumerate(texto.split("\n")) if (t := titulo_da_linha(linha))]
        for texto in paginas
    ]
    achados = _sem_paginas_de_lista(achados, com_paginas=len(paginas) > 1)
    paginas_de: dict[str, set[int]] = {}
    for numero, da_pagina in enumerate(achados):
        for _, titulo in da_pagina:
            paginas_de.setdefault(normalizar(titulo.texto), set()).add(numero)

    vistos: set[str] = set()
    limpos: list[list[tuple[int, Titulo]]] = []
    for da_pagina in achados:
        mantidos = []
        for i, titulo in da_pagina:
            chave = normalizar(titulo.texto)
            minimo = (
                PAGINAS_PARA_SER_CARIMBO
                if titulo.origem == "caixa_alta"
                else (PAGINAS_PARA_SER_CABECALHO)
            )
            corrido = len(paginas_de[chave]) >= minimo
            if corrido and titulo.origem != "palavra":
                continue
            if corrido and chave in vistos:
                continue
            vistos.add(chave)
            mantidos.append((i, titulo))
        limpos.append(mantidos)
    return limpos


def _sem_paginas_de_lista(
    achados: list[list[tuple[int, Titulo]]], *, com_paginas: bool
) -> list[list[tuple[int, Titulo]]]:
    """Descarta os "títulos" de páginas que parecem lista (sumário, notas) e romanos isolados."""
    resultado = []
    for da_pagina in achados:
        de_texto = [a for a in da_pagina if a[1].origem != "markdown"]
        lista = com_paginas and len(de_texto) > MAX_TITULOS_POR_PAGINA
        resultado.append([] if lista else da_pagina)
    romanos = {a[1].texto for da_pagina in resultado for a in da_pagina if a[1].origem == "romano"}
    if len(romanos) < MIN_ROMANOS:
        resultado = [[a for a in da_pagina if a[1].origem != "romano"] for da_pagina in resultado]
    return resultado


def isolar(texto: str, marcas: list[tuple[int, Titulo]]) -> str:
    """Põe cada título em um parágrafo só dele, para a junção de linhas não o grudar no texto."""
    linhas = texto.split("\n")
    for i, _ in marcas:
        linhas[i] = f"\n\n{linhas[i]}\n\n"
    return "\n".join(linhas)


def paragrafos_da_pagina(texto: str) -> list[str]:
    """Os parágrafos como a divisão em trechos os enxerga."""
    return [p.strip() for p in texto.split("\n\n") if p.strip()]


def indexar(titulos: list[Titulo], paragrafos: list[str]) -> tuple[Titulo, ...]:
    """Descobre em que parágrafo da página cada título aparece (na ordem em que vieram)."""
    resultado = []
    inicio = 0
    for titulo in titulos:
        alvo = normalizar(titulo.linha)
        achado = None
        for i in range(inicio, len(paragrafos)):
            candidato = normalizar(paragrafos[i])
            if candidato == alvo or (titulo.origem == "indice" and candidato.startswith(alvo)):
                achado = i
                break
        if achado is not None:
            inicio = achado
        resultado.append(Titulo(titulo.texto, titulo.nivel, titulo.origem, titulo.linha, achado))
    return tuple(resultado)


def titulos_do_indice(leitor) -> dict[int, list[Titulo]]:
    """Os bookmarks do PDF por número de página (1 = primeira). Vazio se não houver."""
    por_pagina: dict[int, list[Titulo]] = {}

    def percorrer(itens, nivel: int) -> None:
        for item in itens:
            if isinstance(item, list):
                percorrer(item, nivel + 1)
                continue
            try:
                titulo = _limitar(str(item.title))
                pagina = leitor.get_destination_page_number(item)
            except Exception:  # destinos quebrados são comuns; ignora só esse item
                continue
            if titulo and pagina is not None and pagina >= 0:
                por_pagina.setdefault(pagina + 1, []).append(
                    Titulo(titulo, min(nivel, 6), "indice", titulo)
                )

    try:
        percorrer(leitor.outline, 1)
    except Exception:
        return {}
    return por_pagina
