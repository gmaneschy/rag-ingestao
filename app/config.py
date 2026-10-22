"""Configuração da aplicação, lida de variáveis de ambiente e validada na subida."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RAG com ingestão de documentos"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str
    api_key: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def normalizar_database_url(cls, valor: str) -> str:
        # Provedores como Neon e Supabase entregam "postgresql://"; o driver é o psycopg 3.
        if valor.startswith("postgres://"):
            valor = "postgresql://" + valor.removeprefix("postgres://")
        if valor.startswith("postgresql://"):
            valor = "postgresql+psycopg://" + valor.removeprefix("postgresql://")
        if not valor.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL deve ser uma URL de PostgreSQL")
        return valor

    @model_validator(mode="after")
    def exigir_chave_em_producao(self) -> "Settings":
        if self.app_env == "production":
            chave = self.api_key.get_secret_value() if self.api_key else ""
            if len(chave) < 16:
                raise ValueError("API_KEY com pelo menos 16 caracteres é obrigatória em produção")
        return self

    @property
    def em_producao(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
