from fastapi.testclient import TestClient


def test_saude_ok(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.saude.check_database", lambda: None)
    resposta = client.get("/saude")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "banco": "ok"}


def test_saude_banco_fora(client: TestClient, monkeypatch) -> None:
    def falha() -> None:
        raise ConnectionError("sem banco")

    monkeypatch.setattr("app.routers.saude.check_database", falha)
    resposta = client.get("/saude")
    assert resposta.status_code == 503
    assert resposta.json() == {"status": "degradado", "banco": "falhou"}


def test_resposta_traz_id_de_requisicao(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.saude.check_database", lambda: None)
    assert client.get("/saude").headers["X-Request-ID"]
    assert client.get("/saude", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
