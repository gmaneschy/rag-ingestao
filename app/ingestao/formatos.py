"""Leitores de EPUB, FB2, DOCX, ODT, HTML, MOBI/AZW3 e DjVu.

Os formatos de livro eletrônico e de texto não têm página; têm estrutura. Aqui o título explícito
(h1-h6, estilo "Título", <title> do FB2) vale mais do que qualquer heurística: cada seção vira uma
`Pagina` sem número, com o título no começo, e a citação mostra a seção em vez da página. Se o
arquivo não traz nenhum título, cai nas mesmas heurísticas do TXT.

Segurança: o XML vem de fora, por isso passa por `defusedxml` (sem entidades externas nem bombas de
expansão), e o tamanho descompactado dos ZIPs tem teto.
"""

import logging
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from app.ingestao import cabecalhos
from app.ingestao.extracao import (
    ErroDeExtracao,
    Pagina,
    _montar_paginas,
    limpar,
    paginas_de_texto,
    remover_numero_de_pagina,
)
from app.ingestao.secoes import Titulo

logger = logging.getLogger("app.ingestao")

TETO_DESCOMPACTADO = 300 * 1024 * 1024  # um ZIP de 20 MB não pode virar 10 GB
Bloco = tuple[int, str]  # (nível do título, 0 = parágrafo; texto)
_ESPACOS = re.compile(r"\s+")
FONTES_OFUSCADAS = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}


def _compactar(texto: str) -> str:
    return _ESPACOS.sub(" ", texto).strip()


def _abrir_zip(caminho: Path) -> zipfile.ZipFile:
    try:
        arquivo = zipfile.ZipFile(caminho)
    except (zipfile.BadZipFile, OSError) as erro:
        raise ErroDeExtracao("O arquivo está corrompido ou não é do formato indicado") from erro
    if sum(i.file_size for i in arquivo.infolist()) > TETO_DESCOMPACTADO:
        raise ErroDeExtracao("Arquivo grande demais depois de descompactado")
    return arquivo


def _xml(dados: bytes) -> Element:
    try:
        return ET.fromstring(dados)
    except (ET.ParseError, DefusedXmlException) as erro:
        raise ErroDeExtracao("XML inválido ou inseguro no arquivo") from erro


def _nome(elemento: Element) -> str:
    """Nome da tag sem o namespace."""
    return elemento.tag.rsplit("}", 1)[-1] if isinstance(elemento.tag, str) else ""


def paginas_de_blocos(blocos: list[Bloco]) -> list[Pagina]:
    """Um `Pagina` por seção: o título abre a página e o texto vai até o próximo título."""
    blocos = [(n, t) for n, t in ((n, _compactar(t)) for n, t in blocos) if t]
    if not any(nivel for nivel, _ in blocos):  # nenhum título: heurísticas do TXT
        return paginas_de_texto("\n\n".join(t for _, t in blocos))
    paginas: list[Pagina] = []
    titulo: Titulo | None = None
    paragrafos: list[str] = []

    def fechar() -> None:
        if paragrafos:
            titulos = (titulo,) if titulo else ()
            paginas.append(Pagina(None, "\n\n".join(paragrafos), titulos))

    for nivel, texto in blocos:
        if nivel:
            fechar()
            titulo = Titulo(texto[:120], nivel, "estrutura", texto, 0)
            paragrafos = [texto]
        else:
            paragrafos.append(texto)
    fechar()
    return paginas


# --- HTML e XHTML (base do EPUB, do MOBI e do .html) -----------------------------------------

_BLOCOS = {"p", "div", "li", "tr", "blockquote", "section", "article", "pre", "dd", "dt", "br"}
_IGNORAR = {"script", "style", "head", "svg", "nav", "noscript"}
_TITULOS = {f"h{n}": n for n in range(1, 7)}


