"""Reprocessar em lote e mover documentos entre coleções (API, tela e serviço)."""

# ruff: noqa: F811

import re

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = "chave-de-teste-com-mais-de-16-caracteres"
API = {"X-API-Key": CHAVE}
HX = {"HX-Request": "true"}


def _texto(n: int) -> bytes:
    return f"Documento {n}: o prazo de entrega do pedido {n} e de {n + 10} dias uteis.".encode()


@pytest.fixture
def cliente(banco):
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


@pytest.fixture
def logado(cliente):
    assert cliente.post("/app/entrar", data={"chave": CHAVE}).status_code == 303
    return cliente


def _enviar(c, n: int, colecao="geral", nome=None) -> int:
    r = c.post(
        "/documentos",
        files={"arquivo": (nome or f"doc{n}.txt", _texto(n))},
        data={"colecao": colecao},
        headers=API,
    )
    assert r.status_code in (200, 202), r.text
    return r.json()["id"]


def _um(banco, sql, **p):
    with banco() as s:
        return s.execute(text(sql), p).scalar_one()


def _colecao_de(banco, doc):
    return _um(banco, "SELECT colecao FROM documento WHERE id = :i", i=doc)


# ---------- API ----------


def test_api_reprocessa_ids_e_informa_os_inexistentes(cliente, banco) -> None:
    a, b = _enviar(cliente, 1), _enviar(cliente, 2)
    r = cliente.post("/documentos/reprocessar", json={"ids": [a, b, 9999]}, headers=API)
    assert r.status_code == 202
    assert r.json() == {"reprocessando": [a, b], "inexistentes": [9999]}
    # O segundo plano roda no fim da requisição: tudo volta a ficar processado.
    assert _um(banco, "SELECT count(*) FROM documento WHERE estado = 'processado'") == 2


def test_api_reprocessa_todos_com_filtro(cliente, banco) -> None:
    _enviar(cliente, 1, colecao="x")
    _enviar(cliente, 2, colecao="y")
    r = cliente.post("/documentos/reprocessar", json={"todos": True, "colecao": "x"}, headers=API)
    assert len(r.json()["reprocessando"]) == 1


@pytest.mark.parametrize("corpo", [{}, {"ids": []}, {"ids": [1], "todos": True}, {"todos": False}])
def test_api_reprocessar_exige_ids_ou_todos(cliente, corpo) -> None:
    assert cliente.post("/documentos/reprocessar", json=corpo, headers=API).status_code == 422


def test_api_mover_troca_a_colecao_sem_reindexar(cliente, banco) -> None:
    doc = _enviar(cliente, 1)
    antes = _um(banco, "SELECT count(*) FROM trecho WHERE documento_id = :i", i=doc)
    r = cliente.post("/documentos/mover", json={"ids": [doc], "destino": "juridico"}, headers=API)
    assert r.json() == {"movidos": [doc], "ignorados": []}
    assert _colecao_de(banco, doc) == "juridico"
    assert _um(banco, "SELECT count(*) FROM trecho WHERE documento_id = :i", i=doc) == antes
    assert _um(banco, "SELECT tipo FROM evento_documento WHERE tipo = 'movido'") == "movido"
    # A busca por coleção já enxerga o documento no novo lugar.
    p = cliente.post(
        "/perguntas", json={"pergunta": "prazo de entrega", "colecoes": ["juridico"]}, headers=API
    )
    assert p.status_code == 200


def test_api_mover_recusa_colisao_de_nome_e_de_conteudo(cliente, banco) -> None:
    a = _enviar(cliente, 1, colecao="geral", nome="igual.txt")
    _enviar(cliente, 2, colecao="alvo", nome="igual.txt")  # mesmo nome no destino
    b = _enviar(cliente, 3, colecao="geral")
    _enviar(cliente, 3, colecao="alvo", nome="outro-nome.txt")  # mesmo conteúdo no destino
    c = _enviar(cliente, 4, colecao="alvo")  # já está no destino
    r = cliente.post(
        "/documentos/mover", json={"ids": [a, b, c, 9999], "destino": "alvo"}, headers=API
    )
    corpo = r.json()
    assert corpo["movidos"] == []
    motivos = {i["id"]: i["motivo"] for i in corpo["ignorados"]}
    assert "esse nome" in motivos[a]
    assert "mesmo conteúdo" in motivos[b]
    assert "já está" in motivos[c]
    assert "não existe" in motivos[9999]
    assert _colecao_de(banco, a) == "geral" and _colecao_de(banco, b) == "geral"


def test_api_mover_valida_o_destino(cliente) -> None:
    r = cliente.post("/documentos/mover", json={"ids": [1], "destino": "com espaço"}, headers=API)
    assert r.status_code == 422
    assert cliente.post("/documentos/mover", json={"ids": [1], "destino": "x"}).status_code in (
        401,
        403,
    )


# ---------- tela ----------


