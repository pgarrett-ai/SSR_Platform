"""A tiny progress-log abstraction. The pipeline emits human-readable steps; the API can
stream them to the UI (SSE) or collect them for a synchronous response."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class ProgressEvent:
    message: str
    step: Optional[str] = None
    pct: Optional[int] = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {"message": self.message, "step": self.step, "pct": self.pct, "ts": self.ts}


class ProgressLog:
    def __init__(self, sink: Optional[Callable[[ProgressEvent], None]] = None) -> None:
        self.events: list[ProgressEvent] = []
        self._sink = sink

    def emit(self, message: str, step: Optional[str] = None, pct: Optional[int] = None) -> None:
        evt = ProgressEvent(message=message, step=step, pct=pct)
        self.events.append(evt)
        if self._sink is not None:
            self._sink(evt)
