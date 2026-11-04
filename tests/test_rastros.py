"""Prova da remoção sem rastro: um termo único do documento não sobra em lugar nenhum."""

# ruff: noqa: F811

import logging
from pathlib import Path

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.ingestao.config import get_ingestao_settings
from app.ingestao.rastros import procurar_rastros
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
TERMO = "zorblaxquinto"
SECRETO = (
    f"O codinome do projeto confidencial e {TERMO}, com prazo de entrega de trinta dias uteis.\n\n"
    "A garantia cobre defeitos de fabricacao por um ano inteiro."
).encode()
OUTRO = b"A politica de devolucao aceita trocas em ate sete dias corridos apos a compra."


@pytest.fixture
def cliente(banco):
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _enviar(cliente, nome, conteudo) -> int:
    r = cliente.post("/documentos", files={"arquivo": (nome, conteudo)}, headers=CHAVE)
    assert r.status_code in (200, 202), r.text
    return r.json()["id"]


def _rastros(banco) -> list[str]:
    with banco() as s:
        return procurar_rastros(s, TERMO, get_ingestao_settings().arquivos_dir)


def _preparar(cliente, banco, caplog) -> int:
    doc = _enviar(cliente, "projeto-confidencial.txt", SECRETO)
    _enviar(cliente, "devolucao.txt", OUTRO)
    caplog.set_level(logging.INFO)
    r = cliente.post(
        "/perguntas", json={"pergunta": f"Qual o codinome {TERMO} do projeto?"}, headers=CHAVE
    )
    assert r.status_code == 200 and r.json()["fontes"]
    return doc


def test_a_varredura_enxerga_o_termo_antes_da_remocao(cliente, banco, caplog) -> None:
    _preparar(cliente, banco, caplog)
    achados = "\n".join(_rastros(banco))
    for lugar in ("tabela trecho", "tabela consulta", "conteúdo do arquivo"):
        assert lugar in achados, achados  # se a varredura não vê, a prova não vale nada


def test_apagando_as_consultas_nao_sobra_rastro_nenhum(cliente, banco, caplog) -> None:
    doc = _preparar(cliente, banco, caplog)
    r = cliente.delete(f"/documentos/{doc}?consultas=apagar", headers=CHAVE)
    assert r.status_code == 200
    assert _rastros(banco) == []
    with banco() as s:  # o outro documento continua inteiro
        assert s.execute(text("SELECT count(*) FROM documento")).scalar_one() == 1


def test_redigindo_so_a_resposta_escrita_pode_conter_o_termo(cliente, banco, caplog) -> None:
    """Limite documentado: a pergunta e a resposta de uma consulta redigida permanecem."""
    doc = _preparar(cliente, banco, caplog)
    cliente.delete(f"/documentos/{doc}", headers=CHAVE)
    achados = _rastros(banco)
    assert all(a.startswith("tabela consulta") for a in achados), achados
    with banco() as s:
        fontes = s.execute(text("SELECT fontes::text FROM consulta")).scalar_one()
    assert TERMO not in fontes.lower()


def test_termo_curto_demais_e_recusado(banco) -> None:
    with banco() as s, pytest.raises(ValueError):
        procurar_rastros(s, "ab", Path("/tmp"))


def test_o_nome_do_arquivo_fica_so_no_registro_de_auditoria(cliente, banco, caplog) -> None:
    """Limite documentado: evento_documento guarda id, nome, tipo e data, sem conteúdo."""
    doc = _preparar(cliente, banco, caplog)
    cliente.delete(f"/documentos/{doc}?consultas=apagar", headers=CHAVE)
    with banco() as s:
        nomes = s.execute(text("SELECT nome FROM evento_documento WHERE tipo = 'removido'")).all()
        assert [n for (n,) in nomes] == ["projeto-confidencial.txt"]
        assert procurar_rastros(s, "projeto-confidencial", get_ingestao_settings().arquivos_dir)
