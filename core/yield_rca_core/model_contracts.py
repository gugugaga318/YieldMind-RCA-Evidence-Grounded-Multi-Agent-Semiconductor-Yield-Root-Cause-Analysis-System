"""Minimal model boundary contracts; no RCA output or business validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from yield_rca_core.result_identity import ResultIdentity


class LLMConfigurationError(RuntimeError):
    """Raised when LLM mode is selected without valid provider configuration."""


def _sanitize_diagnostic_value(value: Any, *, limit: int) -> str | None:
    """Bound provider diagnostics and remove common credential representations."""

    if not isinstance(value, (str, int, float)):
        return None
    sanitized = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not sanitized:
        return None
    sanitized = re.sub(
        r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+",
        r"\1 [REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\b(api[_ -]?key|authorization)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        sanitized,
    )
    return sanitized[:limit]


class LLMCallError(RuntimeError):
    """Raised when the configured model request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        provider_message: str | None = None,
        request_id: str | None = None,
        failure_category: str = "llm_call_error",
        call_attempt_count: int = 1,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = _sanitize_diagnostic_value(provider_code, limit=100)
        self.provider_message = _sanitize_diagnostic_value(
            provider_message,
            limit=500,
        )
        self.request_id = _sanitize_diagnostic_value(request_id, limit=100)
        self.failure_category = (
            _sanitize_diagnostic_value(failure_category, limit=100) or "llm_call_error"
        )
        self.call_attempt_count = max(1, call_attempt_count)


class LLMOutputValidationError(ValueError):
    """Raised when a model response violates its structured output contract."""


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelCapabilities:
    message_roles: frozenset[str] = frozenset({"system", "user"})
    output_modes: frozenset[str] = frozenset({"json_object"})
    temperature_range: tuple[float, float] = (0.0, 2.0)
    context_window_tokens: int | None = None
    usage_available: bool | None = None


@dataclass(frozen=True)
class ModelRequest:
    provider: str
    model: str
    messages: tuple[ModelMessage, ...]
    temperature: float = 0.0
    output_mode: str = "json_object"
    logical_call_id: str = ""
    stage: str | None = None
    agent: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class ModelUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    # Legacy zero defaults are not evidence that missing/billable usage is zero.
    reported_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelOutput:
    content: str
    usage: ModelUsage
    stop_reason: str | None = None
    reported_model: str | None = None


@dataclass(frozen=True)
class ModelJSONResponse:
    data: dict[str, Any]
    usage: ModelUsage
    stop_reason: str | None
    reported_model: str | None
    identity: ResultIdentity | None = None


class ModelAdapter(Protocol):
    provider: str
    capabilities: ModelCapabilities

    def complete(self, request: ModelRequest) -> ModelOutput: ...
