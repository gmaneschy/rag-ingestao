"""POST /perguntas com tipo=analise, de ponta a ponta."""

# ruff: noqa: F811

import pytest
from auxiliares import ambiente, banco, pdf_com_texto, precisa_de_banco  # noqa: F401
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.consulta import servico
from app.consulta.config import get_consulta_settings
from app.llm.config import get_llm_settings
from app.llm.tipos import Resposta
from app.main import create_app
from app.routers.documentos import obter_embeddings
from app.routers.perguntas import obter_gerador

pytestmark = precisa_de_banco

CHAVE = {"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
TEXTO = (
    "# Contrato\n\nO prazo de entrega é de trinta dias úteis.\n\n"
    "## Garantia\n\nA garantia cobre defeitos de fabricação por um ano."
).encode()


class GeradorFalso:
    nome_modelo = "falso"

    def __init__(self, resposta: Resposta) -> None:
        self.resposta = resposta
        self.chamadas: list[str] = []

    def responder(self, pergunta, trechos, *, tipo="factual"):
        self.chamadas.append(tipo)
        return self.resposta


@pytest.fixture
def cliente(banco):
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        c.app_ref = app
        yield c
    app.dependency_overrides.clear()
    obter_embeddings.cache_clear()
    obter_gerador.cache_clear()


def _enviar(cliente, nome="contrato.md", conteudo=TEXTO) -> int:
    r = cliente.post("/documentos", files={"arquivo": (nome, conteudo)}, headers=CHAVE)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _perguntar(cliente, pergunta="Qual é o prazo de entrega?", **extra):
    r = cliente.post("/perguntas", json={"pergunta": pergunta, **extra}, headers=CHAVE)
    assert r.status_code == 200, r.text
    return r.json()


def test_analise_devolve_as_duas_partes_a_base_e_o_aviso(cliente, banco) -> None:
    _enviar(cliente)
    r = _perguntar(cliente, tipo="analise")
    assert r["tipo"] == "analise" and r["recusou"] is False
    assert r["resposta"].startswith("Evidências:") and "\n\nAvaliação:\n" in r["resposta"]
    analise = r["analise"]
    assert analise["separou"] is True and analise["evidencias"] and analise["avaliacao"]
    assert analise["base_trechos"] == len(r["trechos_usados"]) >= 1
    assert analise["base_paginas"] == []  # markdown não tem página
    assert r["fontes"] and r["fontes"][0]["secao"] is not None
    assert r["aviso"] == servico.AVISO_AVALIACAO
    with banco() as s:
        assert (
            s.execute(text("SELECT tipo FROM consulta WHERE id = :i"), {"i": r["id"]}).scalar_one()
            == "analise"
        )


def test_o_factual_nao_ganha_campos_de_analise(cliente, banco) -> None:
    _enviar(cliente)
    r = _perguntar(cliente)
    assert r["tipo"] == "factual" and r["analise"] is None and r["aviso"] is None
    assert not r["resposta"].startswith("Evidências:")
    with banco() as s:
        assert s.execute(text("SELECT tipo FROM consulta")).scalar_one() == "factual"


def test_base_em_paginas_para_pdf(cliente) -> None:
    paginas = [f"Pagina {i} fala do prazo de entrega numero {i}." for i in range(1, 4)]
    _enviar(cliente, "livro.pdf", pdf_com_texto(paginas))
    r = _perguntar(cliente, "prazo de entrega", tipo="analise", k=2)
    assert r["analise"]["base_trechos"] == 2
    assert len(r["analise"]["base_paginas"]) == 2


def test_a_analise_usa_mais_trechos_por_padrao(cliente) -> None:
    paginas = [f"Pagina {i} fala do prazo de entrega numero {i}." for i in range(1, 13)]
    _enviar(cliente, "livro.pdf", pdf_com_texto(paginas))
    factual = _perguntar(cliente, "prazo de entrega")
    analise = _perguntar(cliente, "prazo de entrega", tipo="analise")
    assert len(factual["trechos_usados"]) == 5
    assert len(analise["trechos_usados"]) == 8
    assert len(_perguntar(cliente, "prazo de entrega", tipo="analise", k=3)["trechos_usados"]) == 3


def test_analise_sem_evidencia_recusa_e_nao_chama_o_modelo(cliente) -> None:
    gerador = GeradorFalso(Resposta("não deveria ser chamado"))
    cliente.app_ref.dependency_overrides[obter_gerador] = lambda: gerador
    r = _perguntar(cliente, "zzz qqq", tipo="analise")  # corpus vazio
    assert r["recusou"] is True and r["analise"] is None and r["trechos_usados"] == []
    assert gerador.chamadas == []


def test_so_o_tipo_analise_chega_ao_gerador(cliente) -> None:
    _enviar(cliente)
    gerador = GeradorFalso(
        Resposta("Evidências:\nx [1]\n\nAvaliação:\ny", (1,), evidencias="x [1]", avaliacao="y")
    )
    cliente.app_ref.dependency_overrides[obter_gerador] = lambda: gerador
    _perguntar(cliente, tipo="analise")
    _perguntar(cliente)
    assert gerador.chamadas == ["analise", "factual"]


def test_aviso_quando_o_modelo_nao_separa_as_partes(cliente) -> None:
    _enviar(cliente)
    gerador = GeradorFalso(
        Resposta(
            "O prazo é de trinta dias [1].",
            (1,),
            evidencias="O prazo é de trinta dias [1].",
            avaliacao=None,
        )
    )
    cliente.app_ref.dependency_overrides[obter_gerador] = lambda: gerador
    r = _perguntar(cliente, tipo="analise")
    assert r["analise"]["separou"] is False and r["analise"]["avaliacao"] is None
    assert r["aviso"] == servico.AVISO_SEM_SEPARACAO


def test_aviso_quando_as_evidencias_nao_citam(cliente) -> None:
    _enviar(cliente)
    gerador = GeradorFalso(
        Resposta(
            "Evidências:\nalgo\n\nAvaliação:\nopinião", (), evidencias="algo", avaliacao="opinião"
        )
    )
    cliente.app_ref.dependency_overrides[obter_gerador] = lambda: gerador
    r = _perguntar(cliente, tipo="analise")
    assert r["fontes"] == []
    assert r["aviso"] == f"{servico.AVISO_SEM_CITACAO} {servico.AVISO_AVALIACAO}"


def test_tipo_invalido_e_recusado(cliente) -> None:
    r = cliente.post("/perguntas", json={"pergunta": "qualquer", "tipo": "opiniao"}, headers=CHAVE)
    assert r.status_code == 422


def test_filtros_valem_na_analise(cliente, banco) -> None:
    _enviar(cliente)
    with banco() as session:
        r = servico.perguntar(
            session,
            "O que garante o contrato? defeitos",
            embeddings=obter_embeddings(),
            gerador=obter_gerador(),
            llm=get_llm_settings(),
            consulta=get_consulta_settings(),
            tipo="analise",
            secao="garantia",
        )
    assert {t["secao"] for t in r.trechos_usados} == {"Contrato > Garantia"}


def test_a_coluna_tipo_so_aceita_os_dois_valores(banco) -> None:
    with banco() as s:
        s.execute(text("INSERT INTO consulta (pergunta, resposta) VALUES ('p', 'r')"))
        assert s.execute(text("SELECT tipo FROM consulta")).scalar_one() == "factual"
        s.commit()
        with pytest.raises(IntegrityError):
            s.execute(
                text("INSERT INTO consulta (pergunta, resposta, tipo) VALUES ('p', 'r', 'x')")
            )
