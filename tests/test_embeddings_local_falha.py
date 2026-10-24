"""A falha de download vira um erro claro, sem precisar de rede nem do modelo."""

import sys
import types

import pytest

from app.llm.local import EmbeddingsLocal
from app.llm.tipos import ModeloIndisponivel


def test_falha_de_download_vira_modelo_indisponivel(monkeypatch) -> None:
    def quebra(**_):
        raise ValueError("429 Too Many Requests")

    falso = types.SimpleNamespace(TextEmbedding=quebra)
    monkeypatch.setitem(sys.modules, "fastembed", falso)
    embeddings = EmbeddingsLocal("modelo/qualquer", 384)
    with pytest.raises(ModeloIndisponivel, match="ValueError"):
        embeddings.gerar_consulta("oi")


def test_depois_da_falha_a_proxima_chamada_tenta_de_novo(monkeypatch) -> None:
    chamadas = []

    class Modelo:
        def __init__(self, **_):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise OSError("sem rede")

        def embed(self, textos, batch_size=32):
            import numpy as np

            return (np.zeros(384) for _ in textos)

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=Modelo))
    embeddings = EmbeddingsLocal("modelo/qualquer", 384)
    with pytest.raises(ModeloIndisponivel):
        embeddings.gerar_consulta("oi")
    assert len(embeddings.gerar_consulta("oi")) == 384
    assert len(chamadas) == 2
