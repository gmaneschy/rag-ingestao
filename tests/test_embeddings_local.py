"""Teste do modelo real. Baixa ~220 MB na primeira vez, então só roda sob pedido:

$env:TEST_MODELO_LOCAL = "1"
uv run pytest tests/test_embeddings_local.py -v
"""

import os
from pathlib import Path

import pytest

from app.llm.config import LLMSettings
from app.llm.fabrica import criar_embeddings

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_MODELO_LOCAL") != "1", reason="defina TEST_MODELO_LOCAL=1 para rodar"
)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def embeddings(tmp_path_factory):
    cache = Path(os.environ.get("MODELOS_DIR", tmp_path_factory.mktemp("modelos")))
    from app.llm.local import EmbeddingsLocal

    settings = LLMSettings(_env_file=None, embeddings_provider="local")
    return EmbeddingsLocal(settings.embeddings_model, settings.embeddings_dim, cache)


def test_a_fabrica_cria_o_provedor_local() -> None:
    assert (
        criar_embeddings(LLMSettings(_env_file=None, embeddings_provider="local")).dimensao == 384
    )


def test_dimensao_real_bate_com_a_coluna(embeddings) -> None:
    vetores = embeddings.gerar_trechos(["O prazo de entrega é de trinta dias."])
    assert len(vetores) == 1 and len(vetores[0]) == 384


def test_portugues_e_ingles_sobre_o_mesmo_assunto_ficam_proximos(embeddings) -> None:
    pergunta = embeddings.gerar_consulta("Qual é o prazo de entrega?")
    pt = embeddings.gerar_trechos(["O prazo de entrega é de trinta dias úteis."])[0]
    en = embeddings.gerar_trechos(["Delivery takes thirty business days."])[0]
    fora = embeddings.gerar_trechos(["Receita de bolo de cenoura com cobertura de chocolate."])[0]
    assert _cos(pergunta, pt) > _cos(pergunta, fora)
    assert _cos(pergunta, en) > _cos(pergunta, fora)
