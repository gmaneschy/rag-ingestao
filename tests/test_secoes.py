"""Detecção de seções: o que é título, o que não é, e como isso chega aos trechos."""

from io import BytesIO

import pytest
from auxiliares import pdf_com_texto
from pypdf import PdfReader, PdfWriter

from app.ingestao import extracao, secoes
from app.ingestao.divisao import dividir
from app.ingestao.extracao import Pagina
from app.ingestao.secoes import Titulo


@pytest.mark.parametrize(
    ("linha", "texto", "nivel", "origem"),
    [
        ("Capítulo 5", "Capítulo 5", 2, "palavra"),
        ("CAPÍTULO V", "CAPÍTULO V", 2, "palavra"),
        ("Capítulo 12 — O retorno", "Capítulo 12 — O retorno", 2, "palavra"),
        ("Capítulo dois", "Capítulo dois", 2, "palavra"),
        ("Parte II", "Parte II", 1, "palavra"),
        ("Seção 2.1", "Seção 2.1", 3, "palavra"),
        ("Chapter 3: The End", "Chapter 3: The End", 2, "palavra"),
        ("Prólogo", "Prólogo", 1, "palavra"),
        ("Introduction", "Introduction", 1, "palavra"),
        ("# Visão geral", "Visão geral", 1, "markdown"),
        ("### Detalhe", "Detalhe", 3, "markdown"),
        ("1.2 Requisitos do sistema", "1.2 Requisitos do sistema", 2, "decimal"),
        ("2 Metodologia", "2 Metodologia", 1, "decimal"),
        ("1.2.3 Subitem profundo", "1.2.3 Subitem profundo", 3, "decimal"),
        ("INTRODUÇÃO GERAL", "INTRODUÇÃO GERAL", 1, "caixa_alta"),
    ],
)
def test_reconhece_titulos(linha: str, texto: str, nivel: int, origem: str) -> None:
    titulo = secoes.titulo_da_linha(linha)
    assert titulo is not None
    assert (titulo.texto, titulo.nivel, titulo.origem) == (texto, nivel, origem)


@pytest.mark.parametrize(
    "linha",
    [
        "",
        "Parte da história continuou depois do jantar.",
        "Capítulo cinco foi longo e difícil para ele.",
        "Parte I do plano",
        "— NÃO!",
        "NÃO!",
        "SIM?",
        "Ele disse: oi",
        "1990 foi um ano bom",
        "10 pessoas vieram",
        "3. Ele chegou às cinco.",
        "O prazo de entrega é de trinta dias úteis.",
        "AB",
        "x" * 200,
    ],
)
def test_nao_confunde_texto_corrido_com_titulo(linha: str) -> None:
    assert secoes.titulo_da_linha(linha) is None


def test_cabecalho_corrido_nao_vira_secao() -> None:
    paginas = [
        f"A FALÉSIA E O FAROL\nTexto da página {i} com palavras de enchimento.\nMais uma linha."
        for i in range(5)
    ]
    assert all(m == [] for m in secoes.detectar_no_texto(paginas))


def test_capitulo_repetido_no_cabecalho_fica_so_na_primeira_pagina() -> None:
    paginas = [
        "Capítulo 5\nTexto 0.",
        "Capítulo 5\nMais texto.",
        "Capítulo 5\nE mais.",
        "Capítulo 5\nFim.",
    ]
    marcas = secoes.detectar_no_texto(paginas)
    assert [len(m) for m in marcas] == [1, 0, 0, 0]


def test_titulo_unico_em_caixa_alta_e_mantido() -> None:
    marcas = secoes.detectar_no_texto(["PRIMEIRA PARTE\nTexto.", "Texto.", "Texto."])
    assert [len(m) for m in marcas] == [1, 0, 0]


# ---------- do texto aos trechos ----------


def test_titulo_fica_em_paragrafo_proprio_e_e_localizado() -> None:
    texto = "Capítulo 1\nO prazo de entrega\né de trinta dias.\n\nA garantia cobre defeitos."
    [pagina] = extracao._montar_paginas([(1, texto)], {})
    assert pagina.texto.split("\n\n")[0] == "Capítulo 1"
    assert "O prazo de entrega é de trinta dias." in pagina.texto  # linhas seguem sendo juntadas
    assert [(t.texto, t.paragrafo) for t in pagina.titulos] == [("Capítulo 1", 0)]


def test_titulo_no_meio_da_pagina() -> None:
    texto = "Fim do capítulo anterior, ainda sem título.\n\nParte II\nComeço da segunda parte."
    [pagina] = extracao._montar_paginas([(7, texto)], {})
    assert [(t.texto, t.paragrafo) for t in pagina.titulos] == [("Parte II", 1)]


