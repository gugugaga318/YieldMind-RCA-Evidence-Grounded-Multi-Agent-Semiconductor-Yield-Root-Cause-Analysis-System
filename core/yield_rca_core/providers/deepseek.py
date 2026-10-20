"""DeepSeek non-thinking JSON transport; no SDK or implicit transport retries."""

from __future__ import annotations

import math
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from yield_rca_core.model_contracts import (
    LLMCallError,
    LLMConfigurationError,
    LLMOutputValidationError,
    ModelCapabilities,
    ModelOutput,
    ModelRequest,
    ModelUsage,
    _sanitize_diagnostic_value,
)
from yield_rca_core.model_telemetry import emit_model_event


class DeepSeekAdapter:
    provider = "deepseek"
    capabilities = ModelCapabilities(context_window_tokens=1_000_000, usage_available=True)
    supported_models = frozenset({"deepseek-flash", "deepseek-v4-pro"})
    max_output_tokens = 8192

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or url.hostname != "api.deepseek.com"
            or url.port not in {None, 443}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path.rstrip("/") not in {"", "/v1"}
        ):
            raise LLMConfigurationError("DeepSeek requires its official HTTPS endpoint")
        if not api_key.strip():
            raise LLMConfigurationError("DEEPSEEK_API_KEY is required")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or max_retries < 0:
            raise LLMConfigurationError("invalid DeepSeek timeout/retry configuration")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def validate_workflow(self, model: str, *, min_context_tokens: int = 32768) -> None:
        caps = self.capabilities
        if model not in self.supported_models:
            raise LLMConfigurationError("DeepSeek model is not qualified for this workflow")
        if (
            "json_object" not in caps.output_modes
            or not {"system", "user"}.issubset(caps.message_roles)
            or caps.temperature_range[0] > 0
            or caps.temperature_range[1] < 2
            or caps.usage_available is not True
        ):
            raise LLMConfigurationError("DeepSeek lacks required workflow capabilities")
        if (
            min_context_tokens < 1
            or caps.context_window_tokens is None
            or caps.context_window_tokens < min_context_tokens + self.max_output_tokens
        ):
            raise LLMConfigurationError("DeepSeek context capacity is insufficient")

    def _safe(self, value: object, limit: int = 100) -> str | None:
        if not isinstance(value, (str, int, float)):
            return None
        return _sanitize_diagnostic_value(
            str(value).replace(self.api_key, "[REDACTED]"), limit=limit
        )

    def _http_error(self, response: httpx.Response) -> tuple[str, str | None, str | None]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        code = self._safe(error.get("code"))
        message = self._safe(error.get("message"), 500)
        status = response.status_code
        if status in {401, 403}:
            category = "authentication"
        elif status == 402:
            category = "billing"
        elif status == 429:
            category = "rate_limit"
        elif status >= 500:
            category = "unavailable"
        elif status in {400, 413, 422} and (
            status == 413
            or "context" in (code or "").lower()
            or "context length" in (message or "").lower()
            or "maximum context" in (message or "").lower()
        ):
            category = "context_overflow"
        else:
            category = "provider_http_error"
        return category, code, message

    def _decode(self, response: httpx.Response) -> ModelOutput:
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
            stop = choice["finish_reason"]
            if not isinstance(content, str) or message.get("tool_calls"):
                raise ValueError("unsupported content")
            if stop not in {
                "stop",
                "length",
                "content_filter",
                "insufficient_system_resource",
                "aborted",
            }:
                raise ValueError("unsupported finish reason")
            raw = payload["usage"]

            def count(name: str, container: dict = raw, default: int | None = None) -> int:
                value = container.get(name, default)
                if type(value) is not int or value < 0:
                    raise ValueError("invalid token usage")
                return value

            prompt = count("prompt_tokens")
            completion = count("completion_tokens")
            total = count("total_tokens")
            details = raw.get("prompt_tokens_details") or {}
            cached = count("prompt_cache_hit_tokens", default=0)
            if "cached_tokens" in details:
                cached = count("cached_tokens", details)
            reasoning = count("reasoning_tokens", raw.get("completion_tokens_details") or {}, 0)
            if cached > prompt or reasoning > completion:
                raise ValueError("invalid token breakdown")
            return ModelOutput(
                content,
                ModelUsage(
                    prompt,
                    completion,
                    total,
                    cached,
                    reasoning,
                    ("prompt_tokens", "completion_tokens", "total_tokens"),
                ),
                self._safe(stop),
                self._safe(payload.get("model")),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise LLMOutputValidationError("model response envelope is invalid") from None

    def complete(self, request: ModelRequest) -> ModelOutput:
        self.validate_workflow(request.model)
        if (
            request.provider != self.provider
            or request.output_mode != "json_object"
            or not request.messages
            or any(m.role not in self.capabilities.message_roles for m in request.messages)
            or not math.isfinite(request.temperature)
            or not 0 <= request.temperature <= 2
        ):
            raise LLMConfigurationError("unsupported DeepSeek request capability")
        if not any("json" in m.content.lower() for m in request.messages):
            raise LLMConfigurationError("DeepSeek JSON mode requires a JSON instruction")
        # Conservative byte upper estimate, no truncation or business-payload rewriting.
        size = sum(len(m.content.encode("utf-8")) + 16 for m in request.messages)
        if size + self.max_output_tokens > self.capabilities.context_window_tokens:
            error = LLMCallError(
                "request exceeds conservative context budget",
                status_code=400,
                failure_category="context_overflow",
            )
            error.call_attempt_count = 0  # Local preflight issued no HTTP request.
            raise error
        body = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": self.max_output_tokens,
        }
        status = code = message = request_id = None
        category = "transport_error"
        for attempt in range(1, self.max_retries + 2):
            started = perf_counter()
            try:
                # httpx defaults: transport retries=0 and redirects disabled. No SDK.
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        json=body,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                    )
            except httpx.HTTPError as exc:
                status = code = message = request_id = None
                category = "transport_error"
                emit_model_event(
                    request,
                    "http_attempt",
                    http_attempt=attempt,
                    outcome="transport_error",
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    error_category="timeout"
                    if isinstance(exc, httpx.TimeoutException)
                    else category,
                )
                continue
            if not 200 <= response.status_code < 300:
                status = response.status_code
                category, code, message = self._http_error(response)
                request_id = self._safe(response.headers.get("x-request-id"))
                emit_model_event(
                    request,
                    "http_attempt",
                    http_attempt=attempt,
                    outcome="http_error",
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    status_code=status,
                    error_category=category,
                )
                if status not in {408, 429} and status < 500:
                    break
                continue
            emit_model_event(
                request,
                "http_attempt",
                http_attempt=attempt,
                outcome="success",
                latency_ms=round((perf_counter() - started) * 1000, 3),
                status_code=response.status_code,
            )
            return self._decode(response)
        # Do not retain raw HTTP exceptions/requests containing credential headers.
        raise LLMCallError(
            "DeepSeek request failed after retries",
            status_code=status,
            provider_code=code,
            provider_message=message,
            request_id=request_id,
            failure_category=category,
            call_attempt_count=attempt,
        )
