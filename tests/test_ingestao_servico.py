"""Integração: registro e processamento contra um Postgres com pgvector."""

from pathlib import Path

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from sqlalchemy import text

from app.ingestao import servico
from app.ingestao.config import get_ingestao_settings
from app.llm.simulado import EmbeddingsSimulado

pytestmark = precisa_de_banco

TEXTO_A = "O prazo de entrega é de trinta dias úteis.\n\nA garantia cobre defeitos por um ano."
TEXTO_B = "A política de devolução aceita trocas em até sete dias.\n\nO frete é gratuito."


@pytest.fixture
def emb() -> EmbeddingsSimulado:
    return EmbeddingsSimulado(384)


def _registrar(banco, nome: str, conteudo: bytes, colecao: str = "geral"):  # noqa: F811
    with banco() as s:
        return servico.registrar(
            s, nome=nome, conteudo=conteudo, colecao=colecao, config=get_ingestao_settings()
        )


def _processar(banco, registro, emb) -> None:  # noqa: F811
    servico.processar(banco, registro.documento_id, emb, get_ingestao_settings())


def _um(banco, sql: str, **p):  # noqa: F811
    with banco() as s:
        return s.execute(text(sql), p).scalar_one()


def test_ingere_um_documento_do_inicio_ao_fim(banco, emb) -> None:  # noqa: F811
    reg = _registrar(banco, "contrato.txt", TEXTO_A.encode())
    assert (reg.situacao, reg.estado) == ("novo", "pendente")
    assert Path(_um(banco, "SELECT caminho_arquivo FROM documento")).read_text() == TEXTO_A

    _processar(banco, reg, emb)

    assert _um(banco, "SELECT estado FROM documento") == "processado"
    assert _um(banco, "SELECT count(*) FROM trecho") >= 1
    with banco() as s:
        modelo, dim = s.execute(text("SELECT modelo_embedding, dimensao FROM trecho")).first()
    assert (modelo, dim) == ("simulado-hash-v1", 384)
    assert _um(banco, "SELECT tipo FROM evento_documento") == "ingerido"


def test_pdf_guarda_a_pagina_de_cada_trecho(banco, emb) -> None:  # noqa: F811
    reg = _registrar(banco, "a.pdf", pdf_com_texto(["pagina um", "", "pagina tres"]))
    _processar(banco, reg, emb)
    with banco() as s:
        paginas = [r[0] for r in s.execute(text("SELECT pagina FROM trecho ORDER BY posicao"))]
    assert paginas == [1, 3]


def test_reenviar_o_mesmo_arquivo_nao_duplica(banco, emb) -> None:  # noqa: F811
    primeiro = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, primeiro, emb)
    trechos = _um(banco, "SELECT count(*) FROM trecho")

    de_novo = _registrar(banco, "a.txt", TEXTO_A.encode())
    assert (de_novo.situacao, de_novo.documento_id) == ("duplicado", primeiro.documento_id)
    assert not de_novo.precisa_processar
    assert _um(banco, "SELECT count(*) FROM trecho") == trechos
    assert _um(banco, "SELECT count(*) FROM documento") == 1


def test_mesmo_conteudo_com_outro_nome_tambem_e_duplicado(banco, emb) -> None:  # noqa: F811
    primeiro = _registrar(banco, "a.txt", TEXTO_A.encode())
    copia = _registrar(banco, "copia-de-a.txt", TEXTO_A.encode())
    assert copia.situacao == "duplicado" and copia.documento_id == primeiro.documento_id


def test_mesmo_conteudo_em_outra_colecao_e_outro_documento(banco, emb) -> None:  # noqa: F811
    a = _registrar(banco, "a.txt", TEXTO_A.encode(), "x")
    b = _registrar(banco, "a.txt", TEXTO_A.encode(), "y")
    assert a.documento_id != b.documento_id


