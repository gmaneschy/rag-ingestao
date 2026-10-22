import pytest
from pydantic import ValidationError

from app.config import Settings

URL = "postgresql+psycopg://u:s@localhost:5432/db"


@pytest.fixture(autouse=True)
def _sem_chave_no_ambiente(monkeypatch) -> None:
    # O conftest define API_KEY para os outros testes; aqui ela precisa começar ausente.
    monkeypatch.delenv("API_KEY", raising=False)


def make(**campos) -> Settings:
    return Settings(_env_file=None, **campos)


def test_producao_exige_chave_longa() -> None:
    with pytest.raises(ValidationError):
        make(database_url=URL, app_env="production", api_key="curta")
    with pytest.raises(ValidationError):
        make(database_url=URL, app_env="production")
    assert make(database_url=URL, app_env="production", api_key="x" * 16).em_producao


def test_desenvolvimento_aceita_sem_chave() -> None:
    assert make(database_url=URL).api_key is None


def test_database_url_e_normalizada() -> None:
    esperada = "postgresql+psycopg://u:s@h/db"
    assert make(database_url="postgresql://u:s@h/db").database_url == esperada
    assert make(database_url="postgres://u:s@h/db").database_url == esperada


def test_database_url_invalida() -> None:
    with pytest.raises(ValidationError):
        make(database_url="mysql://u:s@h/db")


def test_database_url_obrigatoria(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_chave_nao_aparece_no_repr() -> None:
    assert "x" * 16 not in repr(make(database_url=URL, api_key="x" * 16))
