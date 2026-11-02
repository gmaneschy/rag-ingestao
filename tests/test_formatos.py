"""EPUB, FB2, DOCX, ODT, HTML, MOBI e DjVu: extração, segurança e envio de ponta a ponta."""

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.ingestao import divisao, formatos
from app.ingestao.extracao import EXTENSOES_ACEITAS, ErroDeExtracao, conferir_assinatura, extrair

CONTAINER = (
    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
    'version="1.0"><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></container>'
)
OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="cap%201.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="cap2.xhtml" media-type="application/xhtml+xml"/>
<item id="img" href="x.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="nav"/><itemref idref="c1"/><itemref idref="c2"/>
<itemref idref="img"/></spine>
</package>"""
CAP1 = (
    "<html><head><title>NAO</title></head><body><h1>A garantia</h1>"
    "<p>A garantia cobre defeitos\n  por um ano.</p><h2>Exclusoes</h2>"
    "<p>Nao cobre mau uso.</p><script>var x = 'segredo';</script></body></html>"
)
CAP2 = "<html><body><h1>A entrega</h1><p>O prazo de entrega e de trinta dias.</p></body></html>"


def _zip(caminho: Path, arquivos: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(caminho, "w") as z:
        for nome, dados in arquivos.items():
            z.writestr(nome, dados)
    return caminho


def _epub(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    return _zip(
        tmp_path / "livro.epub",
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": CONTAINER,
            "OEBPS/content.opf": OPF,
            "OEBPS/nav.xhtml": "<html><body><nav><li>Sumario falso</li></nav></body></html>",
            "OEBPS/cap 1.xhtml": CAP1,
            "OEBPS/cap2.xhtml": CAP2,
            **(extra or {}),
        },
    )


def _secoes(paginas):
    trechos = divisao.dividir(paginas)
    return {t.secao for t in trechos}, " ".join(t.texto for t in trechos)


def test_epub_em_ordem_com_titulos_e_sem_lixo(tmp_path) -> None:
    resultado = extrair(_epub(tmp_path))
    assert all(p.numero is None for p in resultado.paginas)
    secoes, texto = _secoes(resultado.paginas)
    assert "A garantia" in secoes and "A garantia > Exclusoes" in secoes and "A entrega" in secoes
    assert "segredo" not in texto and "Sumario falso" not in texto and "NAO" not in texto
    assert "defeitos por um ano" in texto  # quebra de linha do HTML virou espaço
    assert texto.index("garantia cobre") < texto.index("prazo de entrega")


def test_epub_com_drm_e_recusado(tmp_path) -> None:
    drm = (
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod '
        'Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></EncryptedData></encryption>'
    )
    with pytest.raises(ErroDeExtracao, match="DRM"):
        extrair(_epub(tmp_path, {"META-INF/encryption.xml": drm}))


def test_epub_com_fonte_ofuscada_nao_e_drm(tmp_path) -> None:
    fonte = (
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><EncryptionMethod '
        'Algorithm="http://www.idpf.org/2008/embedding"/></EncryptedData></encryption>'
    )
    assert extrair(_epub(tmp_path, {"META-INF/encryption.xml": fonte})).paginas


def test_zip_que_explode_ao_descompactar(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(formatos, "TETO_DESCOMPACTADO", 100)
    with pytest.raises(ErroDeExtracao, match="grande demais"):
        extrair(_epub(tmp_path))


def test_epub_corrompido(tmp_path) -> None:
    ruim = tmp_path / "ruim.epub"
    ruim.write_bytes(b"PK isso nao e um zip")
    with pytest.raises(ErroDeExtracao, match="corrompido"):
        extrair(ruim)


def test_xml_com_entidade_externa_e_barrado(tmp_path) -> None:
    mal = (
        '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        "<FictionBook><body><section><p>&x;</p></section></body></FictionBook>"
    )
    arquivo = tmp_path / "mal.fb2"
    arquivo.write_text(mal)
    with pytest.raises(ErroDeExtracao, match="inseguro|inválido"):
        extrair(arquivo)


FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><book-title>Contos</book-title></title-info></description>
<body>
 <section><title><p>Parte um</p></title>
   <epigraph><p>Uma epigrafe.</p></epigraph>
   <section><title><p>O farol</p></title><p>O farol acendia ao anoitecer.</p>
     <poem><stanza><v>verso um</v><v>verso dois</v></stanza></poem></section>
 </section>
 <section><title><p>Parte dois</p></title><p>A falesia cedeu.</p></section>
</body>
<body name="notes"><section><title><p>1</p></title><p>nota ignorada</p></section></body>
</FictionBook>"""


