"""Extração de texto: PDF com texto, TXT e Markdown. OCR para PDF escaneado fica para depois."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger("app.ingestao")

EXTENSOES_ACEITAS = frozenset({".pdf", ".txt", ".md"})


class ErroDeExtracao(Exception):
    """O arquivo não pôde virar texto; a mensagem vai para documento.mensagem_erro."""


@dataclass(frozen=True)
class Pagina:
    numero: int | None  # None nos formatos sem página (TXT, Markdown)
    texto: str


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


def _ler_pdf(caminho: Path, max_paginas: int) -> list[Pagina]:
    try:
        leitor = PdfReader(caminho)
        if leitor.is_encrypted:
            raise ErroDeExtracao("PDF protegido por senha")
        total = len(leitor.pages)
        if total > max_paginas:
            raise ErroDeExtracao(f"PDF com {total} páginas; o limite é {max_paginas}")
        paginas = []
        for numero, pagina in enumerate(leitor.pages, start=1):
            texto = limpar(pagina.extract_text() or "")
            texto = juntar_linhas(remover_numero_de_pagina(texto, numero))
            if texto:
                paginas.append(Pagina(numero=numero, texto=texto))
    except ErroDeExtracao:
        raise
    except (PyPdfError, ValueError, OSError, RecursionError, KeyError, TypeError) as erro:
        # PDFs malformados levantam de tudo; o motivo detalhado vai só para o log.
        logger.warning("falha ao ler PDF", extra={"erro": type(erro).__name__})
        raise ErroDeExtracao("PDF corrompido ou em formato não suportado") from erro
    return paginas


def _ler_texto(caminho: Path) -> list[Pagina]:
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    texto = limpar(texto)
    return [Pagina(numero=None, texto=texto)] if texto else []


def extrair_paginas(caminho: Path, *, max_paginas: int = 500) -> list[Pagina]:
    """Devolve as páginas com texto. Levanta ErroDeExtracao se não houver nada aproveitável."""
    extensao = caminho.suffix.lower()
    if extensao == ".pdf":
        paginas = _ler_pdf(caminho, max_paginas)
    elif extensao in {".txt", ".md"}:
        paginas = _ler_texto(caminho)
    else:
        raise ErroDeExtracao(f"Tipo de arquivo não suportado: {extensao or 'sem extensão'}")
    if not paginas:
        raise ErroDeExtracao(
            "Nenhum texto extraível (PDF escaneado? o OCR ainda não foi implementado)"
        )
    return paginas
