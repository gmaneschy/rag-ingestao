"""Parâmetros da busca, lidos de variáveis de ambiente."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConsultaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    consulta_k: int = 5  # quantos trechos vão ao modelo
    consulta_candidatos: int = 20  # candidatos de cada busca antes de fundir
    consulta_rrf_k: int = 60  # constante da fusão por posição (RRF); 60 é o valor da literatura
    # Similaridade de cosseno mínima do melhor trecho para haver "evidência" quando nenhuma palavra
    # da pergunta aparece nos trechos. AINDA NÃO CALIBRADO com o modelo real: a etapa 5 mede.
    limiar_similaridade: float = 0.25

    @model_validator(mode="after")
    def validar(self) -> "ConsultaSettings":
        if not 1 <= self.consulta_k <= 10:
            raise ValueError("CONSULTA_K deve estar entre 1 e 10")
        if self.consulta_candidatos < self.consulta_k or self.consulta_rrf_k < 1:
            raise ValueError("CONSULTA_CANDIDATOS deve ser >= CONSULTA_K e CONSULTA_RRF_K >= 1")
        if not 0 <= self.limiar_similaridade <= 1:
            raise ValueError("LIMIAR_SIMILARIDADE deve estar entre 0 e 1")
        return self


@lru_cache
def get_consulta_settings() -> ConsultaSettings:
    return ConsultaSettings()
