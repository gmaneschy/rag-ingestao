"""POST /perguntas de ponta a ponta, com o gerador simulado."""

import json

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import get_engine, get_session
from app.llm.tipos import ModeloIndisponivel, Resposta
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}


@pytest.fixture
def cliente(banco):  # noqa: F811
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        c.app_ref = app
        yield c
    app.dependency_overrides.clear()
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _enviar(cliente, nome, conteudo, colecao="geral"):
    r = cliente.post(
        "/documentos", files={"arquivo": (nome, conteudo)}, data={"colecao": colecao}, headers=CHAVE
    )
    assert r.status_code in (200, 202), r.text
    return r.json()["id"]


def _perguntar(cliente, pergunta="Qual o prazo de entrega?", **extra):
    return cliente.post("/perguntas", json={"pergunta": pergunta, **extra}, headers=CHAVE)


@pytest.fixture
def manual(cliente):
    return _enviar(
        cliente,
        "manual.pdf",
        pdf_com_texto(
            ["A garantia cobre defeitos por um ano.", "O prazo de entrega e de trinta dias."]
        ),
    )


def test_exige_chave(cliente) -> None:
    assert cliente.post("/perguntas", json={"pergunta": "oi, tudo bem?"}).status_code == 401


def test_resposta_cita_documento_e_pagina(cliente, manual) -> None:
    r = _perguntar(cliente)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["recusou"] is False and corpo["aviso"] is None
    assert "[1]" in corpo["resposta"]
    fonte = corpo["fontes"][0]
    assert (fonte["documento"], fonte["pagina"]) == ("manual.pdf", 2)
    assert "prazo de entrega" in fonte["texto"]
    assert {t["pagina"] for t in corpo["trechos_usados"]} == {1, 2}
    assert corpo["modelo"] == "simulado-extrativo-v1" and corpo["latencia_ms"] >= 0


def test_a_consulta_fica_registrada_com_o_texto_citado(cliente, manual) -> None:
    consulta_id = _perguntar(cliente).json()["id"]
    with get_engine().connect() as c:
        linha = c.execute(
            text(
                "SELECT pergunta, resposta, fontes, modelo_geracao, latencia_ms "
                "FROM consulta WHERE id = :i"
            ),
            {"i": consulta_id},
        ).one()
    assert linha.pergunta == "Qual o prazo de entrega?"
    assert linha.fontes[0]["documento"] == "manual.pdf" and "prazo" in linha.fontes[0]["texto"]
    assert linha.modelo_geracao == "simulado-extrativo-v1" and linha.latencia_ms >= 0


def test_sem_evidencia_recusa_e_nao_chama_o_modelo(cliente, manual) -> None:
    class NaoPodeSerChamado:
        nome_modelo = "x"

        def responder(self, *_):
            raise AssertionError("o modelo não deveria ser chamado")

    cliente.app_ref.dependency_overrides[obter_gerador] = NaoPodeSerChamado
    corpo = _perguntar(cliente, "Quem ganhou a copa do mundo de 1970?").json()
    assert corpo["recusou"] is True and corpo["fontes"] == []
    assert "evidência" in corpo["resposta"]


def test_corpus_vazio_recusa(cliente) -> None:
    corpo = _perguntar(cliente).json()
    assert corpo["recusou"] and corpo["trechos_usados"] == []


def test_filtro_de_colecao(cliente) -> None:
    _enviar(cliente, "a.txt", b"O prazo de entrega e de trinta dias.", "contratos")
    _enviar(cliente, "b.txt", b"O prazo de validade do leite e curto.", "cozinha")
    corpo = _perguntar(cliente, "prazo", colecoes=["cozinha"]).json()
    assert {f["documento"] for f in corpo["fontes"]} == {"b.txt"}
    assert {t["documento"] for t in corpo["trechos_usados"]} == {"b.txt"}


