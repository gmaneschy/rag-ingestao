"""Teste de integração: só roda com um Postgres de verdade (no CI, como service)."""

import os

import pytest
from sqlalchemy import create_engine

import app.db as db

URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="defina TEST_DATABASE_URL para rodar")


def test_check_database_com_banco_real(monkeypatch) -> None:
    monkeypatch.setattr(db, "get_engine", lambda: create_engine(URL))
    db.check_database()
