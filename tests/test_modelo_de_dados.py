"""Integração: aplica as migrações em um Postgres com pgvector e testa as garantias do modelo.

Roda só com TEST_DATABASE_URL (no CI, o service do Postgres). O schema é recriado do zero.
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db import get_engine

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="defina TEST_DATABASE_URL para rodar")

RAIZ = Path(__file__).resolve().parent.parent


def _vetor(i: int, dim: int = 384) -> str:
    v = [0.0] * dim
    v[i] = 1.0
    return "[" + ",".join(str(x) for x in v) + "]"


@pytest.fixture
def engine(monkeypatch):
    # A migração usa get_settings()/get_engine(), que são cacheados: aponta para o banco de teste.
    monkeypatch.setenv("DATABASE_URL", URL)
    get_settings.cache_clear()
    get_engine.cache_clear()
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    command.upgrade(cfg, "head")
    yield eng, cfg
    eng.dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()


def _documento(c, nome="a.pdf", hash_="a" * 64, colecao="geral") -> int:
    return c.execute(
        text(
            "INSERT INTO documento (nome, hash_conteudo, colecao, caminho_arquivo) "
            "VALUES (:n, :h, :c, '/x') RETURNING id"
        ),
        {"n": nome, "h": hash_, "c": colecao},
    ).scalar_one()


def _trecho(c, doc_id, pos=0, texto="o prazo de entrega é de trinta dias") -> int:
    return c.execute(
        text(
            "INSERT INTO trecho (documento_id, posicao, pagina, texto, vetor, "
            "modelo_embedding, dimensao) "
            "VALUES (:d, :p, 1, :t, CAST(:v AS vector), 'simulado', 384) RETURNING id"
        ),
        {"d": doc_id, "p": pos, "t": texto, "v": _vetor(pos)},
    ).scalar_one()


def test_tabelas_e_indices_existem(engine) -> None:
    eng, _ = engine
    with eng.connect() as c:
        tabelas = {
            r[0]
            for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        }
        indices = {
            r[0]
            for r in c.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'trecho'"))
        }
    assert {"documento", "trecho", "consulta", "evento_documento"} <= tabelas
    assert {"idx_trecho_vetor", "idx_trecho_texto"} <= indices


def test_apagar_documento_apaga_os_trechos(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        d = _documento(c)
        _trecho(c, d, 0)
        _trecho(c, d, 1)
    with eng.begin() as c:
        c.execute(text("DELETE FROM documento WHERE id = :d"), {"d": d})
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM trecho")).scalar_one() == 0


def test_hash_duplicado_na_mesma_colecao_e_recusado(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        _documento(c, "a.pdf", "a" * 64)
    with pytest.raises(IntegrityError), eng.begin() as c:
        _documento(c, "b.pdf", "a" * 64)
    with eng.begin() as c:  # outra coleção aceita o mesmo conteúdo
        _documento(c, "b.pdf", "a" * 64, colecao="outra")


def test_estado_invalido_e_recusado(engine) -> None:
    eng, _ = engine
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO documento (nome, hash_conteudo, estado, caminho_arquivo) "
                "VALUES ('a', :h, 'qualquer', '/x')"
            ),
            {"h": "a" * 64},
        )


def test_vetor_de_outra_dimensao_e_recusado(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        d = _documento(c)
    with pytest.raises(DBAPIError), eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO trecho (documento_id, posicao, texto, vetor, modelo_embedding, "
                "dimensao) VALUES (:d, 0, 't', CAST(:v AS vector), 'm', 8)"
            ),
            {"d": d, "v": _vetor(0, dim=8)},
        )


def test_busca_vetorial_devolve_o_mais_proximo(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        d = _documento(c)
        _trecho(c, d, 0)
        alvo = _trecho(c, d, 1)
    with eng.connect() as c:
        achado = c.execute(
            text("SELECT id FROM trecho ORDER BY vetor <=> CAST(:v AS vector) LIMIT 1"),
            {"v": _vetor(1)},
        ).scalar_one()
    assert achado == alvo


def test_busca_textual_em_portugues_usa_radical(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        d = _documento(c)
        _trecho(c, d, 0, "As entregas ocorrem em trinta dias")
        _trecho(c, d, 1, "A garantia cobre defeitos")
    with eng.connect() as c:
        textos = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT texto FROM trecho WHERE to_tsvector('portuguese', texto) "
                    "@@ plainto_tsquery('portuguese', 'entrega')"
                )
            )
        ]
    assert textos == ["As entregas ocorrem em trinta dias"]


def test_consulta_e_evento_sobrevivem_a_remocao_do_documento(engine) -> None:
    eng, _ = engine
    with eng.begin() as c:
        d = _documento(c)
        _trecho(c, d)
        c.execute(
            text(
                "INSERT INTO consulta (pergunta, resposta, fontes) VALUES "
                "('p', 'r', CAST(:f AS jsonb))"
            ),
            {"f": '[{"documento": "a.pdf", "pagina": 1, "texto": "citado"}]'},
        )
        c.execute(
            text(
                "INSERT INTO evento_documento (documento_id, nome, tipo) "
                "VALUES (:d, 'a.pdf', 'removido')"
            ),
            {"d": d},
        )
    with eng.begin() as c:
        c.execute(text("DELETE FROM documento WHERE id = :d"), {"d": d})
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM consulta")).scalar_one() == 1
        assert c.execute(text("SELECT count(*) FROM evento_documento")).scalar_one() == 1


def test_downgrade_remove_as_tabelas_e_upgrade_repete(engine) -> None:
    eng, cfg = engine
    command.downgrade(cfg, "0001")
    with eng.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_tables WHERE tablename = 'trecho'")
            ).scalar_one()
            == 0
        )
    command.upgrade(cfg, "head")
