"""LLM extraction request shape and failure surfacing (no network: the client is faked)."""
from types import SimpleNamespace

from app.core import llm


def _fake(monkeypatch, resp):
    sent = {}

    def create(**kw):
        sent.update(kw)
        return resp

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "_client", lambda: (client, "claude-opus-5-5"))
    return sent


def _call():
    return llm.extract_structured(system="s", user="u", tool_name="t", tool_description="d",
                                  input_schema={"type": "object"}, max_tokens=4000)


def test_tool_input_returned_without_forcing_the_tool(monkeypatch):
    block = SimpleNamespace(type="tool_use", name="t", input={"x": 1})
    sent = _fake(monkeypatch, SimpleNamespace(stop_reason="tool_use", content=[block]))
    assert _call() == {"x": 1}
    assert sent["tool_choice"]["type"] == "auto"          # forced tool_choice is a 400 now
    assert sent["max_tokens"] >= 16000                    # headroom for adaptive thinking


def test_truncated_or_declined_response_is_an_error_not_a_short_result(monkeypatch):
    _fake(monkeypatch, SimpleNamespace(stop_reason="max_tokens", content=[]))
    assert "truncated" in _call()["__error__"]
    _fake(monkeypatch, SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text="no")]))
    assert "did not call" in _call()["__error__"]
