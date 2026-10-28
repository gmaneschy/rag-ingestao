"""Configuração da ingestão, lida de variáveis de ambiente e validada na subida."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestaoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Onde ficam os arquivos originais (necessários para reprocessar) e o cache dos modelos.
    dados_dir: Path = Path("dados")
    modelos_dir: Path = Path("modelos")

    # Ao subir, retoma os documentos que ficaram 'pendente' (servidor reiniciado no meio).
    retomar_pendentes: bool = True

    tamanho_max_mb: int = 20
    max_paginas: int = 500

    # O modelo de embeddings padrão enxerga só ~128 tokens (cerca de 500 caracteres): trechos
    # maiores são truncados no vetor. O tamanho ideal sai dos experimentos da etapa 5.
    tamanho_trecho: int = 500
    sobreposicao_trecho: int = 80

    @model_validator(mode="after")
    def validar(self) -> "IngestaoSettings":
        if self.tamanho_max_mb < 1 or self.max_paginas < 1:
            raise ValueError("TAMANHO_MAX_MB e MAX_PAGINAS precisam ser positivos")
        if self.tamanho_trecho < 100:
            raise ValueError("TAMANHO_TRECHO deve ser de pelo menos 100 caracteres")
        if not 0 <= self.sobreposicao_trecho < self.tamanho_trecho // 2:
            raise ValueError("SOBREPOSICAO_TRECHO deve estar entre 0 e metade do tamanho do trecho")
        return self

    @property
    def tamanho_max_bytes(self) -> int:
        return self.tamanho_max_mb * 1024 * 1024

    @property
    def arquivos_dir(self) -> Path:
        return self.dados_dir / "arquivos"


@lru_cache
def get_ingestao_settings() -> IngestaoSettings:
    return IngestaoSettings()
