import os
from pathlib import Path

import bcrypt
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "test-password-123"
_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", _HASH)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_BASE_URL", "https://study.example.test")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("NTFY_URL", "http://ntfy.invalid")
    from app import auth
    from app.config import reset_config

    reset_config()
    auth._fails.clear()
    from app.db import init_db

    init_db()
    yield
    reset_config()


@pytest.fixture
def conn():
    from app.db import connect

    c = connect()
    yield c
    c.close()


@pytest.fixture
def xlsx_bytes():
    return (FIXTURES / "AHK_Study_Tracker.xlsx").read_bytes()


@pytest.fixture
def ics_bytes():
    return (FIXTURES / "AHK_Diavasma.ics").read_bytes()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c


def csrf_from(html: str) -> str:
    import re

    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def logged_in(client):
    r = client.get("/login")
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": csrf_from(r.text), "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 303
    return client
