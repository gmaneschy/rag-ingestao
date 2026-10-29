"""Seções, avisos e filtros de ponta a ponta (HTTP até o banco)."""

# ruff: noqa: F811

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
GUIA = (
    "# Guia\n\nEste guia explica o produto inteiro.\n\n"
    "## Instalação\n\nBaixe o pacote e execute o instalador no computador.\n\n"
    "## Garantia\n\nA garantia cobre defeitos de fabricação por um ano."
).encode()


@pytest.fixture
def cliente(banco):
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _enviar(cliente, nome, conteudo, colecao="geral") -> int:
    r = cliente.post(
        "/documentos", files={"arquivo": (nome, conteudo)}, data={"colecao": colecao}, headers=CHAVE
    )
    assert r.status_code in (200, 202), r.text
    return r.json()["id"]


def _perguntar(cliente, pergunta="Qual é a garantia?", **extra):
    r = cliente.post("/perguntas", json={"pergunta": pergunta, **extra}, headers=CHAVE)
    assert r.status_code == 200, r.text
    return r.json()


def test_secoes_do_documento(cliente) -> None:
    doc = _enviar(cliente, "guia.md", GUIA)
    corpo = cliente.get(f"/documentos/{doc}/secoes", headers=CHAVE).json()
    assert [s["secao"] for s in corpo["secoes"]] == [
        "Guia",
        "Guia > Instalação",
        "Guia > Garantia",
    ]
    assert corpo["trechos_sem_secao"] == 0
    assert all(s["trechos"] >= 1 for s in corpo["secoes"])

    detalhe = cliente.get(f"/documentos/{doc}?amostra=3", headers=CHAVE).json()
    assert detalhe["secoes"] == 3 and detalhe["aviso"] is None
    assert {t["secao"] for t in detalhe["amostra"]} == {
        "Guia",
        "Guia > Instalação",
        "Guia > Garantia",
    }
    assert cliente.get("/documentos/999/secoes", headers=CHAVE).status_code == 404
    assert cliente.get(f"/documentos/{doc}/secoes").status_code == 401


def test_documento_sem_estrutura_nao_tem_secoes(cliente) -> None:
    doc = _enviar(cliente, "a.txt", b"Texto corrido sem nenhum titulo, so palavras soltas.")
    corpo = cliente.get(f"/documentos/{doc}/secoes", headers=CHAVE).json()
    assert corpo["secoes"] == [] and corpo["trechos_sem_secao"] == 1


def test_aviso_de_pagina_sem_texto_aparece_no_detalhe_e_na_lista(cliente) -> None:
    doc = _enviar(cliente, "a.pdf", pdf_com_texto(["Texto da primeira pagina.", "", "Terceira."]))
    detalhe = cliente.get(f"/documentos/{doc}", headers=CHAVE).json()
    assert detalhe["estado"] == "processado"
    assert detalhe["aviso"].startswith("1 de 3 páginas sem texto")
    assert cliente.get("/documentos", headers=CHAVE).json()["itens"][0]["aviso"] == detalhe["aviso"]


def test_filtro_por_secao(cliente) -> None:
    _enviar(cliente, "guia.md", GUIA)
    todas = _perguntar(cliente, "O que o produto faz? Baixe, instale, garantia", k=10)
    assert {f["secao"] for f in todas["trechos_usados"]} >= {"Guia > Instalação", "Guia > Garantia"}

    so_garantia = _perguntar(
        cliente, "O que o produto faz? Baixe, instale, garantia", k=10, secao="garantia"
    )
    assert so_garantia["trechos_usados"]
    assert {f["secao"] for f in so_garantia["trechos_usados"]} == {"Guia > Garantia"}


def test_filtro_por_faixa_de_paginas(cliente) -> None:
    paginas = [f"Pagina {i} fala do prazo de entrega numero {i}." for i in range(1, 6)]
    _enviar(cliente, "livro.pdf", pdf_com_texto(paginas))
    r = _perguntar(cliente, "prazo de entrega", k=10, pagina_de=2, pagina_ate=3)
    assert sorted(f["pagina"] for f in r["trechos_usados"]) == [2, 3]
    r = _perguntar(cliente, "prazo de entrega", k=10, pagina_de=4)
    assert sorted(f["pagina"] for f in r["trechos_usados"]) == [4, 5]


def test_faixa_de_paginas_invertida_e_recusada(cliente) -> None:
    r = cliente.post(
        "/perguntas",
        json={"pergunta": "qualquer coisa", "pagina_de": 5, "pagina_ate": 2},
        headers=CHAVE,
    )
    assert r.status_code == 422


def test_aviso_quando_a_colecao_nao_existe(cliente) -> None:
    _enviar(cliente, "guia.md", GUIA)
    r = _perguntar(cliente, colecoes=["nao-existe", "geral"])
    assert r["aviso"] is None  # "geral" existe: nada a avisar
    r = _perguntar(cliente, colecoes=["nao-existe"])
    assert r["recusou"] is True
    assert r["aviso"] == "Coleção sem documentos: nao-existe."


def test_aviso_quando_a_secao_nao_existe(cliente) -> None:
    _enviar(cliente, "guia.md", GUIA)
    r = _perguntar(cliente, secao="capítulo 99")
    assert r["recusou"] is True
    assert r["aviso"] == "Nenhum trecho em seção que contenha 'capítulo 99'."


def test_secao_com_curinga_do_like_e_tratada_como_texto(cliente) -> None:
    _enviar(cliente, "guia.md", GUIA)
    # "%" não pode casar com tudo: a seção digitada é literal.
    r = _perguntar(cliente, secao="%")
    assert r["trechos_usados"] == []


def test_reprocessar_atualiza_as_secoes(cliente, banco) -> None:
    doc = _enviar(cliente, "guia.md", GUIA)
    with banco() as s:
        s.execute(text("UPDATE trecho SET secao = NULL"))
        s.commit()
    assert cliente.post(f"/documentos/{doc}/reprocessar", headers=CHAVE).status_code == 202
    corpo = cliente.get(f"/documentos/{doc}/secoes", headers=CHAVE).json()
    assert len(corpo["secoes"]) == 3
