"""Etapa 4a: listagem, detalhe com amostra, remoção sem rastro e retomada de pendentes."""

# ruff: noqa: F811

import json
import time
from pathlib import Path

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from app.consulta.config import get_consulta_settings
from app.ingestao import manutencao, remocao, servico
from app.ingestao.config import get_ingestao_settings
from app.llm.simulado import EmbeddingsSimulado
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
PRAZO = (
    b"O prazo de entrega e de trinta dias uteis para todo o territorio nacional.\n\n"
    b"A garantia cobre defeitos de fabricacao por um ano inteiro."
)
DEVOLUCAO = b"A politica de devolucao aceita trocas em ate sete dias corridos apos a compra."


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


def _perguntar(cliente, pergunta="Qual o prazo de entrega?", **extra):
    r = cliente.post("/perguntas", json={"pergunta": pergunta, **extra}, headers=CHAVE)
    assert r.status_code == 200, r.text
    return r.json()


def _um(banco, sql: str, **p):
    with banco() as s:
        return s.execute(text(sql), p).scalar_one()


# ---------- listagem ----------


def test_listagem_filtra_e_pagina(cliente) -> None:
    _enviar(cliente, "a.txt", PRAZO, "contratos")
    _enviar(cliente, "b.txt", DEVOLUCAO, "contratos")
    _enviar(cliente, "c.txt", b"Texto de outra colecao qualquer, com algumas palavras.", "geral")

    tudo = cliente.get("/documentos", headers=CHAVE).json()
    assert tudo["total"] == 3
    assert [d["nome"] for d in tudo["itens"]] == ["c.txt", "b.txt", "a.txt"]  # mais recente antes
    assert all(d["trechos"] >= 1 and d["estado"] == "processado" for d in tudo["itens"])

    contratos = cliente.get("/documentos?colecao=contratos", headers=CHAVE).json()
    assert contratos["total"] == 2

    pagina = cliente.get("/documentos?limite=1&deslocamento=1", headers=CHAVE).json()
    assert pagina["total"] == 3 and [d["nome"] for d in pagina["itens"]] == ["b.txt"]


def test_listagem_filtra_por_estado_e_valida_parametros(cliente, banco) -> None:
    doc = _enviar(cliente, "a.txt", PRAZO)
    with banco() as s:
        s.execute(
            text("UPDATE documento SET estado = 'falhou', mensagem_erro = 'x' WHERE id = :i"),
            {"i": doc},
        )
        s.commit()
    assert cliente.get("/documentos?estado=falhou", headers=CHAVE).json()["total"] == 1
    assert cliente.get("/documentos?estado=processado", headers=CHAVE).json()["total"] == 0
    assert cliente.get("/documentos?estado=qualquer", headers=CHAVE).status_code == 422
    assert cliente.get("/documentos?limite=0", headers=CHAVE).status_code == 422
    assert cliente.get("/documentos").status_code == 401


# ---------- detalhe com amostra ----------


def test_detalhe_com_amostra_espalhada(cliente) -> None:
    paginas = [
        f"Pagina {i} com conteudo proprio numero {i} e palavras suficientes." for i in range(10)
    ]
    doc = _enviar(cliente, "livro.pdf", pdf_com_texto(paginas))

    sem = cliente.get(f"/documentos/{doc}", headers=CHAVE).json()
    assert "amostra" not in sem and sem["trechos"] == 10
    assert sem["modelo_embedding"] == "simulado-hash-v1"

    com = cliente.get(f"/documentos/{doc}?amostra=3", headers=CHAVE).json()
    posicoes = [t["posicao"] for t in com["amostra"]]
    assert posicoes == [0, 3, 6]  # espalhados do início ao fim, não só os primeiros
    assert set(com["amostra"][0]) == {"posicao", "pagina", "texto"}

    muita = cliente.get(f"/documentos/{doc}?amostra=20", headers=CHAVE).json()
    assert [t["posicao"] for t in muita["amostra"]] == list(range(10))
    assert cliente.get(f"/documentos/{doc}?amostra=21", headers=CHAVE).status_code == 422


# ---------- remoção ----------


