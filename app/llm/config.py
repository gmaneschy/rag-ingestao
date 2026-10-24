"""Configuração dos provedores de embeddings e de resposta, lida e validada na subida.

É uma classe separada da `Settings` do template, para o template continuar sem nada do domínio.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.tipos import DIMENSAO_VETOR

Provedor = Literal["simulado", "local", "api"]


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # EMBEDDINGS_PROVIDER e LLM_PROVIDER escolhem a implementação de cada interface.
    embeddings_provider: Provedor = "simulado"
    llm_provider: Provedor = "simulado"

    # Modelo local de embeddings: multilíngue (~50 idiomas, inclui português), 384 dimensões,
    # ~220 MB, disponível no fastembed. Enxerga ~128 tokens por trecho. A escolha definitiva sai
    # da avaliação da etapa 5; o nome vai gravado em cada vetor.
    embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embeddings_dim: int = DIMENSAO_VETOR

    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: SecretStr | None = None

    # Tetos de custo: por pergunta (tokens) e por dia (dólares), aplicados nas etapas 3 e 5.
    max_tokens_por_pergunta: int = 2000
    limite_diario_gasto: float = 1.0

    @model_validator(mode="after")
    def validar(self) -> "LLMSettings":
        if self.embeddings_dim != DIMENSAO_VETOR:
            raise ValueError(
                f"EMBEDDINGS_DIM={self.embeddings_dim}, mas a coluna trecho.vetor tem "
                f"{DIMENSAO_VETOR} dimensões. Para usar outro modelo, crie uma migração que "
                "altere a coluna, atualize DIMENSAO_VETOR e reindexe os documentos."
            )
        usa_api = "api" in (self.embeddings_provider, self.llm_provider)
        if usa_api and not (self.llm_api_key and self.llm_api_key.get_secret_value()):
            raise ValueError("LLM_API_KEY é obrigatória quando algum provedor é 'api'")
        if self.max_tokens_por_pergunta < 1 or self.limite_diario_gasto < 0:
            raise ValueError("Os limites de tokens e de gasto precisam ser positivos")
        return self


@lru_cache
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
