"""Anthropic client for structured JSON extraction.

The response schema is a tool definition; the system prompt tells the model to call it once
(current models reject a forced tool_choice). Returns the tool input as a plain dict.
Fails soft: no key -> None; API error, refusal or a truncated response -> {"__error__": ...},
which every caller turns into a pipeline warning.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .config import get_settings

# Adaptive thinking is always on and its tokens count against max_tokens: leave headroom so
# the tool call itself is never the part that gets cut off.
_MIN_MAX_TOKENS = 16000
_FALLBACK_BETA = "server-side-fallback-2026-06-01"
_FALLBACK_MODEL = "claude-opus-4-8"   # re-runs a request the primary model declines


class LLMUnavailable(Exception):
    pass


def _client():
    settings = get_settings()
    if not settings.llm_enabled:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    import anthropic

    # whole-document extractions can take minutes; 5 min x 3 attempts bounds a stuck call
    return (anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=300.0,
                                max_retries=2),
            settings.anthropic_model)


def extract_structured(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 4096,
    model: Optional[str] = None,
    effort: str = "medium",
) -> Optional[dict[str, Any]]:
    """Ask for exactly one `tool_name` call and return its input dict, or None when the LLM
    is not configured. `model` overrides the configured default for narrow tasks;
    `max_tokens` is the size of the expected output, raised to leave room for thinking."""
    try:
        client, default_model = _client()
    except LLMUnavailable:
        return None

    model = model or default_model
    kwargs: dict[str, Any] = {}
    if "haiku" not in model:   # Haiku has no server-side refusal fallback
        kwargs = {"betas": [_FALLBACK_BETA], "fallbacks": [{"model": _FALLBACK_MODEL}]}
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=max(max_tokens, _MIN_MAX_TOKENS),
            system=f"{system}\n\nRespond by calling the `{tool_name}` tool exactly once.",
            tools=[{"name": tool_name, "description": tool_description,
                    "input_schema": input_schema}],
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            output_config={"effort": effort},
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )
    except Exception as exc:  # network / rate / model errors -> degrade gracefully
        return {"__error__": str(exc)}

    if resp.stop_reason == "max_tokens":
        return {"__error__": "LLM response truncated at max_tokens; extraction incomplete"}
    if resp.stop_reason == "refusal":
        return {"__error__": "LLM declined the request"}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    for block in resp.content:   # a model that answered in text instead of the tool
        if getattr(block, "type", None) == "text":
            try:
                data = json.loads(block.text)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {"__error__": f"LLM did not call {tool_name}"}