class _LeitorHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocos: list[Bloco] = []
        self._nivel = 0
        self._partes: list[str] = []
        self._ignorando = 0

    def _fechar(self) -> None:
        texto = _compactar("".join(self._partes))
        if texto:
            self.blocos.append((self._nivel, texto))
        self._partes = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORAR:
            self._ignorando += 1
        elif tag in _TITULOS:
            self._fechar()
            self._nivel = _TITULOS[tag]
        elif tag in _BLOCOS:
            self._fechar()
        elif tag in {"td", "th"}:
            self._partes.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORAR:
            self._ignorando = max(0, self._ignorando - 1)
        elif tag in _TITULOS:
            self._fechar()
            self._nivel = 0
        elif tag in _BLOCOS:
            self._fechar()

    def handle_data(self, data: str) -> None:
        if not self._ignorando:
            self._partes.append(data)

    def close(self) -> None:
        super().close()
        self._fechar()


def _decodificar(dados: bytes) -> str:
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return dados.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return dados.decode("latin-1")


def blocos_de_html(dados: bytes) -> list[Bloco]:
    leitor = _LeitorHtml()
    leitor.feed(_decodificar(dados))
    leitor.close()
    return leitor.blocos


def ler_html(caminho: Path) -> list[Pagina]:
    return paginas_de_blocos(blocos_de_html(caminho.read_bytes()))


# --- EPUB ------------------------------------------------------------------------------------


def _sem_drm(arquivo: zipfile.ZipFile) -> None:
    if "META-INF/encryption.xml" not in arquivo.namelist():
        return
    raiz = _xml(arquivo.read("META-INF/encryption.xml"))
    algoritmos = {e.get("Algorithm", "") for e in raiz.iter() if _nome(e) == "EncryptionMethod"}
    # Só a ofuscação de fontes (IDPF, Adobe) é inofensiva; qualquer outra coisa é DRM.
    if algoritmos - FONTES_OFUSCADAS:
        raise ErroDeExtracao("EPUB protegido por DRM; não é possível ler o texto")


def ler_epub(caminho: Path) -> list[Pagina]:
    with _abrir_zip(caminho) as arquivo:
        _sem_drm(arquivo)
        try:
            container = _xml(arquivo.read("META-INF/container.xml"))
        except KeyError as erro:
            raise ErroDeExtracao("EPUB sem META-INF/container.xml") from erro
        raiz_opf = next(
            (e.get("full-path") for e in container.iter() if _nome(e) == "rootfile"), None
        )
        if not raiz_opf or raiz_opf not in arquivo.namelist():
            raise ErroDeExtracao("EPUB sem o arquivo de pacote (OPF)")
        opf = _xml(arquivo.read(raiz_opf))
        pasta = PurePosixPath(raiz_opf).parent
        itens = {
            e.get("id"): e
            for e in opf.iter()
            if _nome(e) == "item" and e.get("id") and e.get("href")
        }
        blocos: list[Bloco] = []
        for ref in (e for e in opf.iter() if _nome(e) == "itemref"):
            item = itens.get(ref.get("idref"))
            if item is None or "nav" in (item.get("properties") or "").split():
                continue
            if "html" not in (item.get("media-type") or ""):
                continue
            destino = str(pasta / unquote(item.get("href", "").split("#")[0]))
            destino = str(PurePosixPath(destino))
            try:
                blocos.extend(blocos_de_html(arquivo.read(destino)))
            except KeyError:
                logger.warning("item do EPUB ausente", extra={"item": destino})
    return paginas_de_blocos(blocos)


# --- FB2 -------------------------------------------------------------------------------------


def _texto_do_elemento(elemento: Element) -> str:
    return _compactar("".join(elemento.itertext()))


def _blocos_fb2(secao: Element, nivel: int, blocos: list[Bloco]) -> None:
    for filho in secao:
        nome = _nome(filho)
        if nome == "title":
            blocos.append((nivel, _texto_do_elemento(filho)))
        elif nome == "section":
            _blocos_fb2(filho, nivel + 1, blocos)
        elif nome in {"p", "subtitle", "text-author"}:
            blocos.append((0, _texto_do_elemento(filho)))
        elif nome in {"epigraph", "cite", "poem", "annotation", "stanza"}:
            _blocos_fb2(filho, nivel, blocos)
        elif nome == "v":  # verso
            blocos.append((0, _texto_do_elemento(filho)))


