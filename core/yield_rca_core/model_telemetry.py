"""Opt-in in-memory model facts. No State, persistence, payload or credential capture."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from yield_rca_core.model_contracts import ModelRequest, ModelUsage


@dataclass(frozen=True)
class ModelEvent:
    logical_call_id: str
    provider: str
    model: str
    kind: str
    outcome: str | None = None
    http_attempt: int | None = None
    status_code: int | None = None
    error_category: str | None = None
    usage: ModelUsage | None = None
    stop_reason: str | None = None
    reported_model: str | None = None
    stage: str | None = None
    agent: str | None = None
    prompt_version: str | None = None
    latency_ms: float | None = None


@dataclass
class ModelCapture:
    max_events: int
    events: list[ModelEvent] = field(default_factory=list)
    dropped_count: int = 0

    @property
    def complete(self) -> bool:
        return self.dropped_count == 0


@dataclass(frozen=True)
class _Sink:
    capture: ModelCapture
    parent: _Sink | None


_SINK: ContextVar[_Sink | None] = ContextVar("yield_rca_model_facts", default=None)


@contextmanager
def capture_model_telemetry(*, max_events: int = 1000) -> Iterator[ModelCapture]:
    if max_events < 0:
        raise ValueError("max_events must be non-negative")
    capture = ModelCapture(max_events)
    token = _SINK.set(_Sink(capture, _SINK.get()))
    try:
        yield capture
    finally:
        _SINK.reset(token)


def emit_model_event(request: ModelRequest, kind: str, **facts: object) -> None:
    sink = _SINK.get()
    if sink is None:
        return
    try:
        event = ModelEvent(
            request.logical_call_id, request.provider, request.model, kind,
            stage=request.stage, agent=request.agent, prompt_version=request.prompt_version,
            **facts,
        )
    except Exception:
        while sink is not None:
            sink.capture.dropped_count += 1
            sink = sink.parent
        return
    # Emit once per active scope; no second merge at exit. Failures never affect calls.
    while sink is not None:
        try:
            if len(sink.capture.events) < sink.capture.max_events:
                sink.capture.events.append(event)
            else:
                sink.capture.dropped_count += 1
        except Exception:
            sink.capture.dropped_count += 1
        sink = sink.parent
