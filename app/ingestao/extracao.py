"""Extração de texto: PDF com texto, TXT e Markdown, com as seções detectadas.

OCR para PDF escaneado e EPUB ficam para a etapa 5."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.ingestao import secoes
from app.ingestao.secoes import Titulo

logger = logging.getLogger("app.ingestao")

EXTENSOES_ACEITAS = frozenset({".pdf", ".txt", ".md"})


class ErroDeExtracao(Exception):
    """O arquivo não pôde virar texto; a mensagem vai para documento.mensagem_erro."""


@dataclass(frozen=True)
class Pagina:
    numero: int | None  # None nos formatos sem página (TXT, Markdown)
    texto: str
    titulos: tuple[Titulo, ...] = ()  # seções que começam nesta página, em ordem


@dataclass(frozen=True)
class Extracao:
    paginas: list[Pagina]
    total_paginas: int | None
    aviso: str | None  # algo que deu certo, mas que o usuário deve saber


_ESPACOS = re.compile(r"[^\S\n]+")
_QUEBRAS = re.compile(r"\n{3,}")


def limpar(texto: str) -> str:
    """Normaliza o texto extraído.

    Remove o caractere NUL (o Postgres recusa em colunas de texto), junta espaços repetidos e
    deixa no máximo uma linha em branco: parágrafos continuam separados, ruído não.
    """
    texto = texto.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    linhas = [_ESPACOS.sub(" ", linha).strip() for linha in texto.split("\n")]
    return _QUEBRAS.sub("\n\n", "\n".join(linhas)).strip()


_FIM_DE_FRASE = (".", "!", "?", "…", ":", '"', "”", "»", ")")
_INICIO_DE_FALA = ("—", "–", "-")
_SO_NUMERO = re.compile(r"^\d{1,5}$")


def remover_numero_de_pagina(texto: str, numero: int) -> str:
    """Tira a linha que é só o número da página (no topo ou no pé), quando ele é o da página.

    Só remove se o número bater com o índice da página no PDF: assim uma linha "3" que faça parte
    do texto (um capítulo numerado, uma lista) não é apagada por engano.
    """
    linhas = texto.strip().split("\n")

    def e_o_numero(linha: str) -> bool:
        return bool(_SO_NUMERO.match(linha)) and int(linha) == numero

    if linhas and e_o_numero(linhas[0]):
        linhas.pop(0)
    elif linhas and e_o_numero(linhas[-1]):
        linhas.pop()
    return limpar("\n".join(linhas))


def juntar_linhas(texto: str) -> str:
    """Desfaz as quebras de linha do layout do PDF, mantendo parágrafos e falas.

    O PDF entrega uma linha por linha impressa. Dentro de um bloco, a quebra vira espaço; hífen
    de fim de linha seguido de minúscula junta a palavra. Uma linha que começa com travessão
    (fala de diálogo) depois de uma frase terminada continua sendo uma linha própria.
    """
    blocos = []
    for bloco in texto.split("\n\n"):
        resultado = ""
        for linha in (x for x in bloco.split("\n") if x):
            if not resultado:
                resultado = linha
            elif resultado.endswith("-") and linha[:1].islower():
                resultado = resultado[:-1] + linha
            elif linha.startswith(_INICIO_DE_FALA) and resultado.endswith(_FIM_DE_FRASE):
                resultado += "\n" + linha
            else:
                resultado += " " + linha
        if resultado:
            blocos.append(resultado)
    return "\n\n".join(blocos)


def _ler_pdf(caminho: Path, max_paginas: int) -> tuple[list[Pagina], int]:
    try:
        leitor = PdfReader(caminho)
        if leitor.is_encrypted:
            raise ErroDeExtracao("PDF protegido por senha")
        total = len(leitor.pages)
        if total > max_paginas:
            raise ErroDeExtracao(f"PDF com {total} páginas; o limite é {max_paginas}")
        textos: list[tuple[int, str]] = []
        for numero, pagina in enumerate(leitor.pages, start=1):
            texto = limpar(pagina.extract_text() or "")
            texto = remover_numero_de_pagina(texto, numero)
            if texto:
                textos.append((numero, texto))
        indice = secoes.titulos_do_indice(leitor)
    except ErroDeExtracao:
        raise
    except (PyPdfError, ValueError, OSError, RecursionError, KeyError, TypeError) as erro:
        # PDFs malformados levantam de tudo; o motivo detalhado vai só para o log.
        logger.warning("falha ao ler PDF", extra={"erro": type(erro).__name__})
        raise ErroDeExtracao("PDF corrompido ou em formato não suportado") from erro
    return _montar_paginas(textos, indice), total


def _montar_paginas(textos: list[tuple[int, str]], indice: dict[int, list[Titulo]]) -> list[Pagina]:
    """Junta as linhas de cada página e anota as seções que começam nela.

    Usa o índice do PDF se ele tiver pelo menos duas entradas; senão procura títulos no texto.
    """
    usar_indice = sum(len(v) for v in indice.values()) >= 2
    marcas_por_pagina = (
        [[] for _ in textos] if usar_indice else secoes.detectar_no_texto([t for _, t in textos])
    )
    paginas = []
    for (numero, texto), marcas in zip(textos, marcas_por_pagina, strict=True):
        if marcas:
            texto = limpar(secoes.isolar(texto, marcas))
        texto = juntar_linhas(texto)
        if not texto:
            continue
        paragrafos = secoes.paragrafos_da_pagina(texto)
        titulos = indice.get(numero, []) if usar_indice else [t for _, t in marcas]
        paginas.append(Pagina(numero, texto, secoes.indexar(titulos, paragrafos)))
    return paginas


def _ler_texto(caminho: Path) -> list[Pagina]:
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    texto = limpar(texto)
    if not texto:
        return []
    marcas = secoes.detectar_no_texto([texto])[0]
    if marcas:
        texto = limpar(secoes.isolar(texto, marcas))
    titulos = secoes.indexar([t for _, t in marcas], secoes.paragrafos_da_pagina(texto))
    return [Pagina(numero=None, texto=texto, titulos=titulos)]


def extrair(caminho: Path, *, max_paginas: int = 500) -> Extracao:
    """Páginas com texto, mais um aviso se algo ficou de fora. Levanta ErroDeExtracao se não
    houver nada aproveitável."""
    extensao = caminho.suffix.lower()
    total: int | None = None
    if extensao == ".pdf":
        paginas, total = _ler_pdf(caminho, max_paginas)
    elif extensao in {".txt", ".md"}:
        paginas = _ler_texto(caminho)
    else:
        raise ErroDeExtracao(f"Tipo de arquivo não suportado: {extensao or 'sem extensão'}")
    if not paginas:
        raise ErroDeExtracao(
            "Nenhum texto extraível (PDF escaneado? o OCR ainda não foi implementado)"
        )
    aviso = None
    if total is not None and len(paginas) < total:
        sem_texto = total - len(paginas)
        aviso = (
            f"{sem_texto} de {total} páginas sem texto extraível (escaneadas?); "
            "o conteúdo delas não foi indexado"
        )
    return Extracao(paginas, total, aviso)


def extrair_paginas(caminho: Path, *, max_paginas: int = 500) -> list[Pagina]:
    return extrair(caminho, max_paginas=max_paginas).paginas
