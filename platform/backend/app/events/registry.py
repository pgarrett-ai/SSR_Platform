"""Detector registry (plan §5): form prefix -> pure detector functions.

A detector: detect(filing_meta, raw_header, universe_row) -> list[Event]. No I/O.
A prefix covers the exact form, its '/A' amendments, and '-' variants ('15' covers
15-12B/15-12G/15-15D; '4' does NOT cover 424B5)."""
from __future__ import annotations

from typing import Callable, Optional

from .types import Event, FilingMeta

DetectorFn = Callable[[FilingMeta, dict, object], list[Event]]

_REGISTRY: dict[str, list[DetectorFn]] = {}


def register(*form_prefixes: str):
    def deco(fn: DetectorFn) -> DetectorFn:
        for p in form_prefixes:
            _REGISTRY.setdefault(p, []).append(fn)
        return fn
    return deco


def form_matches(form: str, prefix: str) -> bool:
    return form == prefix or form.startswith(prefix + "/") or form.startswith(prefix + "-")


def detectors_for(form: str) -> list[DetectorFn]:
    return [fn for p in sorted(_REGISTRY) if form_matches(form, p) for fn in _REGISTRY[p]]


def tracked_prefixes() -> tuple[str, ...]:
    """Single source of truth for which forms the feed/backfill extract."""
    return tuple(sorted(_REGISTRY))
