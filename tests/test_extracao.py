import pytest
from auxiliares import pdf_com_texto
from pypdf import PdfWriter

from app.ingestao.extracao import ErroDeExtracao, Pagina, extrair_paginas, limpar


def test_limpar_remove_nul_e_ruido_mas_mantem_paragrafos() -> None:
    sujo = "Olá\x00  mundo \r\n\r\n\r\n\r\n  segundo   parágrafo  "
    assert limpar(sujo) == "Olá mundo\n\nsegundo parágrafo"


def test_texto_utf8(tmp_path) -> None:
    arquivo = tmp_path / "a.txt"
    arquivo.write_text("Ação e coração", encoding="utf-8")
    assert extrair_paginas(arquivo) == [Pagina(None, "Ação e coração")]


def test_texto_latin1_cai_no_plano_b(tmp_path) -> None:
    arquivo = tmp_path / "a.txt"
    arquivo.write_bytes("Ação".encode("latin-1"))
    assert extrair_paginas(arquivo)[0].texto == "Ação"


def test_markdown(tmp_path) -> None:
    arquivo = tmp_path / "a.md"
    arquivo.write_text("# Título\n\ncorpo", encoding="utf-8")
    assert extrair_paginas(arquivo)[0].texto.startswith("# Título")


def test_pdf_numera_as_paginas_e_ignora_as_vazias(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["primeira pagina", "", "terceira pagina"]))
    paginas = extrair_paginas(arquivo)
    assert [(p.numero, p.texto) for p in paginas] == [
        (1, "primeira pagina"),
        (3, "terceira pagina"),
    ]


def test_pdf_sem_texto_e_recusado_com_dica_de_ocr(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["", ""]))
    with pytest.raises(ErroDeExtracao, match="OCR"):
        extrair_paginas(arquivo)


def test_pdf_corrompido_vira_erro_de_extracao(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(b"%PDF-1.4\nisto nao e um pdf de verdade")
    with pytest.raises(ErroDeExtracao):
        extrair_paginas(arquivo)


def test_pdf_acima_do_limite_de_paginas(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["x"] * 3))
    with pytest.raises(ErroDeExtracao, match="limite"):
        extrair_paginas(arquivo, max_paginas=2)


def test_pdf_protegido_por_senha(tmp_path) -> None:
    escritor = PdfWriter()
    escritor.add_blank_page(100, 100)
    escritor.encrypt("segredo")
    arquivo = tmp_path / "a.pdf"
    with arquivo.open("wb") as f:
        escritor.write(f)
    with pytest.raises(ErroDeExtracao, match="senha"):
        extrair_paginas(arquivo)


def test_extensao_nao_suportada(tmp_path) -> None:
    arquivo = tmp_path / "a.exe"
    arquivo.write_bytes(b"MZ")
    with pytest.raises(ErroDeExtracao, match="não suportado"):
        extrair_paginas(arquivo)


def test_numero_da_pagina_no_topo_ou_no_pe_sai_quando_bate_com_a_pagina() -> None:
    from app.ingestao.extracao import remover_numero_de_pagina

    assert remover_numero_de_pagina("2\n\nCapítulo 1\n\ntexto", 2) == "Capítulo 1\n\ntexto"
    assert remover_numero_de_pagina("texto\n\n57", 57) == "texto"


def test_numero_que_nao_e_o_da_pagina_fica() -> None:
    from app.ingestao.extracao import remover_numero_de_pagina

    assert remover_numero_de_pagina("3\n\ntexto", 2) == "3\n\ntexto"
    assert remover_numero_de_pagina("texto com 2 no meio", 2) == "texto com 2 no meio"


def test_juntar_linhas_desfaz_a_quebra_do_layout_mas_mantem_paragrafos() -> None:
    from app.ingestao.extracao import juntar_linhas

    texto = "Digamos, aqui no topo,\nna música erudita.\n\nOutro bloco\ncom duas linhas."
    assert juntar_linhas(texto) == (
        "Digamos, aqui no topo, na música erudita.\n\nOutro bloco com duas linhas."
    )


def test_juntar_linhas_cola_a_palavra_hifenizada() -> None:
    from app.ingestao.extracao import juntar_linhas

    assert juntar_linhas("o desconhe-\ncido arde") == "o desconhecido arde"
    assert juntar_linhas("um guarda-\nChuva") == "um guarda- Chuva"  # maiúscula: não é hifenização


def test_juntar_linhas_preserva_as_falas_do_dialogo() -> None:
    from app.ingestao.extracao import juntar_linhas

    texto = "Ela olhou para ele e disse:\n— Você veio.\n— Vim.\nE ficou calada\nolhando o mar."
    # Cada fala fica na sua linha. Sem recuo no PDF, a narração depois da última fala não se
    # distingue de uma continuação, então ela se junta à fala: perde-se só a quebra de linha.
    assert juntar_linhas(texto) == (
        "Ela olhou para ele e disse:\n— Você veio.\n— Vim. E ficou calada olhando o mar."
    )


def test_pdf_aplica_as_duas_limpezas(tmp_path) -> None:
    arquivo = tmp_path / "a.pdf"
    arquivo.write_bytes(pdf_com_texto(["um", "texto qualquer"]))  # página 1 é só o número "um"
    assert [p.texto for p in extrair_paginas(arquivo)] == ["um", "texto qualquer"]
