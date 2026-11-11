"""Perfis de trecho e parâmetros de ingestão configuráveis."""

import pytest
from auxiliares import (  # noqa: F401
    ambiente,
    banco,
    pdf_com_texto,
    precisa_de_banco,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app import configuracao
from app.db import get_engine, get_sessionmaker
from app.ingestao.config import IngestaoSettings
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

CHAVE = "chave-de-teste-com-mais-de-16-caracteres"
HX = {"HX-Request": "true"}


def test_perfil_define_tamanho_e_sobreposicao() -> None:
    assert IngestaoSettings(perfil_trechos="medio").tamanho_trecho == 1000
    longo = IngestaoSettings(perfil_trechos="longo", tamanho_trecho=300)
    assert (longo.tamanho_trecho, longo.sobreposicao_trecho) == (1800, 250)
    livre = IngestaoSettings(perfil_trechos="personalizado", tamanho_trecho=700)
    assert livre.tamanho_trecho == 700
    assert IngestaoSettings().tamanho_trecho == 500  # sem perfil: o padrão de sempre


def test_perfil_desconhecido_e_recusado(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        IngestaoSettings(perfil_trechos="gigante")
    monkeypatch.setenv("PERFIL_TRECHOS", "medio")
    assert IngestaoSettings().sobreposicao_trecho == 150


@precisa_de_banco
def test_configuracao_grava_perfil_e_parametros(banco) -> None:  # noqa: F811
    with get_sessionmaker()() as s:
        assert configuracao.salvar(s, {"perfil_trechos": "medio", "max_paginas": "300"}) == []
        ingestao = configuracao.efetivas(s)[2]
        assert (ingestao.tamanho_trecho, ingestao.sobreposicao_trecho) == (1000, 150)
        assert ingestao.max_paginas == 300
        # personalizado usa os campos
        assert (
            configuracao.salvar(
                s,
                {
                    "perfil_trechos": "personalizado",
                    "tamanho_trecho": "700",
                    "sobreposicao_trecho": "90",
                },
            )
            == []
        )
        assert configuracao.efetivas(s)[2].tamanho_trecho == 700


@precisa_de_banco
def test_configuracao_recusa_valores_incoerentes(banco) -> None:  # noqa: F811
    with get_sessionmaker()() as s:
        erros = configuracao.salvar(
            s,
            {
                "perfil_trechos": "personalizado",
                "tamanho_trecho": "400",
                "sobreposicao_trecho": "300",
            },
        )
        assert erros and "SOBREPOSICAO" in " ".join(erros).upper()
        assert configuracao.salvar(s, {"tamanho_trecho": "50"})  # abaixo do mínimo
        assert configuracao.ler(s) == {}  # nada foi gravado


@precisa_de_banco
def test_perfil_curto_traz_a_nota_minima_calibrada(banco) -> None:  # noqa: F811
    with get_sessionmaker()() as s:
        configuracao.salvar(s, {"perfil_trechos": "curto", "reranker_nota_minima": "0.4"})
        assert configuracao.efetivas(s)[0].reranker_nota_minima == 0.05
        # perfil sem calibração: vale a nota configurada
        configuracao.salvar(s, {"perfil_trechos": "medio", "reranker_nota_minima": "0.4"})
        assert configuracao.efetivas(s)[0].reranker_nota_minima == 0.4


@pytest.fixture
def logado(banco):  # noqa: F811
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    app = create_app()
    with TestClient(app, follow_redirects=False) as c:
        assert c.post("/app/entrar", data={"chave": CHAVE}).status_code == 303
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


@precisa_de_banco
def test_documento_guarda_o_tamanho_e_a_tela_avisa_dos_desatualizados(logado) -> None:
    r = logado.post(
        "/app/documentos/enviar",
        files={
            "arquivos": ("manual.pdf", pdf_com_texto(["A garantia cobre defeitos por um ano."]))
        },
        headers=HX,
    )
    assert r.status_code == 200
    with get_engine().connect() as c:
        assert c.execute(
            text("SELECT tamanho_trecho, sobreposicao_trecho FROM documento")
        ).all() == [(500, 80)]
    assert "processado(s) com outro tamanho" not in logado.get("/app/configuracao").text
    r = logado.post("/app/configuracao", data={"perfil_trechos": "medio"}, headers=HX)
    assert "Salvo" in r.text and "1 documento(s) foram processados com outro tamanho" in r.text
    pagina = logado.get("/app/configuracao").text
    assert "nota mínima ainda não calibrada" in pagina and "Perfil de trecho" in pagina
    # um novo envio já usa o perfil
    logado.post(
        "/app/documentos/enviar",
        files={"arquivos": ("outro.pdf", pdf_com_texto(["O prazo de entrega e de trinta dias."]))},
        headers=HX,
    )
    with get_engine().connect() as c:
        assert sorted(c.execute(text("SELECT tamanho_trecho FROM documento")).scalars()) == [
            500,
            1000,
        ]
