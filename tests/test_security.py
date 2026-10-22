from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.security import exigir_chave


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/privado", dependencies=[Depends(exigir_chave)])
    def privado() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_sem_chave_negado() -> None:
    assert _app().get("/privado").status_code == 401


def test_chave_errada_negada() -> None:
    assert _app().get("/privado", headers={"X-API-Key": "errada"}).status_code == 401


def test_chave_correta_aceita() -> None:
    resposta = _app().get(
        "/privado", headers={"X-API-Key": "chave-de-teste-com-mais-de-16-caracteres"}
    )
    assert resposta.status_code == 200