def ler_fb2_bytes(dados: bytes) -> list[Pagina]:
    raiz = _xml(dados)
    blocos: list[Bloco] = []
    for corpo in (e for e in raiz if _nome(e) == "body"):
        if corpo.get("name") == "notes":  # notas de rodapé: só números e referências
            continue
        _blocos_fb2(corpo, 1, blocos)
    titulo_do_livro = next(
        (_texto_do_elemento(e) for e in raiz.iter() if _nome(e) == "book-title"), ""
    )
    if titulo_do_livro and blocos:
        blocos.insert(0, (1, titulo_do_livro))
        blocos = [(n + 1 if n and i else n, t) for i, (n, t) in enumerate(blocos)]
    return paginas_de_blocos(blocos)


def ler_fb2(caminho: Path) -> list[Pagina]:
    return ler_fb2_bytes(caminho.read_bytes())


def ler_fbz(caminho: Path) -> list[Pagina]:
    with _abrir_zip(caminho) as arquivo:
        nomes = [n for n in arquivo.namelist() if n.lower().endswith(".fb2")]
        if not nomes:
            raise ErroDeExtracao("FBZ sem arquivo .fb2 dentro")
        return ler_fb2_bytes(arquivo.read(nomes[0]))


# --- DOCX ------------------------------------------------------------------------------------


def _niveis_de_titulo_docx(arquivo: zipfile.ZipFile) -> dict[str, int]:
    """Estilos que são títulos: pelo nome ("Heading 2", "Título 2", "Title") ou pelo nível de
    estrutura (outlineLvl) definido no estilo."""
    if "word/styles.xml" not in arquivo.namelist():
        return {}
    niveis: dict[str, int] = {}
    for estilo in _xml(arquivo.read("word/styles.xml")):
        if _nome(estilo) != "style":
            continue
        id_ = next((v for k, v in estilo.attrib.items() if k.endswith("styleId")), None)
        nome = ""
        nivel = 0
        for filho in estilo.iter():
            if _nome(filho) == "name":
                nome = next((v for k, v in filho.attrib.items() if k.endswith("val")), "")
            elif _nome(filho) == "outlineLvl":
                valor = next((v for k, v in filho.attrib.items() if k.endswith("val")), "")
                nivel = int(valor) + 1 if valor.isdigit() else 0
        achado = re.match(r"(?:heading|t[ií]tulo)\s*(\d)", nome.lower())
        if achado:
            nivel = int(achado.group(1))
        elif nome.lower() in {"title", "título"}:
            nivel = 1
        if id_ and nivel:
            niveis[id_] = nivel
    return niveis


def _texto_do_paragrafo_docx(p: Element) -> str:
    partes = []
    for e in p.iter():
        nome = _nome(e)
        if nome == "t" and e.text:
            partes.append(e.text)
        elif nome in {"tab", "br"}:
            partes.append(" ")
    return _compactar("".join(partes))


def _blocos_docx(corpo: Element, niveis: dict[str, int]) -> list[Bloco]:
    blocos: list[Bloco] = []
    for no in corpo:
        nome = _nome(no)
        if nome == "p":
            estilo = next(
                (v for e in no.iter() if _nome(e) == "pStyle" for v in e.attrib.values()), ""
            )
            blocos.append((niveis.get(estilo, 0), _texto_do_paragrafo_docx(no)))
        elif nome == "tbl":
            for linha in (e for e in no.iter() if _nome(e) == "tr"):
                celulas = [
                    " ".join(_texto_do_paragrafo_docx(p) for p in c.iter() if _nome(p) == "p")
                    for c in linha
                    if _nome(c) == "tc"
                ]
                blocos.append((0, " | ".join(c for c in celulas if c)))
    return blocos


def ler_docx(caminho: Path) -> list[Pagina]:
    with _abrir_zip(caminho) as arquivo:
        if "word/document.xml" not in arquivo.namelist():
            raise ErroDeExtracao("DOCX sem word/document.xml")
        if "EncryptedPackage" in arquivo.namelist():
            raise ErroDeExtracao("DOCX protegido por senha")
        niveis = _niveis_de_titulo_docx(arquivo)
        corpo = next(
            (e for e in _xml(arquivo.read("word/document.xml")) if _nome(e) == "body"), None
        )
    return paginas_de_blocos(_blocos_docx(corpo, niveis) if corpo is not None else [])