def test_processar_duas_vezes_da_o_mesmo_resultado(banco, emb) -> None:  # noqa: F811
    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, emb)
    antes = _um(banco, "SELECT count(*) FROM trecho")
    _processar(banco, reg, emb)
    assert _um(banco, "SELECT count(*) FROM trecho") == antes


def test_versao_nova_substitui_a_anterior(banco, emb) -> None:  # noqa: F811
    v1 = _registrar(banco, "manual.txt", TEXTO_A.encode())
    _processar(banco, v1, emb)
    arquivo_v1 = Path(_um(banco, "SELECT caminho_arquivo FROM documento"))

    v2 = _registrar(banco, "manual.txt", TEXTO_B.encode())
    assert (v2.situacao, v2.documento_id) == ("nova_versao", v1.documento_id)
    assert _um(banco, "SELECT versao FROM documento") == 2
    assert not arquivo_v1.exists()  # a versão antiga do arquivo saiu

    _processar(banco, v2, emb)
    with banco() as s:
        textos = " ".join(r[0] for r in s.execute(text("SELECT texto FROM trecho")))
    assert "devolução" in textos and "prazo de entrega" not in textos
    assert _um(banco, "SELECT count(*) FROM documento") == 1
    with banco() as s:
        eventos = [r[0] for r in s.execute(text("SELECT tipo FROM evento_documento ORDER BY id"))]
    assert eventos == ["ingerido", "reprocessado"]


def test_falha_na_versao_nova_mantem_o_texto_antigo(banco, emb) -> None:  # noqa: F811
    v1 = _registrar(banco, "a.pdf", pdf_com_texto(["conteudo antigo valido"]))
    _processar(banco, v1, emb)
    antigos = _um(banco, "SELECT count(*) FROM trecho")

    v2 = _registrar(banco, "a.pdf", pdf_com_texto(["", ""]))  # sem texto: falhará
    _processar(banco, v2, emb)

    assert _um(banco, "SELECT estado FROM documento") == "falhou"
    assert "OCR" in _um(banco, "SELECT mensagem_erro FROM documento")
    assert _um(banco, "SELECT count(*) FROM trecho") == antigos
    with banco() as s:
        assert "antigo" in s.execute(text("SELECT texto FROM trecho")).scalar_one()


def test_falha_do_gerador_de_embeddings_nao_deixa_meio_documento(banco) -> None:  # noqa: F811
    class Quebrado(EmbeddingsSimulado):
        def gerar_trechos(self, textos):
            raise RuntimeError("modelo indisponível")

    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, Quebrado(384))
    assert _um(banco, "SELECT estado FROM documento") == "falhou"
    assert _um(banco, "SELECT mensagem_erro FROM documento") == "Erro interno (RuntimeError)"
    assert _um(banco, "SELECT count(*) FROM trecho") == 0


def test_documento_falho_pode_ser_reenviado_e_recuperado(banco, emb) -> None:  # noqa: F811
    class Quebrado(EmbeddingsSimulado):
        def gerar_trechos(self, textos):
            raise RuntimeError("fora")

    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, Quebrado(384))
    de_novo = _registrar(banco, "a.txt", TEXTO_A.encode())
    assert de_novo.situacao == "duplicado" and de_novo.precisa_processar
    # O erro antigo some assim que a nova tentativa é aceita, antes mesmo de processar.
    assert de_novo.estado == "pendente"
    assert _um(banco, "SELECT estado FROM documento") == "pendente"
    assert _um(banco, "SELECT mensagem_erro FROM documento") is None
    _processar(banco, de_novo, emb)
    assert _um(banco, "SELECT estado FROM documento") == "processado"


def test_dimensao_incompativel_e_recusada(banco) -> None:  # noqa: F811
    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, EmbeddingsSimulado(128))
    assert _um(banco, "SELECT estado FROM documento") == "falhou"
    assert "384" in _um(banco, "SELECT mensagem_erro FROM documento")