def test_remocao_apaga_trechos_arquivo_e_trechos_citados(cliente, banco, ambiente) -> None:
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    pasta = ambiente / "arquivos" / str(doc)
    assert pasta.exists()
    resposta = _perguntar(cliente)
    assert resposta["fontes"] and resposta["fontes"][0]["documento_id"] == doc

    r = cliente.delete(f"/documentos/{doc}", headers=CHAVE)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["id"] == doc and corpo["nome"] == "prazos.txt"
    assert corpo["trechos_removidos"] >= 1
    assert corpo["consultas_redigidas"] == 1 and corpo["consultas_apagadas"] == 0
    assert corpo["arquivo_removido"] is True

    # Nada do documento sobra: trechos, linha, arquivo.
    assert _um(banco, "SELECT count(*) FROM documento") == 0
    assert _um(banco, "SELECT count(*) FROM trecho") == 0
    assert not pasta.exists()
    assert cliente.get(f"/documentos/{doc}", headers=CHAVE).status_code == 404

    # A consulta antiga perdeu o texto, o nome e a página do trecho citado.
    fontes = _um(banco, "SELECT fontes FROM consulta")
    assert fontes == [{"n": 1, "removido": True}]
    bruto = json.dumps(fontes)
    assert "prazo" not in bruto and "prazos.txt" not in bruto

    # O evento registra a remoção sem conteúdo e sem limpeza pendente.
    with banco() as s:
        evento = s.execute(
            text(
                "SELECT nome, tipo, ator, limpeza_pendente, caminho_arquivo FROM evento_documento "
                "WHERE tipo = 'removido'"
            )
        ).one()
    assert tuple(evento) == ("prazos.txt", "removido", "api", False, None)

    # E a busca não encontra mais nada.
    depois = _perguntar(cliente)
    assert depois["recusou"] is True and depois["trechos_usados"] == []


def test_remocao_apagando_as_consultas(cliente, banco) -> None:
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    _perguntar(cliente)
    _perguntar(cliente, "Quanto dura a garantia de fabricacao?")
    assert _um(banco, "SELECT count(*) FROM consulta") == 2

    r = cliente.delete(f"/documentos/{doc}?consultas=apagar", headers=CHAVE).json()
    assert r["consultas_apagadas"] == 2 and r["consultas_redigidas"] == 0
    assert _um(banco, "SELECT count(*) FROM consulta") == 0


def test_remocao_redigida_mantem_a_resposta_escrita(cliente, banco) -> None:
    """Limite documentado: a resposta pode ter parafraseado o documento. Quem precisa apagar
    isso usa consultas=apagar."""
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    _perguntar(cliente)
    cliente.delete(f"/documentos/{doc}", headers=CHAVE)
    assert "trinta dias" in _um(banco, "SELECT resposta FROM consulta")


def test_remover_um_documento_nao_toca_nos_outros(cliente, banco) -> None:
    # Mesmo nome em outra coleção: o texto citado do outro documento tem de continuar lá.
    a = _enviar(cliente, "prazos.txt", PRAZO, "a")
    b = _enviar(cliente, "prazos.txt", PRAZO + b"\n\nAnexo extra.", "b")
    _perguntar(cliente, colecoes=["b"])
    _perguntar(cliente, colecoes=["a"])

    cliente.delete(f"/documentos/{a}", headers=CHAVE)

    with banco() as s:
        fontes = [f[0] for f in s.execute(text("SELECT fontes FROM consulta ORDER BY id"))]
    assert fontes[0][0]["documento_id"] == b and "texto" in fontes[0][0]  # intacta
    assert fontes[1] == [{"n": 1, "removido": True}]
    assert _um(banco, "SELECT count(*) FROM trecho WHERE documento_id = :b", b=b) >= 1
    assert cliente.get(f"/documentos/{b}", headers=CHAVE).status_code == 200


def test_remocao_de_consulta_antiga_sem_documento_id(cliente, banco) -> None:
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    antigas = [{"n": 1, "trecho_id": 1, "documento": "prazos.txt", "pagina": 1, "texto": "segredo"}]
    with banco() as s:
        s.execute(
            text("INSERT INTO consulta (pergunta, resposta, fontes) VALUES ('p', 'r', :f)"),
            {"f": json.dumps(antigas)},
        )
        s.commit()
    cliente.delete(f"/documentos/{doc}", headers=CHAVE)
    assert _um(banco, "SELECT fontes FROM consulta") == [{"n": 1, "removido": True}]


def test_remover_documento_inexistente_e_exigir_chave(cliente) -> None:
    assert cliente.delete("/documentos/999", headers=CHAVE).status_code == 404
    assert cliente.delete("/documentos/1").status_code == 401
    assert cliente.delete("/documentos/1?consultas=outra", headers=CHAVE).status_code == 422


