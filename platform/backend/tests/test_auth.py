"""PR-6 auth v1: shared bearer token on /api/* when PLATFORM_API_TOKEN is set;
open on localhost by default (unset). /api/health stays open for liveness."""
from fastapi.testclient import TestClient

from app.core import config
from app.main import app

client = TestClient(app).__enter__()


def _set_token(monkeypatch, value):
    # same pattern as test_settings_toggle.py: mutate the lru_cached Settings instance
    monkeypatch.setattr(config.get_settings(), "platform_api_token", value)


def test_open_when_token_unset(monkeypatch):
    _set_token(monkeypatch, "")
    assert client.get("/api/screen").status_code == 200


def test_401_without_token(monkeypatch):
    _set_token(monkeypatch, "s3cret")
    r = client.get("/api/screen")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["error"] == "unauthorized"


def test_200_with_bearer(monkeypatch):
    _set_token(monkeypatch, "s3cret")
    assert client.get("/api/screen",
                      headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_wrong_token_401(monkeypatch):
    _set_token(monkeypatch, "s3cret")
    assert client.get("/api/screen",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


def test_cookie_accepted_for_eventsource(monkeypatch):
    # EventSource can't send headers — the platform_token cookie is the equivalent bearer
    _set_token(monkeypatch, "s3cret")
    client.cookies.set("platform_token", "s3cret")
    try:
        assert client.get("/api/screen").status_code == 200
    finally:
        client.cookies.delete("platform_token")


def test_health_stays_open_and_reports_auth(monkeypatch):
    _set_token(monkeypatch, "s3cret")
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["auth_required"] is True
    _set_token(monkeypatch, "")
    assert client.get("/api/health").json()["auth_required"] is False
