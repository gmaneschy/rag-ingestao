"""Markdown das respostas do modelo: formatação, citações e segurança."""

import pytest

from app.web.formato import texto_citado


def _html(texto: str, prefixo: str = "fonte") -> str:
    return str(texto_citado(texto, prefixo))


def test_negrito_e_lista_como_o_modelo_escreve() -> None:
    html = _html("Pontos:\n\n* **Transformação do Mundo:** a ideia [2].\n* **Amor:** outra [5].")
    assert "<strong>Transformação do Mundo:</strong>" in html
    assert html.count("<li>") == 2 and "<ul>" in html
    assert 'href="#fonte-2"' in html and 'href="#fonte-5"' in html


def test_citacao_multipla_vira_um_link_por_numero() -> None:
    html = _html("fato [1, 3]")
    assert 'href="#fonte-1"' in html and 'href="#fonte-3"' in html and "[<a" in html


def test_prefixo_separa_as_respostas_da_comparacao() -> None:
    assert 'href="#r2-4"' in _html("x [4]", "r2")


def test_citacao_dentro_de_codigo_nao_vira_link() -> None:
    html = _html("use `lista[1]` ou\n\n```\nv[2]\n```\n\nmas [3] sim")
    assert "<code>lista[1]</code>" in html and "v[2]" in html
    assert html.count('class="citacao"') == 1 and 'href="#fonte-3"' in html


def test_tabela_e_quebra_de_linha() -> None:
    html = _html("| a | b |\n|---|---|\n| 1 | 2 |\n\nlinha um\nlinha dois")
    assert "<table>" in html and "<th>a</th>" in html
    assert "linha um<br" in html


@pytest.mark.parametrize(
    "malicioso",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        "<iframe src='//evil'></iframe>",
        '<a href="javascript:alert(1)">x</a>',
    ],
)
def test_html_cru_do_modelo_vira_texto(malicioso: str) -> None:
    html = _html(f"antes {malicioso} depois [1]")
    assert "<script" not in html and "<img" not in html and "<iframe" not in html
    assert '<a href="javascript' not in html and "&lt;" in html


def test_link_javascript_em_markdown_nao_e_linkado() -> None:
    html = _html("[clique](javascript:alert(1)) e [ok](https://exemplo.com)")
    assert 'href="javascript' not in html and 'href="https://exemplo.com"' in html


def test_vazio_e_none() -> None:
    assert _html("") == "" and str(texto_citado(None)) == ""
