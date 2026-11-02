"""Integração: login por cookie e a tela de Documentos."""

import re

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.documentos import obter_embeddings

pytestmark = precisa_de_banco

CHAVE = "chave-de-teste-com-mais-de-16-caracteres"
HX = {"HX-Request": "true"}
TEXTO = b"O prazo de entrega e de trinta dias uteis.\n\nA garantia cobre defeitos por um ano."


@pytest.fixture
def cliente(banco):  # noqa: F811
    obter_embeddings.cache_clear()
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    obter_embeddings.cache_clear()


@pytest.fixture
def logado(cliente):
    resposta = cliente.post("/app/entrar", data={"chave": CHAVE})
    assert resposta.status_code == 303
    return cliente


def _id(html: str) -> str:
    return re.search(r'href="/app/documentos/(\d+)"', html).group(1)


def _enviar(c, nome="a.txt", conteudo=TEXTO, colecao="geral"):
    return c.post(
        "/app/documentos/enviar",
        files={"arquivos": (nome, conteudo)},
        data={"colecao": colecao},
        headers=HX,
    )


def test_sem_sessao_redireciona_para_entrar(cliente) -> None:
    r = cliente.get("/app/documentos")
    assert r.status_code == 303 and r.headers["location"] == "/app/entrar"
    r = cliente.get("/app/documentos", headers=HX)
    assert r.status_code == 401 and r.headers["hx-redirect"] == "/app/entrar"


def test_raiz_leva_aos_documentos(cliente) -> None:
    assert cliente.get("/").headers["location"] == "/app/documentos"


def test_chave_errada_nao_entra(cliente) -> None:
    r = cliente.post("/app/entrar", data={"chave": "errada"})
    assert r.status_code == 401 and "incorreta" in r.text
    assert "rag_sessao" not in r.headers.get("set-cookie", "")


def test_cookie_nao_e_a_chave(cliente) -> None:
    r = cliente.post("/app/entrar", data={"chave": CHAVE})
    cookie = r.headers["set-cookie"]
    assert CHAVE not in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/app" in cookie


def test_cookie_nao_abre_a_api_json(logado) -> None:
    assert logado.get("/documentos").status_code == 401


def test_sair_apaga_a_sessao(logado) -> None:
    logado.post("/app/sair")
    logado.cookies.clear()
    assert logado.get("/app/documentos").status_code == 303


def test_pagina_de_documentos_vazia(logado) -> None:
    r = logado.get("/app/documentos")
    assert r.status_code == 200 and "Nenhum documento" in r.text


def test_envio_aparece_na_lista_e_no_detalhe(logado) -> None:
    r = _enviar(logado)
    assert r.status_code == 200 and "a.txt" in r.text and "processando" in r.text
    lista = logado.get("/app/documentos")
    assert "a.txt" in lista.text and "Pronto" in lista.text
    doc_id = _id(lista.text)
    detalhe = logado.get(f"/app/documentos/{doc_id}")
    assert detalhe.status_code == 200 and "Amostra de trechos" in detalhe.text


def test_reenvio_nao_duplica(logado) -> None:
    _enviar(logado)
    r = _enviar(logado)
    assert "nada foi duplicado" in r.text
    assert "1 documento" in logado.get("/app/documentos/tabela").text


def test_arquivo_invalido_mostra_o_motivo(logado) -> None:
    assert "vazio" in _enviar(logado, conteudo=b"").text
    assert "PDF válido" in _enviar(logado, nome="x.pdf", conteudo=b"nao sou pdf").text
    assert "coleção" in _enviar(logado, colecao="a/b").text


def test_filtros(logado) -> None:
    _enviar(logado, colecao="um")
    _enviar(logado, nome="b.txt", conteudo=TEXTO + b" b", colecao="dois")
    assert "b.txt" not in logado.get("/app/documentos/tabela?colecao=um").text
    assert "a.txt" in logado.get("/app/documentos/tabela?colecao=um").text
    assert "Nenhum" in logado.get("/app/documentos/tabela?estado=falhou").text


def test_acoes_exigem_cabecalho_htmx(logado) -> None:
    _enviar(logado)
    assert logado.post("/app/documentos/1/reprocessar").status_code == 403
    assert logado.delete("/app/documentos/1").status_code == 403
    assert (
        logado.post("/app/documentos/enviar", files={"arquivos": ("a.txt", TEXTO)}).status_code
        == 403
    )


def test_reprocessar_e_remover(logado) -> None:
    _enviar(logado)
    doc_id = _id(logado.get("/app/documentos").text)
    r = logado.post(f"/app/documentos/{doc_id}/reprocessar", headers=HX)
    assert r.status_code == 200 and 'id="estado"' in r.text
    r = logado.delete(f"/app/documentos/{doc_id}?consultas=redigir", headers=HX)
    assert r.headers["hx-redirect"] == "/app/documentos?removido=1"
    assert "Documento removido" in logado.get("/app/documentos?removido=1").text
    assert logado.get(f"/app/documentos/{doc_id}").status_code == 200  # página "não encontrado"
    assert "Não encontrado" in logado.get(f"/app/documentos/{doc_id}").text
    r = logado.delete(f"/app/documentos/{doc_id}", headers=HX)
    assert r.headers["hx-redirect"] == "/app/documentos?removido=0"


def test_estaticos_sem_cdn(cliente) -> None:
    assert cliente.get("/app/static/htmx.min.js").status_code == 200
    assert cliente.get("/app/static/app.css").status_code == 200
    html = cliente.get("/app/entrar").text
    assert "http://" not in html and "https://" not in html


def test_filtro_vazio_nao_da_422(logado) -> None:
    """O "todos" do formulário e o recarregamento automático mandam estado= vazio."""
    _enviar(logado)
    for url in ("/app/documentos/tabela?colecao=&estado=", "/app/documentos?colecao=&estado="):
        r = logado.get(url)
        assert r.status_code == 200 and "a.txt" in r.text
    assert logado.get("/app/documentos/tabela?estado=xx").status_code == 422
