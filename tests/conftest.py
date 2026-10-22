import os

# Precisa vir antes de qualquer import de app.*: a configuração é lida e validada na importação.
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["DATABASE_URL"] = "postgresql+psycopg://teste:teste@localhost:5432/teste"
os.environ["API_KEY"] = "chave-de-teste-com-mais-de-16-caracteres"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c
