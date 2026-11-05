"""Andamento da ingestão: percentual, etapa e tempo estimado."""

# ruff: noqa: F811

from collections.abc import Sequence

import pytest
from auxiliares import ambiente, banco, precisa_de_banco  # noqa: F401
from sqlalchemy import text

from app.ingestao import servico
from app.ingestao.config import get_ingestao_settings
from app.llm.simulado import EmbeddingsSimulado
from app.web.formato import restante

pytestmark = precisa_de_banco

TEXTO = "\n\n".join(f"Parágrafo {n}: " + f"palavra distinta número {n} " * 40 for n in range(60))


class EmbeddingsQueObserva(EmbeddingsSimulado):
    """Lê o progresso gravado no banco a cada lote de vetores."""

    def __init__(self, banco, dimensao: int = 768) -> None:
        super().__init__(dimensao)
        self._banco = banco
        self.vistos: list[tuple[int | None, str | None]] = []

    def gerar_trechos(self, textos: Sequence[str]) -> list[list[float]]:
        with self._banco() as s:
            self.vistos.append(
                tuple(s.execute(text("SELECT progresso, etapa FROM documento")).one())
            )
        return super().gerar_trechos(textos)


def _registrar(banco, nome="longo.txt", conteudo=None):
    conteudo = TEXTO.encode() if conteudo is None else conteudo
    with banco() as s:
        return servico.registrar(
            s, nome=nome, conteudo=conteudo, colecao="geral", config=get_ingestao_settings()
        )


def _linha(banco):
    with banco() as s:
        return s.execute(
            text("SELECT estado, progresso, etapa, processando_desde FROM documento")
        ).one()


def test_o_progresso_sobe_em_lotes_e_some_ao_terminar(banco) -> None:
    reg = _registrar(banco)
    assert _linha(banco)[1:] == (None, None, None)  # na fila: sem andamento
    emb = EmbeddingsQueObserva(banco)
    servico.processar(banco, reg.documento_id, emb, get_ingestao_settings())

    pcts = [p for p, _ in emb.vistos]
    assert len(pcts) > 2, "o texto precisa render vários lotes"
    assert pcts == sorted(pcts) and pcts[0] == servico._PCT_DEPOIS_DE_LER
    assert pcts[-1] < servico._PCT_FIM_DOS_VETORES
    assert all("Gerando vetores" in (e or "") for _, e in emb.vistos)
    assert _linha(banco) == ("processado", None, None, None)


def test_falha_limpa_o_progresso(banco) -> None:
    reg = _registrar(banco, "vazio.txt", b"   \n  ")
    servico.processar(banco, reg.documento_id, EmbeddingsSimulado(768), get_ingestao_settings())
    assert _linha(banco) == ("falhou", None, None, None)


def test_reprocessar_zera_um_andamento_antigo(banco) -> None:
    reg = _registrar(banco)
    with banco() as s:
        s.execute(
            text("UPDATE documento SET progresso = 60, etapa = 'velho', processando_desde = now()")
        )
        s.commit()
        assert servico.marcar_para_reprocessar(s, reg.documento_id)
    assert _linha(banco)[1:] == (None, None, None)


def test_o_progresso_nao_mexe_na_ordem_da_lista(banco) -> None:
    reg = _registrar(banco)
    with banco() as s:
        antes = s.execute(text("SELECT atualizado_em FROM documento")).scalar_one()
    servico._progresso(banco, reg.documento_id, 40, "x")
    with banco() as s:
        assert s.execute(text("SELECT atualizado_em FROM documento")).scalar_one() == antes


def test_estimativa_de_tempo_so_depois_de_15_por_cento(banco) -> None:
    from app.routers.documentos import listar_documentos

    reg = _registrar(banco)
    servico._progresso(banco, reg.documento_id, 10, "a", inicio=True)
    with banco() as s:
        item = listar_documentos(s)["itens"][0]
        assert item["progresso"] == 10 and item["restante_s"] is None
        s.execute(
            text(
                "UPDATE documento SET progresso = 50, "
                "processando_desde = now() - interval '100 seconds'"
            )
        )
        s.commit()
        item = listar_documentos(s)["itens"][0]
        assert 95 <= item["restante_s"] <= 110  # metade feita em 100 s: faltam ~100 s


@pytest.mark.parametrize(
    ("segundos", "texto"),
    [(None, ""), (10, "menos de 1 min"), (170, "cerca de 3 min"), (5400, "cerca de 1 h 30 min")],
)
def test_texto_do_tempo_restante(segundos, texto) -> None:
    assert restante(segundos) == texto
