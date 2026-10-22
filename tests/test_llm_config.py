import pytest
from pydantic import ValidationError

from app.llm.config import LLMSettings
from app.llm.fabrica import ProvedorNaoImplementado, criar_embeddings, criar_gerador_de_resposta
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch) -> None:
    for nome in (
        "EMBEDDINGS_PROVIDER",
        "LLM_PROVIDER",
        "EMBEDDINGS_DIM",
        "LLM_API_KEY",
        "MAX_TOKENS_POR_PERGUNTA",
        "LIMITE_DIARIO_GASTO",
    ):
        monkeypatch.delenv(nome, raising=False)


def _novo(**kw) -> LLMSettings:
    return LLMSettings(_env_file=None, **kw)


def test_padrao_e_simulado() -> None:
    s = _novo()
    assert (s.embeddings_provider, s.llm_provider) == ("simulado", "simulado")
    assert isinstance(criar_embeddings(s), EmbeddingsSimulado)
    assert isinstance(criar_gerador_de_resposta(s), GeradorDeRespostaSimulado)


def test_variaveis_de_ambiente_escolhem_o_provedor(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "local")
    assert _novo().embeddings_provider == "local"


def test_provedor_desconhecido_e_recusado() -> None:
    with pytest.raises(ValidationError):
        _novo(embeddings_provider="inventado")


def test_dimensao_incompativel_com_a_coluna_e_recusada() -> None:
    with pytest.raises(ValidationError, match="384"):
        _novo(embeddings_dim=768)


def test_api_exige_chave() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        _novo(llm_provider="api")
    assert _novo(llm_provider="api", llm_api_key="segredo").llm_provider == "api"


def test_limites_precisam_ser_validos() -> None:
    with pytest.raises(ValidationError):
        _novo(max_tokens_por_pergunta=0)
    with pytest.raises(ValidationError):
        _novo(limite_diario_gasto=-1)


def test_chave_nao_vaza_na_representacao() -> None:
    assert "segredo-xyz" not in repr(_novo(llm_provider="api", llm_api_key="segredo-xyz"))


def test_provedor_ainda_nao_implementado_falha_com_mensagem_clara() -> None:
    with pytest.raises(ProvedorNaoImplementado, match="local"):
        criar_embeddings(_novo(embeddings_provider="local"))
    with pytest.raises(ProvedorNaoImplementado, match="api"):
        criar_gerador_de_resposta(_novo(llm_provider="api", llm_api_key="x"))
