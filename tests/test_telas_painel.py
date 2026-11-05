"""Integração: configuração (precedência), saúde e logs."""

import logging

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine
from app.logging_config import MEMORIA
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
            files={"arquivos": ("m.pdf", pdf_com_texto(["O prazo de entrega e de trinta dias."]))},
            headers=HX,
        )
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _salvar(c, **campos):
    return c.post("/app/configuracao", data=campos, headers=HX)


def _linhas() -> dict[str, str]:
    with get_engine().connect() as c:
        return dict(c.execute(text("SELECT chave, valor FROM configuracao")).all())


def test_exige_sessao(banco) -> None:  # noqa: F811
    with TestClient(create_app(), follow_redirects=False) as c:
        for caminho in ("/app/configuracao", "/app/saude", "/app/logs", "/app/logs/lista"):
            assert c.get(caminho).status_code == 303
        assert c.post("/app/configuracao", data={}).status_code == 303


def test_tela_mostra_o_padrao_do_env(logado) -> None:
    html = logado.get("/app/configuracao").text
    assert 'name="consulta_k"' in html and ".env: 5" in html and "Fixos no" in html


def test_salvar_e_restaurar(logado) -> None:
    r = _salvar(logado, consulta_k="3", limiar_similaridade="0,4")
    assert "Salvo" in r.text and "definido aqui" in r.text
    assert _linhas() == {"consulta_k": "3", "limiar_similaridade": "0.4"}
    _salvar(logado, consulta_k="3")  # o campo vazio apaga a linha
    assert _linhas() == {"consulta_k": "3"}
    _salvar(logado)
    assert _linhas() == {}


def test_valor_invalido_nao_grava_nada(logado) -> None:
    r = _salvar(logado, consulta_k="3", limiar_similaridade="2")
    assert "Nada foi salvo" in r.text and "entre 0 e 1" in r.text
    assert "esperado número inteiro" in _salvar(logado, consulta_k="abc").text
    assert _linhas() == {}


def test_regra_entre_campos(logado) -> None:
    r = _salvar(logado, consulta_candidatos="3")  # menor que o maior k
    assert "Nada foi salvo" in r.text and _linhas() == {}


def test_banco_vence_o_env_e_a_pergunta_vence_o_banco(logado) -> None:
    paginas = [
        " ".join(
            f"O prazo {n} de entrega do pedido {n} e de trinta dias uteis, item {i}."
            for i in range(25)
        )
        for n in range(6)
    ]
    logado.post(
        "/app/documentos/enviar",
        files={"arquivos": ("p.pdf", pdf_com_texto(paginas))},
        headers=HX,
    )

    def recuperados(**extra):
        r = logado.post("/app/perguntar", data={"pergunta": "Qual o prazo?", **extra}, headers=HX)
        return r.text.count('id="trecho-')

    assert recuperados() == 5  # .env (padrão)
    _salvar(logado, consulta_k="2")
    assert recuperados() == 2  # banco vence o .env
    assert recuperados(k="4") == 4  # a pergunta vence o banco
    _salvar(logado)
    assert recuperados() == 5  # restaurado


def test_banco_invalido_cai_no_env(logado) -> None:
    with get_engine().begin() as c:
        c.execute(text("INSERT INTO configuracao (chave, valor) VALUES ('consulta_k', 'lixo')"))
    assert (
        logado.post("/app/perguntar", data={"pergunta": "Qual o prazo?"}, headers=HX).status_code
        == 200
    )


def test_modelo_novo_gera_gerador_novo(logado) -> None:
    from app.llm.config import get_llm_settings
    from app.routers.perguntas import obter_gerador as fabrica

    base = get_llm_settings()
    a = fabrica(base)
    b = fabrica(base.model_copy(update={"llm_think": False}))
    assert a is fabrica(base) and a is not b


def test_saude(logado) -> None:
    html = logado.get("/app/saude").text
    assert "Banco" in html and "0009" in html and "Trechos" in html and "pgvector" in html


def test_logs_filtram_por_nivel_e_texto(logado) -> None:
    logging.getLogger("app").warning("algo estranho aconteceu", extra={"documento_id": 7})
    MEMORIA.eventos.append(
        {"ts": "2026-11-02T10:00:00+00:00", "nivel": "ERROR", "logger": "x", "msg": "falha grave"}
    )
    assert "falha grave" in logado.get("/app/logs/lista?nivel=ERROR").text
    assert "falha grave" not in logado.get("/app/logs/lista?nivel=INFO&q=estranho").text
    assert "documento_id=7" in logado.get("/app/logs/lista?nivel=WARNING").text
    assert logado.get("/app/logs/lista?nivel=XX").status_code == 422
    assert "<script>" not in logado.get("/app/logs/lista?q=<script>").text


def test_idioma_da_resposta_na_configuracao(logado) -> None:
    from app.llm.config import get_llm_settings
    from app.routers.perguntas import obter_gerador as fabrica

    html = logado.get("/app/configuracao").text
    assert 'name="llm_idioma"' in html and "sempre português" in html
    assert "como no .env (o idioma da pergunta)" in html
    assert "definido aqui" in _salvar(logado, llm_idioma="pt").text
    assert _linhas() == {"llm_idioma": "pt"}
    r = _salvar(logado, llm_idioma="klingon")
    assert "escolha uma das opções" in r.text
    assert _linhas() == {"llm_idioma": "pt"}  # o inválido não gravou nada
    base = get_llm_settings()
    assert fabrica(base) is not fabrica(base.model_copy(update={"llm_idioma": "pt"}))
