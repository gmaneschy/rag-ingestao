"""Configuração da ingestão, lida de variáveis de ambiente e validada na subida."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ingestao import perfis


class IngestaoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Onde ficam os arquivos originais (necessários para reprocessar) e o cache dos modelos.
    dados_dir: Path = Path("dados")
    modelos_dir: Path = Path("modelos")

    # Ao subir, retoma os documentos que ficaram 'pendente' (servidor reiniciado no meio).
    retomar_pendentes: bool = True

    # Pasta do DjVuLibre (djvutxt e djvused) quando o app roda fora do Docker e os programas não
    # estão no PATH. No Windows, os caminhos de instalação padrão também são tentados.
    djvulibre_dir: str = ""

    tamanho_max_mb: int = 20
    max_paginas: int = 500

    # Tamanho do trecho em caracteres (~4 por token) e quanto um trecho repete do anterior.
    # Um perfil (curto, medio, longo) escolhe os dois de uma vez; "personalizado" usa os valores
    # abaixo. Ver app/ingestao/perfis.py e docs/configuracao-da-ingestao.md.
    perfil_trechos: str = perfis.PERSONALIZADO
    tamanho_trecho: int = 500
    sobreposicao_trecho: int = 80

    @model_validator(mode="before")
    @classmethod
    def aplicar_perfil(cls, dados: Any) -> Any:
        perfil = perfis.POR_CHAVE.get(str(dados.get("perfil_trechos", perfis.PERSONALIZADO)))
        if perfil is not None and perfil.tamanho and perfil.sobreposicao is not None:
            dados = {
                **dados,
                "tamanho_trecho": perfil.tamanho,
                "sobreposicao_trecho": perfil.sobreposicao,
            }
        return dados

    @model_validator(mode="after")
    def validar(self) -> "IngestaoSettings":
        if (
            self.perfil_trechos != perfis.PERSONALIZADO
            and self.perfil_trechos not in perfis.POR_CHAVE
        ):
            raise ValueError("PERFIL_TRECHOS deve ser curto, medio, longo ou personalizado")
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
