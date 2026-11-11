"""Integração: conversas, perguntas em segundo plano, parar, editar, apagar."""

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

from app.conversa import servico
from app.db import get_engine, get_sessionmaker
from app.llm.acompanhamento import Acompanhamento, Cancelado
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
        c.post(
            "/app/documentos/enviar",
            files={
                "arquivos": (
                    "manual.pdf",
                    pdf_com_texto(
                        ["A garantia cobre defeitos por um ano.", "O prazo e de trinta dias."]
                    ),
                )
            },
            headers=HX,
        )
        yield c
    app.dependency_overrides.clear()
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _linhas(sql: str, **p):
    with get_engine().connect() as c:
        return c.execute(text(sql), p).all()


def test_cria_conversa_responde_e_aparece_na_barra(logado) -> None:
    r = logado.post("/app/conversas", data={"pergunta": "Qual o prazo de entrega?"}, headers=HX)
    destino = r.headers["HX-Redirect"]
    assert re.fullmatch(r"/app/conversas/\d+", destino)
    pagina = logado.get(destino).text
    assert 'data-estado="pronta"' in pagina and "Qual o prazo de entrega?" in pagina
    assert 'class="conversa-item' in logado.get("/app/documentos").text
    assert _linhas("SELECT estado FROM turno") == [("pronta",)]


def test_segunda_pergunta_entra_na_mesma_conversa_e_guarda_parametros(logado) -> None:
    destino = logado.post("/app/conversas", data={"pergunta": "Qual o prazo?"}, headers=HX).headers[
        "HX-Redirect"
    ]
    cid = destino.rsplit("/", 1)[1]
    r = logado.post(
        f"/app/conversas/{cid}/turnos",
        data={"pergunta": "Qual a garantia?", "modo": "textual", "k": "3", "colecao": "x"},
        headers=HX,
    )
    assert r.status_code == 200 and 'id="turno-2"' in r.text
    assert _linhas("SELECT count(*) FROM turno WHERE conversa_id = :c", c=int(cid)) == [(2,)]
    cfg = _linhas("SELECT config FROM conversa")[0][0]
    assert cfg["modo"] == "textual" and cfg["k"] == 3 and cfg["colecoes"] == ["x"]
    # a tela da conversa reabre com os parâmetros marcados
    pagina = logado.get(destino).text
    assert re.search(r'value="textual"\s+checked', pagina)


def test_pergunta_curta_e_exige_htmx(logado) -> None:
    assert (
        "Escreva uma pergunta"
        in logado.post("/app/conversas", data={"pergunta": " "}, headers=HX).text
    )
    assert logado.post("/app/conversas", data={"pergunta": "Qual o prazo?"}).status_code == 403
    assert logado.get("/app/conversas/999").status_code == 200  # tela "não encontrado"
    assert "Não encontrado" in logado.get("/app/conversas/999").text


def test_renomear_e_apagar_remove_as_consultas(logado) -> None:
    destino = logado.post("/app/conversas", data={"pergunta": "Qual o prazo?"}, headers=HX).headers[
        "HX-Redirect"
    ]
    cid = destino.rsplit("/", 1)[1]
    assert (
        logado.post(f"/app/conversas/{cid}/renomear", data={"titulo": "  "}, headers=HX).status_code
        == 422
    )
    assert (
        logado.post(
            f"/app/conversas/{cid}/renomear", data={"titulo": "Prazos"}, headers=HX
        ).status_code
        == 200
    )
    assert _linhas("SELECT titulo FROM conversa") == [("Prazos",)]
    assert _linhas("SELECT count(*) FROM consulta") == [(1,)]
    r = logado.delete(f"/app/conversas/{cid}", headers=HX)
    assert r.headers["HX-Redirect"] == "/app/perguntar"
    assert _linhas("SELECT count(*) FROM consulta") == [(0,)]
    assert _linhas("SELECT count(*) FROM turno") == [(0,)]