def test_versao_mais_nova_no_meio_do_caminho_nao_e_sobrescrita(banco, emb, monkeypatch) -> None:  # noqa: F811
    """Se chega a v2 enquanto a v1 ainda processa, a v1 descarta o resultado."""
    v1 = _registrar(banco, "a.txt", TEXTO_A.encode())
    original = emb.gerar_trechos

    def com_versao_nova_no_meio(textos):
        _registrar(banco, "a.txt", TEXTO_B.encode())
        return original(textos)

    monkeypatch.setattr(emb, "gerar_trechos", com_versao_nova_no_meio)
    _processar(banco, v1, emb)
    assert _um(banco, "SELECT count(*) FROM trecho") == 0  # a v1 não gravou nada
    assert _um(banco, "SELECT estado FROM documento") == "pendente"  # a v2 é quem processa


def test_documento_removido_antes_de_processar_e_ignorado(banco, emb) -> None:  # noqa: F811
    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    with banco() as s:
        s.execute(text("DELETE FROM documento"))
        s.commit()
    _processar(banco, reg, emb)  # não levanta
    assert _um(banco, "SELECT count(*) FROM trecho") == 0


@pytest.mark.parametrize(
    ("nome", "conteudo"),
    [
        ("a.exe", b"MZ"),
        ("vazio.txt", b""),
        ("falso.pdf", b"nao sou pdf"),
        ("", b"x"),
        ("..", b"x"),
    ],
)
def test_entradas_invalidas(banco, nome, conteudo) -> None:  # noqa: F811
    with pytest.raises(servico.ArquivoInvalido):
        _registrar(banco, nome, conteudo)
    assert _um(banco, "SELECT count(*) FROM documento") == 0


def test_nome_com_diretorios_e_reduzido_ao_arquivo(banco) -> None:  # noqa: F811
    reg = _registrar(banco, "../../etc/segredos.txt", b"conteudo")
    assert (
        _um(banco, "SELECT nome FROM documento WHERE id = :i", i=reg.documento_id) == "segredos.txt"
    )
    caminho = Path(_um(banco, "SELECT caminho_arquivo FROM documento"))
    assert get_ingestao_settings().arquivos_dir in caminho.parents


def test_arquivo_acima_do_limite(banco) -> None:  # noqa: F811
    grande = b"a" * (get_ingestao_settings().tamanho_max_bytes + 1)
    with pytest.raises(servico.ArquivoInvalido, match="MB"):
        _registrar(banco, "a.txt", grande)


def test_modelo_indisponivel_vira_falha_com_mensagem_clara(banco) -> None:  # noqa: F811
    from app.llm.tipos import ModeloIndisponivel

    class SemModelo(EmbeddingsSimulado):
        def gerar_trechos(self, textos):
            raise ModeloIndisponivel("Não consegui carregar o modelo x: ValueError")

    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, SemModelo(384))
    assert _um(banco, "SELECT estado FROM documento") == "falhou"
    erro = _um(banco, "SELECT mensagem_erro FROM documento")
    assert "download" in erro and "reenvie" in erro


def test_reprocessar_registra_evento_e_nao_duplica(banco, emb) -> None:  # noqa: F811
    reg = _registrar(banco, "a.txt", TEXTO_A.encode())
    _processar(banco, reg, emb)
    trechos = _um(banco, "SELECT count(*) FROM trecho")
    with banco() as s:
        assert servico.marcar_para_reprocessar(s, reg.documento_id)
        assert not servico.marcar_para_reprocessar(s, 999)
    assert _um(banco, "SELECT estado FROM documento") == "pendente"
    _processar(banco, reg, emb)
    assert _um(banco, "SELECT count(*) FROM trecho") == trechos
    with banco() as s:
        eventos = [r[0] for r in s.execute(text("SELECT tipo FROM evento_documento ORDER BY id"))]
    assert eventos == ["ingerido", "reprocessado"]
