"""DashScope wire encoding, envelope decoding and the original bounded HTTP retry."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx

from yield_rca_core.model_contracts import (
    LLMCallError,
    LLMOutputValidationError,
    ModelCapabilities,
    ModelOutput,
    ModelRequest,
    ModelUsage,
    _sanitize_diagnostic_value,
)
from yield_rca_core.model_telemetry import emit_model_event


def _sanitize_provider_value(value: Any, *, api_key: str, limit: int) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    sanitized = str(value)
    if api_key:
        sanitized = sanitized.replace(api_key, "[REDACTED]")
    return _sanitize_diagnostic_value(sanitized, limit=limit)


def _extract_provider_error(
    response: httpx.Response,
    *,
    api_key: str,
) -> tuple[str | None, str | None, str | None]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = None

    error_payload: dict[str, Any] = {}
    top_level: dict[str, Any] = payload if isinstance(payload, dict) else {}
    if isinstance(top_level.get("error"), dict):
        error_payload = top_level["error"]

    provider_code = _sanitize_provider_value(
        error_payload.get("code", top_level.get("code")),
        api_key=api_key,
        limit=100,
    )
    provider_message = _sanitize_provider_value(
        error_payload.get("message", top_level.get("message")),
        api_key=api_key,
        limit=500,
    )
    request_id = _sanitize_provider_value(
        top_level.get("request_id", top_level.get("requestId"))
        or response.headers.get("x-request-id"),
        api_key=api_key,
        limit=100,
    )
    return provider_code, provider_message, request_id


class QwenAdapter:
    provider = "dashscope"
    capabilities = ModelCapabilities()

    def __init__(
        self, *, base_url: str, api_key: str, timeout_seconds: float = 60.0, max_retries: int = 1
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def complete(self, request: ModelRequest) -> ModelOutput:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "response_format": {"type": request.output_mode},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        last_status_code: int | None = None
        last_provider_code: str | None = None
        last_provider_message: str | None = None
        last_request_id: str | None = None
        failure_category = "transport_error"
        call_attempt_count = 0
        http_attempt_count = 0
        for _ in range(self.max_retries + 1):
            call_attempt_count += 1
            issued = False
            attempt_started = perf_counter()
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    http_attempt_count += 1
                    issued = True
                    response = client.post(endpoint, headers=headers, json=body)
                    response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                last_status_code = exc.response.status_code
                failure_category = "provider_http_error"
                (last_provider_code, last_provider_message, last_request_id) = (
                    _extract_provider_error(exc.response, api_key=self.api_key)
                )
                if issued:
                    emit_model_event(
                        request,
                        "http_attempt",
                        http_attempt=http_attempt_count,
                        latency_ms=round((perf_counter() - attempt_started) * 1000, 3),
                        status_code=last_status_code,
                        outcome="http_error",
                        error_category=failure_category,
                    )
                if 400 <= last_status_code < 500 and last_status_code not in {408, 429}:
                    break
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                failure_category = "transport_error"
                if issued:
                    emit_model_event(
                        request,
                        "http_attempt",
                        http_attempt=http_attempt_count,
                        latency_ms=round((perf_counter() - attempt_started) * 1000, 3),
                        outcome="transport_error",
                        error_category=(
                            "timeout"
                            if isinstance(exc, httpx.TimeoutException)
                            else "transport_error"
                        ),
                    )
                continue
            emit_model_event(
                request,
                "http_attempt",
                http_attempt=http_attempt_count,
                latency_ms=round((perf_counter() - attempt_started) * 1000, 3),
                status_code=response.status_code,
                outcome="success",
            )
            try:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("response payload must be a JSON object")
                content = payload["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise TypeError("response content must be a string")
                usage_payload = dict(payload.get("usage", {}))
                prompt_tokens = int(usage_payload.get("prompt_tokens", 0))
                completion_tokens = int(usage_payload.get("completion_tokens", 0))
                details = dict(usage_payload.get("prompt_tokens_details") or {})
                completion_details = dict(usage_payload.get("completion_tokens_details") or {})
                usage = ModelUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=int(usage_payload.get("total_tokens", 0)),
                    cached_tokens=int(details.get("cached_tokens", 0)),
                    reasoning_tokens=int(completion_details.get("reasoning_tokens", 0)),
                    reported_fields=tuple(
                        k
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        if k in usage_payload
                    ),
                )
                choice = payload["choices"][0]
                stop = choice.get("finish_reason") if isinstance(choice, dict) else None
                reported = payload.get("model")
                return ModelOutput(
                    content,
                    usage,
                    _sanitize_provider_value(stop, api_key=self.api_key, limit=100)
                    if isinstance(stop, str)
                    else None,
                    _sanitize_provider_value(reported, api_key=self.api_key, limit=100)
                    if isinstance(reported, str)
                    else None,
                )
            except LLMOutputValidationError:
                raise
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise LLMOutputValidationError("model response envelope is invalid") from exc
        raise LLMCallError(
            "Qwen request failed after retries",
            status_code=last_status_code,
            provider_code=last_provider_code,
            provider_message=last_provider_message,
            request_id=last_request_id,
            failure_category=failure_category,
            call_attempt_count=call_attempt_count,
        ) from last_error
