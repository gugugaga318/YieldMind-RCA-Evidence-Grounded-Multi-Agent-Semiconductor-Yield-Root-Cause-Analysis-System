"""Best-effort audit sinks for RCA lifecycle and LLM usage events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from yield_rca_core.models import LLMUsageEvent


@dataclass(frozen=True)
class AuditEvent:
    action: str
    job_id: str
    correlation_id: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    actor: str = "api"
    event_id: str = field(default_factory=lambda: f"AUDIT_{uuid4().hex.upper()}")
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AuditSink(Protocol):
    def record_event(self, event: AuditEvent) -> None: ...

    def record_llm_usage(
        self,
        *,
        job_id: str,
        correlation_id: str,
        usage: LLMUsageEvent,
    ) -> None: ...


class InMemoryAuditSink:
    """Thread-safe audit sink used by CSV mode and tests."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.llm_usage: list[tuple[str, str, LLMUsageEvent]] = []
        self._lock = RLock()

    def record_event(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(event)

    def record_llm_usage(
        self,
        *,
        job_id: str,
        correlation_id: str,
        usage: LLMUsageEvent,
    ) -> None:
        with self._lock:
            self.llm_usage.append((job_id, correlation_id, usage))


class PostgresAuditSink:
    """Append-only PostgreSQL audit sink backed by Step 16 tables."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def record_event(self, event: AuditEvent) -> None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit_event (
                        event_id, occurred_at, action, job_id, correlation_id,
                        actor, outcome, details
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        event.event_id,
                        event.occurred_at,
                        event.action,
                        event.job_id,
                        event.correlation_id,
                        event.actor,
                        event.outcome,
                        json.dumps(event.details),
                    ),
                )

    def record_llm_usage(
        self,
        *,
        job_id: str,
        correlation_id: str,
        usage: LLMUsageEvent,
    ) -> None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO llm_usage_event (
                        call_id, job_id, correlation_id, agent, provider, model,
                        prompt_version, prompt_tokens, completion_tokens, total_tokens,
                        cached_tokens, reasoning_tokens, latency_ms, status
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        usage.call_id,
                        job_id,
                        correlation_id,
                        usage.agent,
                        usage.provider,
                        usage.model,
                        usage.prompt_version,
                        usage.prompt_tokens,
                        usage.completion_tokens,
                        usage.total_tokens,
                        usage.cached_tokens,
                        usage.reasoning_tokens,
                        usage.latency_ms,
                        usage.status,
                    ),
                )
