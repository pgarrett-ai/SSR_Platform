"""Anthropic client for structured JSON extraction.

Uses tool-use with a forced tool call as the response schema (no regex for semantics, low
temperature). Returns the tool input as a plain dict. Designed to fail soft: if the key is
missing or the API errors, callers get None and the pipeline degrades gracefully.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .config import get_settings


class LLMUnavailable(Exception):
    pass


def _client():
    settings = get_settings()
    if not settings.llm_enabled:
        raise LLMUnavailable("ANTHROPIC_API_KEY not set")
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key), settings.anthropic_model


def extract_structured(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    max_tokens: int = 4096,
) -> Optional[dict[str, Any]]:
    """Force a single tool call and return its input dict, or None on failure.

    Note: newer models (e.g. claude-opus-4-8) deprecate the `temperature` parameter and run
    deterministically by default, so we don't send it.
    """
    try:
        client, model = _client()
    except LLMUnavailable:
        return None

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": input_schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:  # network / rate / model errors -> degrade gracefully
        return {"__error__": str(exc)}

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    # Fallback: some responses may put JSON in text
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except Exception:
                continue
    return None