def test_fb2_secoes_aninhadas_e_sem_notas(tmp_path) -> None:
    arquivo = tmp_path / "c.fb2"
    arquivo.write_text(FB2, encoding="utf-8")
    secoes, texto = _secoes(extrair(arquivo).paginas)
    assert {
        "Contos",
        "Contos > Parte um",
        "Contos > Parte um > O farol",
        "Contos > Parte dois",
    } <= secoes
    assert "verso dois" in texto and "nota ignorada" not in texto


def test_fbz(tmp_path) -> None:
    arquivo = _zip(tmp_path / "c.fbz", {"c.fb2": FB2})
    assert "farol" in _secoes(extrair(arquivo).paginas)[1]


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(tmp_path: Path, com_estilos: bool = True) -> Path:
    estilos = (
        f'<w:styles xmlns:w="{W}">'
        '<w:style w:styleId="Ttulo1"><w:name w:val="heading 1"/></w:style>'
        '<w:style w:styleId="Ttulo2"><w:name w:val="heading 2"/></w:style>'
        '<w:style w:styleId="Corpo"><w:name w:val="Normal"/></w:style></w:styles>'
    )

    def p(texto, estilo=None):
        e = f'<w:pPr><w:pStyle w:val="{estilo}"/></w:pPr>' if estilo else ""
        return f"<w:p>{e}<w:r><w:t>{texto}</w:t></w:r></w:p>"

    corpo = (
        p("Garantia", "Ttulo1" if com_estilos else None)
        + p("A garantia cobre defeitos por um ano.")
        + p("Prazos", "Ttulo2" if com_estilos else None)
        + "<w:tbl><w:tr><w:tc>"
        + p("Entrega")
        + "</w:tc><w:tc>"
        + p("30 dias")
        + "</w:tc></w:tr></w:tbl>"
    )
    return _zip(
        tmp_path / "d.docx",
        {
            "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{corpo}</w:body></w:document>',
            "word/styles.xml": estilos,
        },
    )


def test_docx_titulos_por_estilo_e_tabela(tmp_path) -> None:
    secoes, texto = _secoes(extrair(_docx(tmp_path)).paginas)
    assert {"Garantia", "Garantia > Prazos"} <= secoes
    assert "Entrega | 30 dias" in texto


def test_docx_sem_titulos_cai_nas_heuristicas(tmp_path) -> None:
    paginas = extrair(_docx(tmp_path, com_estilos=False)).paginas
    assert "garantia cobre" in paginas[0].texto


def test_docx_sem_documento(tmp_path) -> None:
    with pytest.raises(ErroDeExtracao, match="document.xml"):
        extrair(_zip(tmp_path / "x.docx", {"a.txt": "oi"}))


def test_odt(tmp_path) -> None:
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    )
    conteudo = (
        f"<office:document-content {ns}><office:body><office:text>"
        '<text:h text:outline-level="1">Entrega</text:h>'
        "<text:p>O prazo e de trinta dias.</text:p></office:text></office:body>"
        "</office:document-content>"
    )
    secoes, texto = _secoes(extrair(_zip(tmp_path / "a.odt", {"content.xml": conteudo})).paginas)
    assert "Entrega" in secoes and texto.count("Entrega") == 1


def test_html(tmp_path) -> None:
    arquivo = tmp_path / "a.html"
    arquivo.write_bytes("<h1>Garantia</h1><p>Cobre defeitos por um ano.</p>".encode("cp1252"))
    secoes, _ = _secoes(extrair(arquivo).paginas)
    assert "Garantia" in secoes


def test_mobi_converte_e_limpa_a_pasta_temporaria(tmp_path, monkeypatch) -> None:
    pasta = tmp_path / "saida"
    pasta.mkdir()
    epub = _epub(pasta)
    import mobi

    monkeypatch.setattr(mobi, "extract", lambda _: (str(pasta), str(epub)))
    arquivo = tmp_path / "a.mobi"
    arquivo.write_bytes(b"x" * 60 + b"BOOKMOBI")
    assert "defeitos por um ano" in _secoes(extrair(arquivo).paginas)[1]
    assert not pasta.exists()


def test_mobi_com_falha_da_biblioteca(tmp_path, monkeypatch) -> None:
    import mobi

    def quebra(_):
        raise RuntimeError("DRM")

    monkeypatch.setattr(mobi, "extract", quebra)
    arquivo = tmp_path / "a.mobi"
    arquivo.write_bytes(b"x" * 60 + b"BOOKMOBI")
    with pytest.raises(ErroDeExtracao, match="DRM"):
        extrair(arquivo)


# --- DjVu (só roda com o djvulibre instalado) --------------------------------------------------

