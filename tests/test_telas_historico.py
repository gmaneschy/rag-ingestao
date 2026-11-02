"""Integração: histórico de consultas e comparação de modelos."""

import re

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine
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
    with TestClient(create_app(), follow_redirects=False) as c:
        assert c.post("/app/entrar", data={"chave": CHAVE}).status_code == 303
        c.post(
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
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _perguntar(c, pergunta="Qual o prazo de entrega?", **d):
    return c.post("/app/perguntar", data={"pergunta": pergunta, **d}, headers=HX)


def _contagem() -> int:
    with get_engine().connect() as c:
        return c.execute(text("SELECT count(*) FROM consulta")).scalar_one()


def test_exige_sessao(banco) -> None:  # noqa: F811
    with TestClient(create_app(), follow_redirects=False) as c:
        for caminho in ("/app/historico", "/app/comparar"):
            assert c.get(caminho).status_code == 303
        assert c.post("/app/comparar/gerar", data={}).status_code == 303


def test_historico_vazio_e_lista(logado) -> None:
    assert "Nenhuma consulta" in logado.get("/app/historico").text
    _perguntar(logado)
    _perguntar(logado, pergunta="Qual a garantia?", tipo="analise")
    html = logado.get("/app/historico").text
    assert (
        "Qual o prazo de entrega?" in html and "Qual a garantia?" in html and "2 consultas" in html
    )
    assert "Qual a garantia?" not in logado.get("/app/historico?tipo=factual").text
    assert "Qual o prazo" in logado.get("/app/historico?q=prazo").text
    assert "Nenhuma consulta com esse filtro" in logado.get("/app/historico?q=zzzz").text


def test_historico_filtra_avaliacao_e_fonte_removida(logado) -> None:
    cid = re.search(r"/app/consultas/(\d+)", _perguntar(logado).text).group(1)
    logado.post(f"/app/consultas/{cid}/avaliacao", data={"ajudou": "false"}, headers=HX)
    assert "Qual o prazo" in logado.get("/app/historico?avaliacao=nao").text
    assert "Nenhuma" in logado.get("/app/historico?avaliacao=sim").text
    assert "Nenhuma" in logado.get("/app/historico?situacao=fonte_removida").text
    doc = re.search(r'href="/app/documentos/(\d+)"', logado.get("/app/documentos").text).group(1)
    logado.delete(f"/app/documentos/{doc}?consultas=redigir", headers=HX)
    html = logado.get("/app/historico?situacao=fonte_removida").text
    assert "Qual o prazo" in html and "removida" in html


def test_filtro_invalido_da_422(logado) -> None:
    assert logado.get("/app/historico?tipo=xx").status_code == 422
    assert logado.get("/app/historico?pagina=0").status_code == 422


def test_pagina_de_comparar_lista_o_modelo_configurado(logado) -> None:
    html = logado.get("/app/comparar").text
    assert 'name="modelo"' in html and 'name="pensar"' in html


def test_montar_comparacao(logado) -> None:
    assert (
        "ao menos um modelo"
        in logado.post("/app/comparar", data={"pergunta": "Qual o prazo?"}, headers=HX).text
    )
    r = logado.post(
        "/app/comparar", data={"pergunta": 'Qual o "prazo"?', "modelo": ["a", "b", "a"]}, headers=HX
    )
    assert r.text.count('hx-post="/app/comparar/gerar"') == 2
    assert (
        logado.post("/app/comparar", data={"pergunta": "Qual o prazo?", "modelo": "a"}).status_code
        == 403
    )


def test_cartao_gera_sem_gravar_no_historico(logado) -> None:
    antes = _contagem()
    r = logado.post(
        "/app/comparar/gerar",
        data={"pergunta": "Qual o prazo de entrega?", "modelo": "simulado", "posicao": "2"},
        headers=HX,
    )
    assert r.status_code == 200 and 'id="m2-1"' in r.text and 'href="#m2-1"' in r.text
    assert _contagem() == antes
