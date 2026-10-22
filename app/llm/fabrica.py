"""Escolhe a implementação de cada interface a partir da configuração."""

from app.llm.config import LLMSettings, get_llm_settings
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado


class ProvedorNaoImplementado(RuntimeError):
    """O provedor existe na configuração, mas a implementação ainda não foi escrita."""


def criar_embeddings(settings: LLMSettings | None = None) -> GeradorDeEmbeddings:
    settings = settings or get_llm_settings()
    if settings.embeddings_provider == "simulado":
        return EmbeddingsSimulado(dimensao=settings.embeddings_dim)
    raise ProvedorNaoImplementado(
        f"EMBEDDINGS_PROVIDER={settings.embeddings_provider!r} ainda não existe "
        "(local: etapa 2; api: etapa 2 ou 5)"
    )


def criar_gerador_de_resposta(settings: LLMSettings | None = None) -> GeradorDeResposta:
    settings = settings or get_llm_settings()
    if settings.llm_provider == "simulado":
        return GeradorDeRespostaSimulado()
    raise ProvedorNaoImplementado(
        f"LLM_PROVIDER={settings.llm_provider!r} ainda não existe (etapas 3 e 5)"
    )