precisa_de_djvulibre = pytest.mark.skipif(
    not all(shutil.which(x) for x in ("djvutxt", "cjb2", "djvm", "djvused")),
    reason="djvulibre-bin não instalado",
)


def _djvu(tmp_path: Path, paginas: list[str | None]) -> Path:
    partes = []
    for i, texto in enumerate(paginas):
        pbm = tmp_path / f"p{i}.pbm"
        pbm.write_text("P1\n100 100\n" + ("0 " * 100 + "\n") * 100)
        djvu = tmp_path / f"p{i}.djvu"
        subprocess.run(["cjb2", str(pbm), str(djvu)], check=True)
        if texto:
            camada = tmp_path / f"p{i}.txt"
            camada.write_text(f'(page 0 0 100 100 (line 0 0 100 20 "{texto}"))')
            subprocess.run(
                ["djvused", str(djvu), "-e", f"select 1; set-txt {camada}", "-s"], check=True
            )
        partes.append(str(djvu))
    livro = tmp_path / "livro.djvu"
    subprocess.run(["djvm", "-c", str(livro), *partes], check=True)
    return livro


@precisa_de_djvulibre
def test_djvu_com_paginas_e_aviso_de_pagina_sem_texto(tmp_path) -> None:
    resultado = extrair(
        _djvu(tmp_path, ["A garantia cobre defeitos por um ano.", None, "O prazo."])
    )
    assert [p.numero for p in resultado.paginas] == [1, 3] and resultado.total_paginas == 3
    assert "1 de 3 páginas sem texto" in resultado.aviso


@precisa_de_djvulibre
def test_djvu_escaneado_sem_camada_de_texto(tmp_path) -> None:
    with pytest.raises(ErroDeExtracao, match="Nenhum texto"):
        extrair(_djvu(tmp_path, [None, None]))


def test_djvu_sem_o_programa(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    arquivo = tmp_path / "a.djvu"
    arquivo.write_bytes(b"AT&TFORM....")
    with pytest.raises(ErroDeExtracao, match="djvulibre"):
        extrair(arquivo)


# --- assinaturas e lista de extensões ----------------------------------------------------------


def test_extensoes_aceitas() -> None:
    assert {".epub", ".fb2", ".docx", ".mobi", ".djvu", ".pdf", ".txt"} <= EXTENSOES_ACEITAS


@pytest.mark.parametrize(
    ("nome", "conteudo"),
    [
        ("a.epub", b"texto puro"),
        ("a.docx", b"%PDF-1.4"),
        ("a.mobi", b"curto"),
        ("a.djvu", b"%PDF-1.4"),
        ("a.fb2", b"<html></html>"),
        ("a.pdf", b"PK\x03\x04"),
    ],
)
def test_assinatura_errada_e_recusada(nome, conteudo) -> None:
    with pytest.raises(ValueError, match="válido|parece"):
        conferir_assinatura(nome, conteudo)


def test_assinaturas_certas_passam() -> None:
    conferir_assinatura("a.epub", b"PK\x03\x04...")
    conferir_assinatura("a.djvu", b"AT&TFORM\x00")
    conferir_assinatura("a.mobi", b"x" * 60 + b"BOOKMOBI")
    conferir_assinatura("a.fb2", b'<?xml version="1.0"?><FictionBook>')
    conferir_assinatura("a.txt", b"qualquer coisa")


def test_zip_em_memoria_e_lido_por_nome(tmp_path) -> None:  # garante que o buffer não é exigido
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", "<a/>")
    assert buf.getvalue().startswith(b"PK")


def test_fb2_em_utf16_e_com_comentario_longo_passam_na_conferencia() -> None:
    utf16 = '<?xml version="1.0" encoding="utf-16"?><FictionBook/>'.encode("utf-16")
    conferir_assinatura("a.fb2", utf16)
    longo = b'<?xml version="1.0"?><!--' + b"x" * 20000 + b"--><fictionbook></fictionbook>"
    conferir_assinatura("a.fb2", longo)
    with pytest.raises(ValueError, match="FB2"):
        conferir_assinatura("a.fb2", b"<html><body>oi</body></html>")


def test_fb2_zip_e_tratado_como_fbz(tmp_path) -> None:
    from app.ingestao.extracao import extensao_do_nome

    assert extensao_do_nome("Livro.FB2.ZIP") == ".fbz" and extensao_do_nome("a.epub") == ".epub"
    conferir_assinatura("livro.fb2.zip", b"PK\x03\x04")
    arquivo = _zip(tmp_path / "livro.fbz", {"c.fb2": FB2})
    assert "farol" in _secoes(extrair(arquivo).paginas)[1]
    copia = tmp_path / "livro.fb2.zip"
    copia.write_bytes(arquivo.read_bytes())
    assert extrair(copia).paginas
