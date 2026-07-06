"""LLM runtime toggle: POST /api/settings/llm flips health.llm_enabled without a restart."""
from fastapi.testclient import TestClient

from app.core import config
from app.main import app

client = TestClient(app).__enter__()


def test_llm_toggle_flips_health(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(config.get_settings(), "anthropic_api_key", "test-key")

    assert client.get("/api/health").json()["llm_enabled"] is True   # default: on
    assert client.post("/api/settings/llm",
                       json={"enabled": False}).json()["llm_enabled"] is False
    h = client.get("/api/health").json()
    assert h["llm_enabled"] is False and h["llm_key_set"] is True    # off ≠ keyless
    assert client.post("/api/settings/llm",
                       json={"enabled": True}).json()["llm_enabled"] is True


def test_llm_toggle_cannot_enable_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(config.get_settings(), "anthropic_api_key", "")
    r = client.post("/api/settings/llm", json={"enabled": True}).json()
    assert r["llm_enabled"] is False and r["llm_key_set"] is False
