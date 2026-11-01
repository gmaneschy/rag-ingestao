"""Limpeza da ingestão: normalização de texto, cabeçalhos correntes e títulos falsos.

Reproduz, em miniatura, o que as amostras dos livros reais mostraram: "98 The Case of Alexander
Blok" virando seção de 559 trechos, nota de rodapé virando seção, carimbo de biblioteca como
título e ligaduras ("ﬁn de siècle") que a busca não casa.
"""

from app.ingestao import cabecalhos, secoes
from app.ingestao.extracao import _montar_paginas, limpar, normalizar_texto
from app.ingestao.secoes import titulo_da_linha

# ---------- normalização ----------


def test_ligaduras_viram_letras() -> None:
    assert normalizar_texto("transﬁgure o ﬁn de siècle, ofﬁce ﬂow") == (
        "transfigure o fin de siècle, office flow"
    )


def test_hifen_suave_e_espacos_de_ocr() -> None:
    assert normalizar_texto("господство­ вало") == "господствовало"
    assert normalizar_texto("pala­\nvra") == "palavra"
    assert normalizar_texto("Russia’ s advantage, century , these, law .") == (
        "Russia’s advantage, century, these, law."
    )


def test_o_que_nao_e_defeito_fica_como_esta() -> None:
    assert normalizar_texto("1. Item . . . 2. Outro") == "1. Item . . . 2. Outro"
    assert normalizar_texto("Preço: R$ 10 , 50") == "Preço: R$ 10 , 50"  # só depois de letra
    assert normalizar_texto("Reticências … e “aspas”") == "Reticências … e “aspas”"


def test_limpar_aplica_a_normalizacao() -> None:
    assert limpar("o ﬁm\r\nda Russia’ s era") == "o fim\nda Russia’s era"


# ---------- cabeçalhos correntes ----------


def _corpo(n: int, linhas: int = 7) -> str:
    """Linhas diferentes em cada página, com dígitos no meio (não nas pontas)."""
    return "\n".join(f"Linha a{n}b{i}c com conteudo proprio da pagina." for i in range(linhas))


def _livro(paginas_por_capitulo: int = 10, capitulos: int = 3) -> list[tuple[int, str]]:
    """Capítulos com cabeçalho "N Título" (verso) e "Título N" (recto), 3 dígitos incluídos."""
    textos = []
    pdf = 0
    for c in range(capitulos):
        titulo = f"Capitulo numero {['um', 'dois', 'tres'][c]} do livro"
        for _ in range(paginas_por_capitulo):
            pdf += 1
            impresso = pdf + 90  # 91.. : passa de 100 sem formar o padrão de dois dígitos
            corpo = _corpo(pdf)
            cabecalho = f"{impresso} {titulo}" if pdf % 2 else f"{titulo} {impresso}"
            textos.append((pdf, f"{cabecalho}\n{corpo}"))
    return textos


def test_cabecalho_com_numero_de_pagina_sai_do_texto() -> None:
    resultado = cabecalhos.remover_corridos(_livro())
    assert resultado.removidas == 30
    assert all("Capitulo numero" not in texto for _, texto in resultado.textos)
    assert all("Linha" in texto for _, texto in resultado.textos)


def test_o_cabecalho_do_capitulo_vira_inicio_de_secao() -> None:
    resultado = cabecalhos.remover_corridos(_livro())
    assert {p: t.texto for p, t in resultado.titulos.items()} == {
        1: "Capitulo numero um do livro",
        11: "Capitulo numero dois do livro",
        21: "Capitulo numero tres do livro",
    }
    assert {t.nivel for t in resultado.titulos.values()} == {1}


def test_cabecalho_em_todas_as_paginas_e_titulo_do_livro_nao_secao() -> None:
    textos = [
        (
            n,
            f"Titulo do Livro {n}\n"
            + "\n".join(f"Linha {n}-{i} do corpo da pagina." for i in range(7)),
        )
        for n in range(1, 21)
    ]
    resultado = cabecalhos.remover_corridos(textos)
    assert resultado.removidas == 20 and resultado.titulos == {}


def test_carimbo_sem_numero_sai_mas_nao_vira_secao() -> None:
    textos = []
    for n in range(1, 31):
        corpo = _corpo(n)
        carimbo = "\nBURLINGAME PUBLIC LIBRARY" if n % 5 == 0 else ""
        textos.append((n, corpo + carimbo))
    resultado = cabecalhos.remover_corridos(textos)
    assert resultado.removidas == 6 and resultado.titulos == {}
    assert all("BURLINGAME" not in t for _, t in resultado.textos)


def test_numero_de_pagina_solto_diferente_do_indice_sai() -> None:
    textos = [(n, f"{n + 14}\n" + _corpo(n)) for n in range(1, 21)]
    resultado = cabecalhos.remover_corridos(textos)
    assert resultado.removidas == 20
    assert not any(t.split("\n")[0].isdigit() for _, t in resultado.textos)


def test_numero_solto_ocasional_e_conteudo() -> None:
    textos = [(n, _corpo(n) + f" {n}") for n in range(1, 21)]
    textos[3] = (4, "12\n" + _corpo(4))  # um "12" no topo de uma só página: pode ser um capítulo
    assert cabecalhos.remover_corridos(textos).removidas == 0


def test_pagina_com_pouco_texto_nunca_e_apagada() -> None:
    textos = [(n, "Texto da pagina com palavras suficientes.") for n in range(1, 8)]
    resultado = cabecalhos.remover_corridos(textos)
    assert resultado.removidas == 0 and len(resultado.textos) == 7