def test_secao_acompanha_os_trechos_e_atravessa_paginas() -> None:
    paginas = extracao._montar_paginas(
        [
            (1, "Capítulo 1\nTexto do primeiro capítulo."),
            (2, "Continua o primeiro capítulo, sem título novo."),
            (3, "Capítulo 2\nTexto do segundo capítulo."),
        ],
        {},
    )
    trechos = dividir(paginas, 500, 0)
    assert [(t.pagina, t.secao) for t in trechos] == [
        (1, "Capítulo 1"),
        (2, "Capítulo 1"),
        (3, "Capítulo 2"),
    ]
    assert trechos[0].texto.startswith("Capítulo 1")  # o título entra no texto: a busca o acha


def test_trecho_nunca_mistura_duas_secoes() -> None:
    texto = "# A\n\nTexto curto de A.\n\n# B\n\nTexto curto de B."
    [pagina] = extracao._montar_paginas([(1, texto)], {})
    trechos = dividir([pagina], 500, 0)  # caberia tudo em um só trecho, mas são duas seções
    assert [t.secao for t in trechos] == ["A", "B"]
    assert "Texto curto de B" not in trechos[0].texto


def test_hierarquia_vira_caminho_e_nivel_igual_encerra_o_anterior() -> None:
    def t(texto: str, nivel: int) -> Titulo:
        return Titulo(texto, nivel, "palavra", texto, 0)

    paginas = [
        Pagina(1, "a", (t("Parte I", 1), t("Cap 1", 2))),
        Pagina(2, "b", (t("Cap 2", 2),)),
        Pagina(3, "c", (t("Parte II", 1),)),
        Pagina(4, "d"),
    ]
    assert [x.secao for x in dividir(paginas, 500, 0)] == [
        "Parte I > Cap 1",
        "Parte I > Cap 2",
        "Parte II",
        "Parte II",
    ]


def test_documento_sem_estrutura_fica_sem_secao() -> None:
    trechos = dividir(
        extracao._montar_paginas([(1, "Só texto corrido, sem título algum."), (2, "Mais.")], {}),
        500,
        0,
    )
    assert [t.secao for t in trechos] == [None, None]


# ---------- arquivos de verdade ----------


def _pdf_com_indice(paginas: list[str], indice: list[tuple[str, int, str | None]]) -> bytes:
    """indice: (título, página começando em 0, título do pai ou None)."""
    leitor = PdfReader(BytesIO(pdf_com_texto(paginas)))
    escritor = PdfWriter(clone_from=leitor)
    itens = {}
    for titulo, pagina, pai in indice:
        itens[titulo] = escritor.add_outline_item(titulo, pagina, parent=itens.get(pai))
    saida = BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def test_pdf_com_indice_usa_o_indice(tmp_path) -> None:
    pdf = _pdf_com_indice(
        [f"Texto da pagina {i} com palavras suficientes." for i in range(4)],
        [("Parte I", 0, None), ("Introducao", 0, "Parte I"), ("Metodos", 2, "Parte I")],
    )
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf)
    resultado = extracao.extrair(arquivo)
    trechos = dividir(resultado.paginas, 500, 0)
    assert [(t.pagina, t.secao) for t in trechos] == [
        (1, "Parte I > Introducao"),
        (2, "Parte I > Introducao"),
        (3, "Parte I > Metodos"),
        (4, "Parte I > Metodos"),
    ]
    assert resultado.aviso is None


def test_pdf_com_uma_entrada_so_no_indice_cai_para_o_texto(tmp_path) -> None:
    pdf = _pdf_com_indice(["Texto qualquer da pagina."], [("Capa", 0, None)])
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf)
    [trecho] = dividir(extracao.extrair(arquivo).paginas, 500, 0)
    assert trecho.secao is None


def test_markdown_vira_hierarquia(tmp_path) -> None:
    arquivo = tmp_path / "guia.md"
    arquivo.write_text(
        "# Guia\n\nIntrodução ao guia.\n\n## Instalação\n\nBaixe o pacote.\n\n## Uso\n\nRode.",
        encoding="utf-8",
    )
    trechos = dividir(extracao.extrair(arquivo).paginas, 500, 0)
    assert [t.secao for t in trechos] == ["Guia", "Guia > Instalação", "Guia > Uso"]


def test_aviso_de_paginas_sem_texto(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["Texto da primeira.", "", "Texto da terceira.", ""]))
    resultado = extracao.extrair(arquivo)
    assert [p.numero for p in resultado.paginas] == [1, 3]
    assert resultado.aviso is not None and resultado.aviso.startswith("2 de 4 páginas sem texto")


def test_sem_aviso_quando_todas_as_paginas_tem_texto(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["Texto um.", "Texto dois."]))
    assert extracao.extrair(arquivo).aviso is None
