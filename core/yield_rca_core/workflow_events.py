"""Public, bounded Workflow progress events for asynchronous RCA clients."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

WorkflowEventSink = Callable[[str, dict[str, Any]], None]

_ACTIVE_EVENT_SINK: ContextVar[WorkflowEventSink | None] = ContextVar(
    "yield_rca_workflow_event_sink",
    default=None,
)


@contextmanager
def capture_workflow_events(sink: WorkflowEventSink) -> Iterator[None]:
    """Route public progress deltas for one bounded Workflow attempt."""

    token = _ACTIVE_EVENT_SINK.set(sink)
    try:
        yield
    finally:
        _ACTIVE_EVENT_SINK.reset(token)


def emit_workflow_event(event_type: str, payload: dict[str, Any]) -> None:
    """Emit an explicitly projected event; never serialize internal objects here."""

    sink = _ACTIVE_EVENT_SINK.get()
    if sink is not None:
        sink(event_type, dict(payload))
