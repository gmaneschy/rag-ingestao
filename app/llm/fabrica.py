"""Escolhe a implementação de cada interface a partir da configuração."""

from app.ingestao.config import get_ingestao_settings
from app.llm.config import LLMSettings, get_llm_settings
from app.llm.local import EmbeddingsLocal
from app.llm.ollama import GeradorDeRespostaOllama
from app.llm.protocolos import GeradorDeEmbeddings, GeradorDeResposta
from app.llm.simulado import EmbeddingsSimulado, GeradorDeRespostaSimulado


class ProvedorNaoImplementado(RuntimeError):
    """O provedor existe na configuração, mas a implementação ainda não foi escrita."""


def criar_embeddings(settings: LLMSettings | None = None) -> GeradorDeEmbeddings:
    settings = settings or get_llm_settings()
    if settings.embeddings_provider == "simulado":
        return EmbeddingsSimulado(dimensao=settings.embeddings_dim)
    if settings.embeddings_provider == "local":
        return EmbeddingsLocal(
            nome_modelo=settings.embeddings_model,
            dimensao=settings.embeddings_dim,
            cache_dir=get_ingestao_settings().modelos_dir,
        )
    raise ProvedorNaoImplementado(
        f"EMBEDDINGS_PROVIDER={settings.embeddings_provider!r} ainda não existe (api: mais tarde)"
    )


def criar_gerador_de_resposta(settings: LLMSettings | None = None) -> GeradorDeResposta:
    settings = settings or get_llm_settings()
    if settings.llm_provider == "simulado":
        return GeradorDeRespostaSimulado()
    if settings.llm_provider == "ollama":
        return GeradorDeRespostaOllama(
            base_url=settings.llm_base_url,
            modelo=settings.llm_model,
            max_tokens=min(settings.llm_max_tokens_saida, settings.max_tokens_por_pergunta),
            timeout_s=settings.llm_timeout_s,
            num_ctx_max=settings.llm_num_ctx_max,
        )
    raise ProvedorNaoImplementado(
        f"LLM_PROVIDER={settings.llm_provider!r} ainda não existe (api: mais tarde)"
    )