def test_reenviar_depois_de_remover_cria_documento_novo(cliente) -> None:
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    cliente.delete(f"/documentos/{doc}", headers=CHAVE)
    novo = cliente.post("/documentos", files={"arquivo": ("prazos.txt", PRAZO)}, headers=CHAVE)
    assert novo.status_code == 202 and novo.json()["situacao"] == "novo"


# ---------- limpeza de arquivo que falhou ----------


def test_limpeza_que_falha_fica_pendente_e_e_repetida(
    cliente,
    banco,
    ambiente,
    monkeypatch,
) -> None:
    doc = _enviar(cliente, "prazos.txt", PRAZO)
    pasta = ambiente / "arquivos" / str(doc)

    def falha(*_a, **_k):
        raise OSError("arquivo em uso")

    with monkeypatch.context() as m:
        m.setattr(remocao.shutil, "rmtree", falha)
        r = cliente.delete(f"/documentos/{doc}", headers=CHAVE).json()
    assert r["arquivo_removido"] is False
    assert pasta.exists()  # o arquivo ficou, mas o documento já não aparece para a busca
    assert _um(banco, "SELECT count(*) FROM trecho") == 0
    assert _um(banco, "SELECT limpeza_pendente FROM evento_documento WHERE tipo='removido'")

    feitas = remocao.limpar_pendentes(banco, get_ingestao_settings())
    assert feitas == 1 and not pasta.exists()
    assert not _um(banco, "SELECT limpeza_pendente FROM evento_documento WHERE tipo='removido'")
    assert remocao.limpar_pendentes(banco, get_ingestao_settings()) == 0


def test_limpeza_nunca_apaga_fora_da_pasta_de_arquivos(banco, tmp_path: Path) -> None:
    intocavel = tmp_path / "importante"
    intocavel.mkdir()
    (intocavel / "nao_apagar.txt").write_text("x")
    with banco() as s:
        s.execute(
            text(
                "INSERT INTO evento_documento (documento_id, nome, tipo, limpeza_pendente, "
                "caminho_arquivo) VALUES (1, 'x', 'removido', true, :p)"
            ),
            {"p": str(intocavel)},
        )
        s.commit()
    remocao.limpar_pendentes(banco, get_ingestao_settings())
    assert (intocavel / "nao_apagar.txt").exists()
    assert not _um(banco, "SELECT limpeza_pendente FROM evento_documento")


# ---------- retomada de pendentes e validação na subida ----------


def test_retomar_pendentes_processa_o_que_ficou_pelo_caminho(banco) -> None:
    config = get_ingestao_settings()
    with banco() as s:
        registro = servico.registrar(
            s, nome="a.txt", conteudo=PRAZO, colecao="geral", config=config
        )
    assert _um(banco, "SELECT estado FROM documento") == "pendente"  # o servidor "caiu" aqui

    assert manutencao.retomar_pendentes(banco, EmbeddingsSimulado(384), config) == 1
    assert _um(banco, "SELECT estado FROM documento WHERE id = :i", i=registro.documento_id) == (
        "processado"
    )
    assert manutencao.retomar_pendentes(banco, EmbeddingsSimulado(384), config) == 0


def test_a_subida_retoma_pendentes_e_repete_limpezas(banco, monkeypatch) -> None:
    config = get_ingestao_settings()
    with banco() as s:
        servico.registrar(s, nome="a.txt", conteudo=PRAZO, colecao="geral", config=config)
    monkeypatch.setenv("RETOMAR_PENDENTES", "true")
    get_ingestao_settings.cache_clear()
    obter_embeddings.cache_clear()
    try:
        with TestClient(create_app()):
            limite = time.time() + 15
            while time.time() < limite:
                if _um(banco, "SELECT estado FROM documento") == "processado":
                    break
                time.sleep(0.1)
            assert _um(banco, "SELECT estado FROM documento") == "processado"
    finally:
        monkeypatch.undo()
        get_ingestao_settings.cache_clear()
        obter_embeddings.cache_clear()


def test_configuracao_errada_derruba_a_subida(monkeypatch) -> None:
    monkeypatch.setenv("LIMIAR_SIMILARIDADE", "0,25")
    get_consulta_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="limiar_similaridade"), TestClient(create_app()):
            pass
    finally:
        monkeypatch.undo()
        get_consulta_settings.cache_clear()
