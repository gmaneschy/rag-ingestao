"""Apoio aos testes da ingestão: PDFs de verdade, geração de textos e banco limpo."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.consulta.config import get_consulta_settings
from app.db import get_engine, get_sessionmaker
from app.ingestao.config import get_ingestao_settings
from app.llm.config import get_llm_settings

RAIZ = Path(__file__).resolve().parent.parent


def pdf_com_texto(paginas: list[str]) -> bytes:
    """Monta um PDF mínimo e válido, uma linha de texto (ASCII) por página; "" = página vazia."""
    objetos: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    ids_paginas = []
    for i, texto in enumerate(paginas):
        pagina_id = 4 + i * 2
        ids_paginas.append(pagina_id)
        escapado = texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        conteudo = f"BT /F1 12 Tf 50 700 Td ({escapado}) Tj ET".encode() if texto else b""
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {pagina_id + 1} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objetos.append(b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream")
    kids = " ".join(f"{i} 0 R" for i in ids_paginas)
    objetos[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(paginas)} >>".encode()
    objetos.insert(2, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for p in posicoes:
        saida += f"{p:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)


URL = os.environ.get("TEST_DATABASE_URL")
precisa_de_banco = pytest.mark.skipif(not URL, reason="defina TEST_DATABASE_URL para rodar")


@pytest.fixture(scope="module")
def ambiente(tmp_path_factory) -> Iterator[Path]:
    """Banco de teste migrado uma vez por módulo e pastas de dados temporárias."""
    if not URL:
        pytest.skip("defina TEST_DATABASE_URL para rodar")
    dados = tmp_path_factory.mktemp("dados")
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", URL)
    mp.setenv("DADOS_DIR", str(dados))
    mp.setenv("MODELOS_DIR", str(dados / "modelos"))
    mp.setenv("EMBEDDINGS_PROVIDER", "simulado")
    mp.setenv("LLM_PROVIDER", "simulado")
    mp.setenv("LLM_MODEL", "")
    mp.setenv("RETOMAR_PENDENTES", "false")  # os testes decidem quando retomar
    for fn in (
        get_settings,
        get_engine,
        get_sessionmaker,
        get_ingestao_settings,
        get_llm_settings,
        get_consulta_settings,
    ):
        fn.cache_clear()

    eng = create_engine(URL, connect_args={"connect_timeout": 5})
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "migrations"))
    command.upgrade(cfg, "head")
    yield dados

    get_engine().dispose()
    mp.undo()
    for fn in (
        get_settings,
        get_engine,
        get_sessionmaker,
        get_ingestao_settings,
        get_llm_settings,
        get_consulta_settings,
    ):
        fn.cache_clear()


@pytest.fixture
def banco(ambiente):
    """Tabelas vazias a cada teste."""
    with get_engine().begin() as c:
        c.execute(
            text("TRUNCATE documento, trecho, consulta, evento_documento RESTART IDENTITY CASCADE")
        )
    yield get_sessionmaker()