def test_tela_tem_caixas_e_barra_de_lote(logado) -> None:
    _enviar(logado, 1)
    html = logado.get("/app/documentos").text
    assert 'id="lote"' in html and re.search(
        r'name="ids"[^>]*form="lote"|form="lote"[^>]*name="ids"', html
    )
    assert "/app/documentos/reprocessar-lote" in html and "/app/documentos/mover-lote" in html


def test_tela_reprocessa_em_lote(logado, banco) -> None:
    a, b = _enviar(logado, 1), _enviar(logado, 2)
    r = logado.post("/app/documentos/reprocessar-lote", data={"ids": [a, b]}, headers=HX)
    assert r.status_code == 200 and "2 documentos na fila" in r.text
    assert 'id="tabela"' in r.text
    assert _um(banco, "SELECT count(*) FROM documento WHERE estado = 'processado'") == 2


def test_tela_sem_selecao_avisa(logado) -> None:
    r = logado.post("/app/documentos/reprocessar-lote", data={}, headers=HX)
    assert "Marque ao menos um documento" in r.text
    r = logado.post("/app/documentos/mover-lote", data={"destino": "x"}, headers=HX)
    assert "Marque ao menos um documento" in r.text


def test_tela_move_em_lote_e_mostra_o_que_ficou(logado, banco) -> None:
    a = _enviar(logado, 1, nome="a.txt")
    _enviar(logado, 2, colecao="alvo", nome="a.txt")
    b = _enviar(logado, 3)
    r = logado.post(
        "/app/documentos/mover-lote",
        data={"ids": [a, b], "destino": "alvo", "colecao": "", "estado": ""},
        headers=HX,
    )
    assert "1 documento movido para “alvo”" in r.text
    assert "a.txt: já existe um documento com esse nome no destino" in r.text
    assert _colecao_de(banco, b) == "alvo" and _colecao_de(banco, a) == "geral"


def test_tela_mover_valida_destino(logado, banco) -> None:
    a = _enviar(logado, 1)
    r = logado.post("/app/documentos/mover-lote", data={"ids": [a], "destino": "a b"}, headers=HX)
    assert "Dê a coleção de destino" in r.text and _colecao_de(banco, a) == "geral"


def test_tela_lote_respeita_o_filtro_da_lista(logado) -> None:
    _enviar(logado, 1, colecao="x")
    _enviar(logado, 2, colecao="y")
    r = logado.post(
        "/app/documentos/reprocessar-lote",
        data={"ids": [1], "colecao": "y", "estado": ""},
        headers=HX,
    )
    assert "doc2.txt" in r.text and "doc1.txt" not in r.text


def test_tela_mover_um_documento(logado, banco) -> None:
    a = _enviar(logado, 1)
    assert f'hx-post="/app/documentos/{a}/mover"' in logado.get(f"/app/documentos/{a}").text
    r = logado.post(f"/app/documentos/{a}/mover", data={"destino": "novo"}, headers=HX)
    assert r.headers["hx-refresh"] == "true" and _colecao_de(banco, a) == "novo"
    r = logado.post(f"/app/documentos/{a}/mover", data={"destino": "novo"}, headers=HX)
    assert "Já está nessa coleção" in r.text
    r = logado.post(f"/app/documentos/{a}/mover", data={"destino": "a b"}, headers=HX)
    assert "Coleção inválida" in r.text


def test_acoes_de_lote_exigem_htmx(logado) -> None:
    assert logado.post("/app/documentos/reprocessar-lote", data={"ids": [1]}).status_code == 403
    assert logado.post("/app/documentos/mover-lote", data={"ids": [1]}).status_code == 403
    assert logado.post("/app/documentos/1/mover", data={"destino": "x"}).status_code == 403


# ---------- andamento na tela ----------


def test_tela_mostra_barra_e_etapa_dos_pendentes(logado, banco) -> None:
    a = _enviar(logado, 1)
    with banco() as s:
        s.execute(
            text(
                "UPDATE documento SET estado = 'pendente', progresso = 42, "
                "etapa = 'Gerando vetores (10 de 24 trechos)', "
                "processando_desde = now() - interval '60 seconds' WHERE id = :i"
            ),
            {"i": a},
        )
        s.commit()
    tabela = logado.get("/app/documentos/tabela").text
    assert '<progress max="100" value="42"' in tabela and "42%" in tabela
    assert "Gerando vetores (10 de 24 trechos)" in tabela and "restantes" in tabela
    assert "42%" in logado.get(f"/app/documentos/{a}/estado").text
    with banco() as s:
        s.execute(text("UPDATE documento SET progresso = NULL, etapa = NULL"))
        s.commit()
    assert "Na fila" in logado.get("/app/documentos/tabela").text


def test_indicadores_de_carregamento_nas_telas(logado) -> None:
    assert 'class="roda"' not in logado.get("/app/perguntar").text  # o chat usa o do turno
    comparar = logado.get("/app/comparar").text
    assert 'class="roda"' in comparar and 'hx-indicator="this"' in comparar
