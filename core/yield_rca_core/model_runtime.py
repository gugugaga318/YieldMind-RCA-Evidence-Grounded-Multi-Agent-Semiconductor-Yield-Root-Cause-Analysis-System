"""Provider-neutral JSON execution. Exactly one adapter call; no runtime retry."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from time import perf_counter
from typing import Any
from uuid import uuid4

from yield_rca_core.model_contracts import (
    LLMCallError,
    LLMConfigurationError,
    LLMOutputValidationError,
    ModelAdapter,
    ModelJSONResponse,
    ModelRequest,
    ModelUsage,
)
from yield_rca_core.model_telemetry import emit_model_event
from yield_rca_core.result_identity import ResultIdentity


def parse_json_content(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise LLMOutputValidationError("model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise LLMOutputValidationError("model response must be a JSON object")
    return value


class ModelRuntime:
    def __init__(self, adapter: ModelAdapter) -> None:
        self.adapter = adapter

    def complete_json(
        self,
        request: ModelRequest,
        *,
        on_usage: Callable[[ModelUsage | None], None] | None = None,
    ) -> ModelJSONResponse:
        # Capability errors are startup/request configuration errors, never retries.
        if request.provider != self.adapter.provider:
            raise LLMConfigurationError("requested provider does not match configured adapter")
        capabilities = self.adapter.capabilities
        if (
            request.output_mode != "json_object"
            or request.output_mode not in capabilities.output_modes
        ):
            raise LLMConfigurationError("adapter does not support JSON object output")
        if any(message.role not in capabilities.message_roles for message in request.messages):
            raise LLMConfigurationError("adapter does not support requested message role")
        if not request.logical_call_id:
            request = replace(request, logical_call_id=f"MODEL_{uuid4().hex.upper()}")
        started = perf_counter()

        def emit_result(**facts: object) -> None:
            emit_model_event(
                request, "model_result",
                latency_ms=round((perf_counter() - started) * 1000, 3), **facts,
            )

        emit_model_event(request, "logical_call")
        try:
            output = self.adapter.complete(request)
        except (LLMCallError, LLMOutputValidationError) as exc:
            if on_usage is not None:
                on_usage(None)
            emit_result(
                outcome="failed",
                error_category=getattr(exc, "failure_category", "invalid_response"),
            )
            raise
        try:
            usage = replace(
                output.usage,
                total_tokens=max(
                    output.usage.total_tokens,
                    output.usage.prompt_tokens + output.usage.completion_tokens,
                ),
            )
            # Compatibility callback records/validates legacy usage BEFORE JSON decode.
            # This callback is part of the old contract, not optional telemetry.
            if on_usage is not None:
                on_usage(usage)
        except LLMOutputValidationError:
            emit_result(outcome="failed", error_category="invalid_response")
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if on_usage is not None:
                on_usage(None)
            emit_result(outcome="failed", error_category="invalid_response")
            raise LLMOutputValidationError("model response envelope is invalid") from exc
        try:
            data = parse_json_content(output.content)
        except LLMOutputValidationError:
            emit_result(
                outcome="invalid_json",
                error_category="invalid_json",
                usage=usage,
                stop_reason=output.stop_reason,
                reported_model=output.reported_model,
            )
            raise
        emit_result(
            outcome="success",
            usage=usage,
            stop_reason=output.stop_reason,
            reported_model=output.reported_model,
        )
        return ModelJSONResponse(
            data, usage, output.stop_reason, output.reported_model,
            ResultIdentity("model", self.adapter.provider, request.model, "runtime_config"),
        )
