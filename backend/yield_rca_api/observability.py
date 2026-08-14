"""Per-application Prometheus metrics and structured logging helpers."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram
from yield_rca_core.models import RCAState


class JsonFormatter(logging.Formatter):
    """Serialize a bounded set of correlation fields as one JSON log record."""

    fields = (
        "correlation_id",
        "job_id",
        "task_id",
        "agent",
        "tool_request_id",
        "lot_id",
        "duration_ms",
        "outcome",
        "error_code",
        "error_type",
        "provider_code",
        "provider_message",
        "provider_request_id",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in self.fields:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging() -> None:
    root = logging.getLogger()
    if any(getattr(handler, "yield_rca_json", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.yield_rca_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


class RCAMetrics:
    """Metrics registry isolated per FastAPI app for deterministic tests."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.jobs_total = Counter(
            "rca_jobs_total",
            "RCA jobs by terminal outcome.",
            ("outcome", "agent_mode"),
            registry=self.registry,
        )
        self.job_duration = Histogram(
            "rca_job_duration_seconds",
            "End-to-end RCA job duration.",
            ("outcome", "agent_mode"),
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "tool_calls_total",
            "Tool calls by Tool name.",
            ("tool", "outcome"),
            registry=self.registry,
        )
        self.tool_duration = Histogram(
            "tool_duration_seconds",
            "Tool duration by Tool name.",
            ("tool",),
            registry=self.registry,
        )
        self.llm_calls = Counter(
            "llm_calls_total",
            "LLM calls by Agent, provider, model, and status.",
            ("agent", "provider", "model", "status"),
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "llm_tokens_total",
            "LLM tokens by provider, model, and token type.",
            ("provider", "model", "token_type"),
            registry=self.registry,
        )
        self.llm_duration = Histogram(
            "llm_duration_seconds",
            "LLM call duration by provider and model.",
            ("provider", "model"),
            registry=self.registry,
        )
        self.llm_errors = Counter(
            "llm_errors_total",
            "Failed LLM calls by provider and model.",
            ("provider", "model"),
            registry=self.registry,
        )
        self.inconclusive = Counter(
            "inconclusive_total",
            "Completed RCA jobs with an inconclusive result.",
            ("agent_mode",),
            registry=self.registry,
        )

    def observe_state(self, state: RCAState, *, outcome: str) -> None:
        metadata = state.execution_metadata
        mode = str(metadata.get("agent_mode", "deterministic"))
        duration_ms = float(metadata.get("workflow_duration_ms", 0.0))
        self.jobs_total.labels(outcome=outcome, agent_mode=mode).inc()
        self.job_duration.labels(outcome=outcome, agent_mode=mode).observe(duration_ms / 1000.0)
        for record in metadata.get("tool_latencies", []):
            if not isinstance(record, dict):
                continue
            tool = str(record.get("tool_name", "unknown"))
            tool_outcome = str(record.get("outcome", "success"))
            tool_duration = float(record.get("duration_ms", 0.0)) / 1000.0
            self.tool_calls.labels(tool=tool, outcome=tool_outcome).inc()
            self.tool_duration.labels(tool=tool).observe(tool_duration)
        for usage in state.llm_usage:
            self.llm_calls.labels(
                agent=usage.agent,
                provider=usage.provider,
                model=usage.model,
                status=usage.status,
            ).inc()
            self.llm_tokens.labels(
                provider=usage.provider,
                model=usage.model,
                token_type="prompt",
            ).inc(usage.prompt_tokens)
            self.llm_tokens.labels(
                provider=usage.provider,
                model=usage.model,
                token_type="completion",
            ).inc(usage.completion_tokens)
            self.llm_duration.labels(provider=usage.provider, model=usage.model).observe(
                usage.latency_ms / 1000.0
            )
            if usage.status == "failed":
                self.llm_errors.labels(provider=usage.provider, model=usage.model).inc()
        hypothesis = state.authoritative_hypothesis
        if hypothesis is not None and hypothesis.status == "inconclusive":
            self.inconclusive.labels(agent_mode=mode).inc()

    def observe_llm_error(self, *, provider: str, model: str) -> None:
        self.llm_errors.labels(provider=provider, model=model).inc()
