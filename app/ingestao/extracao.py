"""Extração de texto, com as seções detectadas.

PDF e DjVu (com camada de texto) têm página; TXT, Markdown e os formatos de livro eletrônico e de
texto (EPUB, FB2, DOCX, ODT, HTML, MOBI) têm seção (veja `formatos`). OCR de escaneados fica para
depois."""

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.ingestao import cabecalhos, secoes
from app.ingestao.secoes import Titulo

logger = logging.getLogger("app.ingestao")

EXTENSOES_ACEITAS = frozenset(
    {
        ".pdf", ".txt", ".md", ".epub", ".fb2", ".fbz", ".docx", ".odt", ".html", ".htm",
        ".mobi", ".azw3", ".djvu", ".djv",
    }
)  # fmt: skip


def extensao_do_nome(nome: str) -> str:
    """Extensão que decide o formato. `livro.fb2.zip` é um FBZ (o seletor de arquivos só enxerga o
    último pedaço, ".zip"); o resto vem do sufixo normal."""
    minusculo = nome.lower()
    if minusculo.endswith(".fb2.zip"):
        return ".fbz"
    return Path(minusculo).suffix


_ZIP = {".epub", ".fbz", ".docx", ".odt"}
FORMATOS = EXTENSOES_ACEITAS - {".pdf", ".txt", ".md"}


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
# Ligaduras tipográficas (ﬁ, ﬂ...) que o PDF entrega como um caractere só: "ﬁn de siècle" não
# casa com "fin" na busca e o embedding as enxerga como outra coisa.
_LIGADURAS = {
    c: unicodedata.normalize("NFKC", c) for c in "\ufb00\ufb01\ufb02\ufb03\ufb04\ufb05\ufb06"
}
_HIFEN_SUAVE_NO_FIM = re.compile("\u00ad\n(?=[^\\W\\d_])")
_HIFEN_SUAVE = re.compile("\u00ad[^\\S\n]*")
_ZERO_LARGURA = re.compile("[\u200b\u200c\u200d\ufeff]")
# Defeitos comuns de PDF com camada de OCR: "Russia’ s", "century , these", "law ."
_APOSTROFO_SOLTO = re.compile(r"(?<=\w)([’'])[^\S\n]+(?=s\b)")
_ESPACO_ANTES_DE_VIRGULA = re.compile(r"(?<=[^\W\d_])[^\S\n]+(?=[,;:](?:\s|$))")
_ESPACO_ANTES_DE_PONTO = re.compile(r"(?<=[^\W\d_])[^\S\n]+\.(?=\s+[^\s.]|\s*$)")


def normalizar_texto(texto: str) -> str:
    """Conserta o que a extração de PDF costuma estragar, sem mudar o sentido do texto."""
    texto = texto.translate(str.maketrans(_LIGADURAS))
    texto = _ZERO_LARGURA.sub("", texto)
    texto = _HIFEN_SUAVE_NO_FIM.sub("", texto)
    texto = _HIFEN_SUAVE.sub("", texto)
    texto = _APOSTROFO_SOLTO.sub(r"\1", texto)
    texto = _ESPACO_ANTES_DE_VIRGULA.sub("", texto)
    return _ESPACO_ANTES_DE_PONTO.sub(".", texto)