def test_titulo_de_capitulo_repetido_nao_e_cabecalho() -> None:
    textos = [(n * 10, f"Capítulo {n}\n{_corpo(n)}") for n in range(1, 9)]
    assert cabecalhos.remover_corridos(textos).removidas == 0


def test_o_cabecalho_deduzido_entra_nas_paginas_montadas() -> None:
    resultado = cabecalhos.remover_corridos(_livro())
    paginas = _montar_paginas(resultado.textos, {}, resultado.titulos)
    assert [t.texto for t in paginas[0].titulos] == ["Capitulo numero um do livro"]
    assert paginas[1].titulos == ()
    assert [t.texto for t in paginas[10].titulos] == ["Capitulo numero dois do livro"]


# ---------- títulos falsos ----------


def test_nota_de_rodape_e_data_nao_sao_titulo() -> None:
    assert (
        titulo_da_linha("18. Andrei Bely , Mezhdu dvukh revoliutsii(Moscow: Khudozhestvennaia lit-")
        is None
    )
    assert titulo_da_linha('3. Valery Bryusov, "The Meeting."') is None
    assert titulo_da_linha("4. Chukovsky, Voprosy Literatury 1 (1972): 156") is None
    assert titulo_da_linha("13 March 1919") is None
    assert titulo_da_linha("86. Ibid., 202–5") is None


def test_titulo_numerado_de_verdade_continua_titulo() -> None:
    titulo = titulo_da_linha("3. Voloshin and the Modernist Problem of the Ugly Poetess")
    assert titulo is not None and titulo.origem == "decimal"


def _pagina(*linhas: str) -> str:
    return "\n".join([*linhas, "Texto corrido da pagina, que segue sem ser titulo nenhum."])


def test_sumario_nao_gera_secoes() -> None:
    sumario = _pagina(*(f"Chapter {n}" for n in range(1, 8)))
    corpo = _pagina("Chapter 3")
    marcas = secoes.detectar_no_texto([sumario, corpo])
    assert marcas[0] == []  # a página de sumário inteira é lista, não título
    assert [t.texto for _, t in marcas[1]] == ["Chapter 3"]


def test_pagina_de_notas_nao_gera_secoes() -> None:
    notas = _pagina(
        '3. Valery Bryusov, "The Meeting."', '1. Nikolai Nekrasov, "The Philanthropist."',
        '6. Evgeny Baratynsky, "Piroskaf" (The Steamer).', '8. Nikolai Nekrasov, "The Funeral."',
    )  # fmt: skip
    assert secoes.detectar_no_texto([notas, _pagina("Corpo")])[0] == []


def test_romanos_viram_capitulo_so_se_houver_varios() -> None:
    paginas = [_pagina(r) for r in ("II", "III", "IV", "V")]
    achados = secoes.detectar_no_texto(paginas)
    assert [t.texto for da in achados for _, t in da] == ["II", "III", "IV", "V"]
    assert all(t.origem == "romano" for da in achados for _, t in da)

    so_um = secoes.detectar_no_texto([_pagina("I"), _pagina(), _pagina()])
    assert [t for da in so_um for t in da] == []


def test_romano_com_menos_de_tres_e_ignorado_inclusive_em_texto_corrido() -> None:
    # Um "I" que a quebra de linha deixou sozinho não pode abrir uma seção.
    achados = secoes.detectar_no_texto(["Frase longa que termina em\nI\nsegue a frase."])
    assert achados == [[]]


def test_documento_de_uma_pagina_so_mantem_todos_os_titulos() -> None:
    # TXT e Markdown chegam como uma "página" só: a regra de sumário não pode valer aqui.
    texto = "\n".join(f"## Seção {n}\n\nTexto da seção {n}." for n in range(1, 8))
    assert len(secoes.detectar_no_texto([texto])[0]) == 7


def test_titulos_falsos_de_livro_real() -> None:
    from app.ingestao.secoes import titulo_da_linha

    for linha in (
        "3 Henrietta Street",
        "chapter 7).",
        "36. Charles Bernheimer, Figures of Ill Repute: Representing Prostitution in",
        "35. In Jung’s Neoplatonic psychomythology, the syzygy is an archetypal",
        "BURLINGAME PUBLIC LIBRARY",
        "486 PRIMROSE ROAD",
        "26. Andrei Bely wrote in Arabesques that Przybyszewski’s heroes associate",
        "47. Rozanov had an amorous correspondence with Liudmila Vil’kina. He",
    ):
        assert titulo_da_linha(linha) is None, linha
    assert titulo_da_linha("2.1 Métodos de coleta") is not None


def test_cabecalho_no_meio_da_pagina_tambem_sai() -> None:
    from app.ingestao.cabecalhos import remover_corridos

    def pagina(n: int) -> str:
        meio = f"Texto da página {chr(97 + n)}{chr(98 + n)} sobre assunto distinto número {n * 7}"
        linhas = [f"Frase única {n}a de conteúdo", "Outra frase " + "x" * n, meio]
        linhas += [
            f"Mais conteúdo diferente {n}0 aqui",
            f"{n + 20} Lev Tolstoy as Early Modernist",
        ]
        linhas += ["Fecho da página " + "y" * n]
        return "\n".join(linhas)

    corrido = remover_corridos([(n, pagina(n)) for n in range(1, 8)])
    assert all("Early Modernist" not in t for _, t in corrido.textos)
