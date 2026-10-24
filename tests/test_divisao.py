import pytest

from app.ingestao.divisao import dividir
from app.ingestao.extracao import Pagina

LOREM = (
    "A garantia cobre defeitos de fabricação por doze meses. O prazo de entrega é de trinta "
    "dias úteis após a confirmação do pagamento. Reclamações devem ser enviadas por escrito."
)


def test_texto_curto_vira_um_trecho() -> None:
    trechos = dividir([Pagina(1, "Um parágrafo só.")], 500, 80)
    assert [(t.posicao, t.pagina, t.texto) for t in trechos] == [(0, 1, "Um parágrafo só.")]


def test_nenhum_trecho_passa_do_tamanho_nem_fica_vazio() -> None:
    texto = "\n\n".join([LOREM] * 12)
    for t in dividir([Pagina(1, texto)], 300, 50):
        assert 0 < len(t.texto) <= 300


def test_trecho_nunca_atravessa_paginas_e_a_posicao_e_continua() -> None:
    paginas = [Pagina(1, LOREM), Pagina(2, LOREM), Pagina(5, "fim")]
    trechos = dividir(paginas, 120, 20)
    assert [t.posicao for t in trechos] == list(range(len(trechos)))
    assert {t.pagina for t in trechos} == {1, 2, 5}
    ordem = [t.pagina for t in trechos]
    assert ordem == sorted(ordem)
    assert trechos[-1].pagina == 5 and trechos[-1].texto == "fim"


def test_sobreposicao_repete_o_fim_do_trecho_anterior() -> None:
    texto = "\n\n".join(
        f"Parágrafo número {i} com algumas palavras de enchimento." for i in range(8)
    )
    trechos = dividir([Pagina(1, texto)], 200, 40)
    assert len(trechos) > 1
    for anterior, seguinte in zip(trechos, trechos[1:], strict=False):
        ultima_palavra = anterior.texto.split()[-1]
        assert ultima_palavra in seguinte.texto.split("\n\n")[0]


def test_sem_sobreposicao_nao_repete() -> None:
    texto = "\n\n".join(f"Parágrafo {i} " + "palavra " * 10 for i in range(6))
    trechos = dividir([Pagina(None, texto)], 150, 0)
    juntos = "\n\n".join(t.texto for t in trechos)
    assert juntos.count("Parágrafo 3") == 1


def test_paragrafo_gigante_e_quebrado_por_frase() -> None:
    paragrafo = " ".join(f"Esta é a frase número {i}." for i in range(60))
    trechos = dividir([Pagina(1, paragrafo)], 200, 0)
    assert len(trechos) > 1
    assert all(len(t.texto) <= 200 for t in trechos)
    assert all(t.texto.endswith(".") for t in trechos)


def test_palavra_gigante_sofre_corte_duro() -> None:
    trechos = dividir([Pagina(1, "x" * 1000)], 300, 0)
    assert all(len(t.texto) <= 300 for t in trechos)
    assert "".join(t.texto for t in trechos) == "x" * 1000


def test_nenhuma_palavra_se_perde() -> None:
    texto = "\n\n".join([LOREM, LOREM.upper(), LOREM.lower()])
    trechos = dividir([Pagina(1, texto)], 150, 30)
    achadas = {p for t in trechos for p in t.texto.split()}
    assert set(texto.split()) <= achadas


@pytest.mark.parametrize(("tamanho", "sobreposicao"), [(0, 0), (100, 100), (100, -1)])
def test_parametros_invalidos(tamanho: int, sobreposicao: int) -> None:
    with pytest.raises(ValueError):
        dividir([Pagina(1, "a")], tamanho, sobreposicao)