def test_editar_refaz_o_turno_e_apaga_a_consulta_antiga(logado) -> None:
    destino = logado.post("/app/conversas", data={"pergunta": "Qual o prazo?"}, headers=HX).headers[
        "HX-Redirect"
    ]
    cid = destino.rsplit("/", 1)[1]
    antiga = _linhas("SELECT consulta_id FROM turno")[0][0]
    r = logado.post(
        f"/app/conversas/{cid}/turnos/1/refazer", data={"pergunta": "Qual a garantia?"}, headers=HX
    )
    assert r.status_code == 200 and "Qual a garantia?" in r.text
    assert _linhas("SELECT pergunta, geracao FROM turno") == [("Qual a garantia?", 2)]
    assert _linhas("SELECT count(*) FROM consulta WHERE id = :i", i=antiga) == [(0,)]
    assert _linhas("SELECT count(*) FROM consulta") == [(1,)]


def test_erro_do_modelo_aparece_no_turno_e_da_para_tentar_de_novo(logado) -> None:
    from app.llm.tipos import ModeloIndisponivel

    class Quebrado:
        nome_modelo = "x"

        def responder(self, *a, **k):
            raise ModeloIndisponivel("Ollama fora do ar")

    logado.app_ref.dependency_overrides[obter_gerador] = lambda: Quebrado()
    pagina = perguntar_pela_tela(logado, "Qual o prazo?").text
    assert 'data-estado="erro"' in pagina and "Ollama fora do ar" in pagina
    assert "Tentar de novo" in pagina


def test_parar_no_meio_da_geracao_cancela_e_nao_deixa_consulta(logado) -> None:
    """O usuário clica em Parar enquanto o modelo gera: o turno vira 'cancelada'."""

    class ParadoNoMeio:
        nome_modelo = "lento"

        def responder(self, pergunta, trechos, acompanhar: Acompanhamento | None = None, **_):
            with get_sessionmaker()() as s:
                assert servico.cancelar(s, 1, 1)  # o clique no botão Parar
            acompanhar.conferir()  # o modelo real confere a cada pedaço recebido
            raise AssertionError("não foi cancelado")

    logado.app_ref.dependency_overrides[obter_gerador] = lambda: ParadoNoMeio()
    pagina = perguntar_pela_tela(logado, "Qual o prazo?").text
    assert 'data-estado="cancelada"' in pagina and "Tentar de novo" in pagina
    assert _linhas("SELECT estado FROM turno") == [("cancelada",)]
    assert _linhas("SELECT count(*) FROM consulta") == [(0,)]
    ac = Acompanhamento()
    ac.cancelar()
    with pytest.raises(Cancelado):
        ac.conferir()


def test_turnos_interrompidos_viram_erro_na_subida(banco) -> None:  # noqa: F811
    sm = get_sessionmaker()
    with sm() as s:
        cid = servico.criar_conversa(s, "x y z", {})
        tid = servico.novo_turno(s, cid, "x y z")
    assert servico.encerrar_interrompidos(sm) == 1
    with sm() as s:
        t = servico.turno(s, cid, tid)
    assert t["estado"] == "erro" and t["erro"]


def test_fechar_o_modulo_grava_a_selecao_na_conversa(logado) -> None:
    destino = logado.post("/app/conversas", data={"pergunta": "Qual o prazo?"}, headers=HX).headers[
        "HX-Redirect"
    ]
    cid = destino.rsplit("/", 1)[1]
    doc = _linhas("SELECT id FROM documento")[0][0]
    r = logado.post(
        f"/app/conversas/{cid}/config",
        data={"modo": "vetorial", "tipo": "analise", "documento": str(doc), "incluir": "notas"},
        headers=HX,
    )
    assert r.status_code == 204
    cfg = _linhas("SELECT config FROM conversa")[0][0]
    assert (
        cfg["modo"] == "vetorial" and cfg["documento_ids"] == [doc] and cfg["incluir"] == ["notas"]
    )
    pagina = logado.get(destino).text
    assert f'value="{doc}" checked' in pagina and 'id="modal-docs"' in pagina
    assert logado.post("/app/conversas/999/config", data={}, headers=HX).status_code == 404
    assert logado.post(f"/app/conversas/{cid}/config", data={}).status_code == 403


def test_fontes_vao_para_o_painel_lateral(logado) -> None:
    pagina = perguntar_pela_tela(logado, "Qual o prazo?").text
    assert 'id="painel-fontes"' in pagina and 'data-fontes="1"' in pagina
    assert '<template id="fontes-dados-1">' in pagina
