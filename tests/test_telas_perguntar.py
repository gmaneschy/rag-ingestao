"""Integração: tela Perguntar, avaliação e detalhe da consulta."""

import re

import pytest
from auxiliares import (  # noqa: F401
    ambiente,
    banco,
    pdf_com_texto,
    perguntar_pela_tela,
    precisa_de_banco,
)
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine
from app.llm.tipos import ModeloIndisponivel
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = "chave-de-teste-com-mais-de-16-caracteres"
HX = {"HX-Request": "true"}


@pytest.fixture
def logado(banco):  # noqa: F811
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        assert c.post("/app/entrar", data={"chave": CHAVE}).status_code == 303
        c.app_ref = app
        yield c
    app.dependency_overrides.clear()
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


@pytest.fixture
def manual(logado):
    r = logado.post(
        "/app/documentos/enviar",
        files={
            "arquivos": (
                "manual.pdf",
                pdf_com_texto(
                    [
                        "A garantia cobre defeitos por um ano.",
                        "O prazo de entrega e de trinta dias.",
                    ]
                ),
            )
        },
        headers=HX,
    )
    assert r.status_code == 200
    return logado


def _perguntar(c, pergunta="Qual o prazo de entrega?", **dados):
    return perguntar_pela_tela(c, pergunta, **dados)


def test_exige_sessao(banco) -> None:  # noqa: F811
    with TestClient(create_app(), follow_redirects=False) as c:
        assert c.get("/app/perguntar").status_code == 303
        assert c.post("/app/conversas", data={"pergunta": "oi, tudo bem?"}).status_code == 303


def test_pagina_lista_colecoes_e_documentos(manual) -> None:
    html = manual.get("/app/perguntar").text
    assert "manual.pdf" in html and 'name="colecao"' in html and 'name="modo"' in html


def test_resposta_com_fontes_clicaveis_e_debug(manual) -> None:
    r = _perguntar(manual)
    assert r.status_code == 200
    assert re.search(r'id="t\d+-1"', r.text) and re.search(r'href="#t\d+-1"', r.text)
    assert "manual.pdf" in r.text and "Detalhes técnicos" in r.text
    assert "consulta #" in r.text


def test_pergunta_curta_e_exigencia_de_htmx(manual) -> None:
    assert "Escreva uma pergunta" in _perguntar(manual, pergunta="  ").text
    sem = manual.post("/app/conversas", data={"pergunta": "Qual o prazo?"})
    assert sem.status_code == 403


def test_filtros_do_painel(manual) -> None:
    r = _perguntar(manual, colecao="outra", modo="textual", k="2")
    assert r.status_code == 200 and not re.search(r'id="t\d+-1"', r.text)
    assert _perguntar(manual, k="99").status_code == 422


def test_avaliar_e_detalhe(manual) -> None:
    r = _perguntar(manual)
    cid = re.search(r"/app/consultas/(\d+)", r.text).group(1)
    a = manual.post(f"/app/consultas/{cid}/avaliacao", data={"ajudou": "true"}, headers=HX)
    assert a.status_code == 200 and "ajudou" in a.text
    with get_engine().connect() as c:
        assert c.execute(text("SELECT ajudou FROM consulta WHERE id = :i"), {"i": cid}).scalar()
    d = manual.get(f"/app/consultas/{cid}")
    assert d.status_code == 200 and "Qual o prazo de entrega?" in d.text and "sim" in d.text
    assert (
        manual.post("/app/consultas/9999/avaliacao", data={"ajudou": "1"}, headers=HX).status_code
        == 404
    )
    assert "Não encontrado" in manual.get("/app/consultas/9999").text


def test_detalhe_marca_fonte_removida(manual) -> None:
    cid = re.search(r"/app/consultas/(\d+)", _perguntar(manual).text).group(1)
    doc_id = re.search(r'href="/app/documentos/(\d+)"', manual.get("/app/documentos").text).group(1)
    manual.delete(f"/app/documentos/{doc_id}?consultas=redigir", headers=HX)
    html = manual.get(f"/app/consultas/{cid}").text
    assert "fonte removida" in html and "manual.pdf" not in html.split("Fontes")[1]


def test_erro_do_modelo_vira_mensagem(manual) -> None:
    class Quebrado:
        nome_modelo = "x"

        def responder(self, *a, **k):
            raise ModeloIndisponivel("Ollama fora do ar")

    manual.app_ref.dependency_overrides[obter_gerador] = lambda: Quebrado()
    r = _perguntar(manual)
    assert r.status_code == 200 and "Ollama fora do ar" in r.text


def test_html_da_resposta_e_escapado(manual) -> None:
    from app.web.formato import texto_citado

    assert "<script>" not in texto_citado("<script>x</script> [1]")
    assert 'href="#fonte-2"' in texto_citado("a [1, 2]")
    assert 'href="#t7-2"' in texto_citado("a [1, 2]", prefixo="t7")


def test_diagnostico_aparece_na_resposta_e_na_api(manual) -> None:
    r = _perguntar(manual)
    assert "melhor similaridade" in r.text and "geração" in r.text and "trechos enviados" in r.text
    api = manual.post(
        "/perguntas",
        json={"pergunta": "Qual o prazo de entrega?"},
        headers={"X-API-Key": CHAVE},
    ).json()
    assert api["diagnostico"]["recusa_por"] is None and api["diagnostico"]["ms_busca"] >= 0


def test_recusa_do_portao_diz_quem_recusou(manual) -> None:
    r = _perguntar(manual, pergunta="Zzxq wvvk plomt grnx?", modo="vetorial")
    assert "Recusada pelo" in r.text or "não encontrou nenhum trecho" in r.text
    assert not re.search(r'id="t\d+-1"', r.text)


def test_avaliar_depois_pela_pagina_da_consulta(manual) -> None:
    cid = re.search(r"/app/consultas/(\d+)", _perguntar(manual).text).group(1)
    pagina = manual.get(f"/app/consultas/{cid}").text
    assert "A resposta ajudou?" in pagina
    manual.post(f"/app/consultas/{cid}/avaliacao", data={"ajudou": "false"}, headers=HX)
    assert "não ajudou" in manual.get(f"/app/consultas/{cid}").text
