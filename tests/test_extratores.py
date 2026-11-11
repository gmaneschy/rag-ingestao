"""Comparação de extratores de PDF: amostra de páginas, medidas e relatório."""

import pytest
from auxiliares import pdf_com_texto

from app.avaliacao import extratores

pytest.importorskip("pdfplumber")
pytest.importorskip("pymupdf")


def test_amostra_espalha_as_paginas() -> None:
    assert extratores.paginas_da_amostra(5, 40) == [1, 2, 3, 4, 5]
    amostra = extratores.paginas_da_amostra(200, 10)
    assert len(amostra) == 10 and amostra[0] == 1 and amostra[-1] > 150


def test_medidas_de_concordancia() -> None:
    assert extratores.palavras_em_comum("a b c", "c b a") == 1.0
    assert extratores.mesma_ordem("a b c", "c b a") < 0.5
    assert extratores.mesma_ordem("um dois três", "um dois três") == 1.0


def test_compara_os_tres_extratores(tmp_path) -> None:
    pasta = tmp_path / "pdfs"
    pasta.mkdir()
    (pasta / "manual.pdf").write_bytes(
        pdf_com_texto(
            ["A garantia cobre defeitos por um ano.", "O prazo de entrega é de trinta dias."]
        )
    )
    texto = extratores.executar(pasta, list(extratores.EXTRATORES), amostra=5, limite_docs=None)
    assert "| pypdf |" in texto and "| pdfplumber |" in texto and "| pymupdf |" in texto
    assert "pypdf × pdfplumber" in texto and "manual.pdf" in texto
    assert "prazo" in texto  # o trecho lado a lado


def test_documento_que_falha_nao_derruba_a_comparacao(tmp_path) -> None:
    pasta = tmp_path / "pdfs"
    pasta.mkdir()
    (pasta / "quebrado.pdf").write_bytes(b"%PDF-1.4 isto nao e um pdf")
    (pasta / "bom.pdf").write_bytes(pdf_com_texto(["Texto simples de teste."]))
    texto = extratores.executar(pasta, ["pypdf", "pymupdf"], amostra=3, limite_docs=None)
    assert "bom.pdf" in texto