def test_modelo_sem_citacao_gera_aviso(cliente, manual) -> None:
    class SemCitar:
        nome_modelo = "fake"

        def responder(self, pergunta, trechos):
            return Resposta(texto="Trinta dias.", tokens_entrada=10, tokens_saida=2)

    cliente.app_ref.dependency_overrides[obter_gerador] = SemCitar
    corpo = _perguntar(cliente).json()
    assert corpo["fontes"] == [] and "não citou" in corpo["aviso"]
    assert corpo["trechos_usados"]


def test_citacao_inventada_e_descartada(cliente, manual) -> None:
    class CitaDemais:
        nome_modelo = "fake"

        def responder(self, pergunta, trechos):
            return Resposta(texto="Trinta dias [1][9].", fontes=(1, 9))

    cliente.app_ref.dependency_overrides[obter_gerador] = CitaDemais
    corpo = _perguntar(cliente).json()
    assert [f["n"] for f in corpo["fontes"]] == [1]


def test_modelo_de_embeddings_trocado_da_409(cliente, manual) -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE trecho SET modelo_embedding = 'modelo-antigo'"))
    r = _perguntar(cliente)
    assert r.status_code == 409 and "manual.pdf" in r.json()["detail"]
    assert "reprocessar" in r.json()["detail"]


def test_reprocessar_destrava_depois_da_troca(cliente, manual) -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE trecho SET modelo_embedding = 'modelo-antigo'"))
    assert cliente.post(f"/documentos/{manual}/reprocessar", headers=CHAVE).status_code == 202
    assert _perguntar(cliente).status_code == 200


def test_limite_diario_de_gasto_da_429(cliente, manual) -> None:
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO consulta (pergunta, resposta, custo) VALUES ('x', 'y', 5)"))
    assert _perguntar(cliente).status_code == 429


def test_modelo_fora_do_ar_da_503(cliente, manual) -> None:
    class Fora:
        nome_modelo = "fake"

        def responder(self, *_):
            raise ModeloIndisponivel(
                "Não consegui falar com o Ollama em http://x. Ele está rodando?"
            )

    cliente.app_ref.dependency_overrides[obter_gerador] = Fora
    r = _perguntar(cliente)
    assert r.status_code == 503 and "Ollama" in r.json()["detail"]


def test_banco_fora_do_ar_da_503_e_nao_500(cliente) -> None:
    def quebrado():
        raise OperationalError("SELECT 1", {}, Exception("connection timeout expired"))

    cliente.app_ref.dependency_overrides[get_session] = quebrado
    r = _perguntar(cliente)
    assert r.status_code == 503 and r.json() == {"detail": "Banco de dados indisponível"}


@pytest.mark.parametrize(
    "corpo",
    [
        {"pergunta": "ab"},
        {"pergunta": "x" * 2001},
        {"pergunta": "valida?", "k": 0},
        {"pergunta": "valida?", "k": 11},
        {"pergunta": "valida?", "modo": "magica"},
        {"pergunta": "valida?", "colecoes": ["../etc"]},
    ],
)
def test_entradas_invalidas_dao_422(cliente, corpo) -> None:
    assert cliente.post("/perguntas", json=corpo, headers=CHAVE).status_code == 422


def test_avaliacao(cliente, manual) -> None:
    consulta_id = _perguntar(cliente).json()["id"]
    r = cliente.post(f"/perguntas/{consulta_id}/avaliacao", json={"ajudou": True}, headers=CHAVE)
    assert r.status_code == 200 and r.json() == {"id": consulta_id, "ajudou": True}
    with get_engine().connect() as c:
        assert c.execute(text("SELECT ajudou FROM consulta")).scalar_one() is True
    assert (
        cliente.post("/perguntas/999/avaliacao", json={"ajudou": False}, headers=CHAVE).status_code
        == 404
    )


def test_o_documento_removido_nao_apaga_a_resposta_antiga(cliente, manual) -> None:
    consulta_id = _perguntar(cliente).json()["id"]
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM documento"))
    with get_engine().connect() as c:
        fontes = c.execute(
            text("SELECT fontes FROM consulta WHERE id = :i"), {"i": consulta_id}
        ).scalar_one()
    assert json.dumps(fontes).count("manual.pdf") >= 1
