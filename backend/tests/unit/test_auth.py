import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings

TOKEN = "unit-test-token-123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", TOKEN)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_health_is_open(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "db" in resp.json() and "redis" in resp.json()


def test_api_requires_token(client):
    assert client.get("/api/v1/ping").status_code == 401


def test_api_rejects_wrong_token(client):
    resp = client.get("/api/v1/ping", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_api_accepts_correct_token(client):
    resp = client.get("/api/v1/ping", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}


def test_boot_refuses_empty_token(monkeypatch):
    monkeypatch.setenv("APP_AUTH_TOKEN", "")
    get_settings.cache_clear()
    from app.main import create_app

    with pytest.raises(RuntimeError, match="APP_AUTH_TOKEN"):
        create_app()
    get_settings.cache_clear()