def limpar(texto: str) -> str:
    """Normaliza o texto extraído.

    Remove o caractere NUL (o Postgres recusa em colunas de texto), junta espaços repetidos e
    deixa no máximo uma linha em branco: parágrafos continuam separados, ruído não.
    """
    texto = normalizar_texto(texto.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n"))
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
        corrido = cabecalhos.remover_corridos(textos)
        textos = corrido.textos
        if corrido.removidas:
            logger.info("cabeçalhos e rodapés removidos", extra={"linhas": corrido.removidas})
    except ErroDeExtracao:
        raise
    except (PyPdfError, ValueError, OSError, RecursionError, KeyError, TypeError) as erro:
        # PDFs malformados levantam de tudo; o motivo detalhado vai só para o log.
        logger.warning("falha ao ler PDF", extra={"erro": type(erro).__name__})
        raise ErroDeExtracao("PDF corrompido ou em formato não suportado") from erro
    return _montar_paginas(textos, indice, corrido.titulos), total


def _montar_paginas(
    textos: list[tuple[int, str]],
    indice: dict[int, list[Titulo]],
    de_cabecalho: dict[int, Titulo] | None = None,
) -> list[Pagina]:
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
        if usar_indice:
            titulos = indice.get(numero, [])
        else:
            cabecalho = [de_cabecalho[numero]] if de_cabecalho and numero in de_cabecalho else []
            titulos = cabecalho + [t for _, t in marcas]
        paginas.append(Pagina(numero, texto, secoes.indexar(titulos, paragrafos)))
    return paginas


def paginas_de_texto(texto: str) -> list[Pagina]:
    """Texto corrido -> uma página sem número, com as seções que as heurísticas acharem."""
    texto = limpar(texto)
    if not texto:
        return []
    marcas = secoes.detectar_no_texto([texto])[0]
    if marcas:
        texto = limpar(secoes.isolar(texto, marcas))
    titulos = secoes.indexar([t for _, t in marcas], secoes.paragrafos_da_pagina(texto))
    return [Pagina(numero=None, texto=texto, titulos=titulos)]


def _ler_texto(caminho: Path) -> list[Pagina]:
    bruto = caminho.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    return paginas_de_texto(texto)


def _parece_fb2(conteudo: bytes) -> bool:
    """Procura <FictionBook> no começo, sem depender da codificação: há FB2 em UTF-16 (letras
    separadas por bytes nulos), com comentários longos antes da raiz e com o nome em minúsculas."""
    cabeca = conteudo[:65536].replace(b"\x00", b"").lower()
    return b"<fictionbook" in cabeca


def conferir_assinatura(nome: str, conteudo: bytes) -> None:
    """O conteúdo bate com a extensão? Barra o óbvio antes de gravar (ValueError)."""
    ext = extensao_do_nome(nome)
    inicio = conteudo.lstrip()[:16]
    erro = None
    if ext == ".pdf" and not inicio.startswith(b"%PDF-"):
        erro = "O arquivo não é um PDF válido"
    elif ext in _ZIP and not conteudo.startswith(b"PK"):
        erro = f"O arquivo não é um {ext[1:].upper()} válido"
    elif ext in {".mobi", ".azw3"} and conteudo[60:68] not in {b"BOOKMOBI", b"TEXtREAd"}:
        erro = "O arquivo não é um MOBI/AZW3 válido"
    elif ext in {".djvu", ".djv"} and not conteudo.startswith(b"AT&TFORM"):
        erro = "O arquivo não é um DjVu válido"
    elif ext == ".fb2" and not conteudo.startswith(b"PK") and not _parece_fb2(conteudo):
        comeco = conteudo[:48].decode("utf-8", errors="replace").replace("\x00", "")
        erro = (
            "O arquivo não parece um FB2 (não achei a marca <FictionBook> no começo). "
            f"Ele começa com: {comeco!r}"
        )
    if erro:
        raise ValueError(erro)


def extrair(caminho: Path, *, max_paginas: int = 500) -> Extracao:
    """Páginas com texto, mais um aviso se algo ficou de fora. Levanta ErroDeExtracao se não
    houver nada aproveitável."""
    extensao = extensao_do_nome(caminho.name)
    total: int | None = None
    if extensao == ".pdf":
        paginas, total = _ler_pdf(caminho, max_paginas)
    elif extensao in {".txt", ".md"}:
        paginas = _ler_texto(caminho)
    elif extensao in FORMATOS:
        from app.ingestao import formatos  # aqui para não importar em círculo

        if extensao == ".fb2" and caminho.read_bytes()[:2] == b"PK":
            extensao = ".fbz"  # um ZIP com o nome .fb2

        if extensao in {".djvu", ".djv"}:
            paginas, total = formatos.ler_djvu(caminho, max_paginas)
        else:
            leitores = {
                ".epub": formatos.ler_epub, ".fb2": formatos.ler_fb2, ".fbz": formatos.ler_fbz,
                ".docx": formatos.ler_docx, ".odt": formatos.ler_odt, ".html": formatos.ler_html,
                ".htm": formatos.ler_html, ".mobi": formatos.ler_mobi, ".azw3": formatos.ler_mobi,
            }  # fmt: skip
            paginas = leitores[extensao](caminho)
    else:
        raise ErroDeExtracao(f"Tipo de arquivo não suportado: {extensao or 'sem extensão'}")
    if not paginas:
        raise ErroDeExtracao(
            "Nenhum texto extraível (escaneado, sem camada de texto? o OCR ainda não existe)"
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