# --- ODT -------------------------------------------------------------------------------------


def ler_odt(caminho: Path) -> list[Pagina]:
    with _abrir_zip(caminho) as arquivo:
        if "content.xml" not in arquivo.namelist():
            raise ErroDeExtracao("ODT sem content.xml")
        raiz = _xml(arquivo.read("content.xml"))
    blocos: list[Bloco] = []
    for e in raiz.iter():
        nome = _nome(e)
        if nome == "h":
            nivel = next((v for k, v in e.attrib.items() if k.endswith("outline-level")), "1")
            blocos.append((int(nivel) if nivel.isdigit() else 1, _texto_do_elemento(e)))
        elif nome == "p":
            blocos.append((0, _texto_do_elemento(e)))
    # Parágrafos dentro de títulos e tabelas aparecem duas vezes na iteração plana: tira repetição.
    vistos: list[Bloco] = []
    for b in blocos:
        if not (vistos and vistos[-1][1] == b[1]):
            vistos.append(b)
    return paginas_de_blocos(vistos)


# --- MOBI / AZW3 -----------------------------------------------------------------------------


def ler_mobi(caminho: Path) -> list[Pagina]:
    try:
        import mobi
    except ImportError as erro:  # pragma: no cover
        raise ErroDeExtracao("Suporte a MOBI não instalado (pacote 'mobi')") from erro
    pasta = None
    try:
        pasta, resultado = mobi.extract(str(caminho))
        destino = Path(resultado)
        if destino.suffix.lower() == ".epub":
            return ler_epub(destino)
        if destino.suffix.lower() in {".html", ".htm"}:
            return ler_html(destino)
        raise ErroDeExtracao("Não consegui converter o MOBI em texto")
    except ErroDeExtracao:
        raise
    except Exception as erro:  # a biblioteca levanta de tudo com arquivo ruim ou com DRM
        logger.warning("falha ao ler MOBI", extra={"erro": type(erro).__name__})
        raise ErroDeExtracao("MOBI/AZW3 corrompido ou protegido por DRM") from erro
    finally:
        if pasta:
            shutil.rmtree(pasta, ignore_errors=True)


# --- DjVu ------------------------------------------------------------------------------------


def ler_djvu(caminho: Path, max_paginas: int) -> tuple[list[Pagina], int]:
    """Texto da camada de texto do DjVu (ferramenta `djvutxt`, do pacote djvulibre-bin)."""
    exe = shutil.which("djvutxt")
    if not exe:
        raise ErroDeExtracao("DjVu exige o programa djvutxt (pacote djvulibre-bin) no servidor")

    def rodar(*args: str) -> bytes:
        try:
            return subprocess.run(  # noqa: S603 - caminho nosso, sem shell
                [*args], capture_output=True, timeout=60, check=True
            ).stdout
        except (subprocess.SubprocessError, OSError) as erro:
            raise ErroDeExtracao("DjVu corrompido ou ilegível") from erro

    sed = shutil.which("djvused")
    if not sed:
        raise ErroDeExtracao("DjVu exige o djvulibre-bin (djvused) no servidor")
    contagem = rodar(sed, str(caminho), "-e", "n").decode().strip()
    total = int(contagem) if contagem.isdigit() else 0
    if total > max_paginas:
        raise ErroDeExtracao(f"DjVu com {total} páginas; o limite é {max_paginas}")
    # Uma chamada por página: o djvutxt de arquivo inteiro omite as páginas sem texto, e o
    # número da página (que vira a citação) sairia errado.
    brutas = [
        _decodificar(rodar(exe, f"--page={n}", str(caminho))).replace("\f", "")
        for n in range(1, total + 1)
    ]
    textos: list[tuple[int, str]] = []
    for numero, bruto in enumerate(brutas, start=1):
        texto = remover_numero_de_pagina(limpar(bruto), numero)
        if texto:
            textos.append((numero, texto))
    corrido = cabecalhos.remover_corridos(textos)
    return _montar_paginas(corrido.textos, {}, corrido.titulos), total
