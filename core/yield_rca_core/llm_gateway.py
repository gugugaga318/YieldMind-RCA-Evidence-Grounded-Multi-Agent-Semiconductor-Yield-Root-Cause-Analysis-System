"""Centralized Qwen-compatible LLM Gateway with structured JSON contracts."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib.resources import files
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

import httpx as httpx  # Compatibility patch target; adapter shares this module.

from yield_rca_core.model_contracts import (
    LLMCallError as LLMCallError,
)
from yield_rca_core.model_contracts import (
    LLMConfigurationError as LLMConfigurationError,
)
from yield_rca_core.model_contracts import (
    LLMOutputValidationError as LLMOutputValidationError,
)
from yield_rca_core.model_contracts import (
    ModelMessage,
    ModelRequest,
    ModelUsage,
)
from yield_rca_core.model_runtime import ModelRuntime
from yield_rca_core.model_runtime import (
    parse_json_content as _parse_json_content,  # noqa: F401 - legacy helper import compatibility
)
from yield_rca_core.models import (
    AgentKind,
    AgentMode,
    LLMUsageEvent,
    ModelValidationError,
)
from yield_rca_core.providers.deepseek import DeepSeekAdapter
from yield_rca_core.providers.qwen import QwenAdapter


@dataclass(frozen=True)
class LLMSettings:
    agent_mode: str = AgentMode.DETERMINISTIC.value
    provider: str = "dashscope"
    model: str = "qwen-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 60.0
    max_retries: int = 1
    min_context_tokens: int = 32768

    def __post_init__(self) -> None:
        try:
            AgentMode(self.agent_mode)
        except ValueError as exc:
            raise LLMConfigurationError(
                "YIELD_RCA_AGENT_MODE must be deterministic, fake, or llm"
            ) from exc
        if not self.model.strip():
            raise LLMConfigurationError("LLM model must not be empty")
        if self.agent_mode == AgentMode.LLM.value and not self.api_key.strip():
            key_name = "DEEPSEEK_API_KEY" if self.provider == "deepseek" else "DASHSCOPE_API_KEY"
            raise LLMConfigurationError(f"{key_name} is required when agent mode is llm")
        if self.timeout_seconds <= 0:
            raise LLMConfigurationError("LLM timeout must be positive")
        if self.max_retries < 0:
            raise LLMConfigurationError("LLM max retries must be non-negative")

    @classmethod
    def from_env(cls) -> LLMSettings:
        provider = os.getenv("YIELD_RCA_LLM_PROVIDER", "dashscope").strip()
        deepseek = provider == "deepseek"
        return cls(
            agent_mode=os.getenv(
                "YIELD_RCA_AGENT_MODE", AgentMode.DETERMINISTIC.value
            ).strip(),
            provider=provider,
            model=os.getenv(
                "YIELD_RCA_LLM_MODEL", "deepseek-flash" if deepseek else "qwen-plus"
            ).strip(),
            base_url=os.getenv(
                "YIELD_RCA_LLM_BASE_URL",
                "https://api.deepseek.com" if deepseek
                else "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).strip(),
            api_key=os.getenv("DEEPSEEK_API_KEY" if deepseek else "DASHSCOPE_API_KEY", "").strip(),
            timeout_seconds=float(os.getenv("YIELD_RCA_LLM_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("YIELD_RCA_LLM_MAX_RETRIES", "1")),
            min_context_tokens=int(os.getenv("YIELD_RCA_LLM_MIN_CONTEXT_TOKENS", "32768")),
        )


@dataclass(frozen=True)
class LLMRequest:
    agent: str
    prompt_name: str
    prompt_version: str
    payload: dict[str, Any]
    temperature: float = 0.0

    def __post_init__(self) -> None:
        try:
            AgentKind(self.agent)
        except ValueError as exc:
            raise ModelValidationError(f"invalid LLM request agent: {self.agent}") from exc
        if not self.prompt_name or not self.prompt_version:
            raise ModelValidationError("LLM prompt name and version are required")
        if not 0.0 <= self.temperature <= 2.0:
            raise ModelValidationError("LLM temperature must be between 0 and 2")


@dataclass(frozen=True)
class LLMResponse:
    data: dict[str, Any]
    usage: LLMUsageEvent

    @property
    def identity(self):
        from yield_rca_core.result_identity import ResultIdentity

        return ResultIdentity("model", self.usage.provider, self.usage.model, "runtime_config")


class LLMClient(Protocol):
    provider: str
    model: str

    def complete_json(self, request: LLMRequest) -> LLMResponse: ...


def llm_calls_remaining(client: LLMClient) -> int | None:
    """Return a wrapper-advertised call budget without extending LLMClient.

    Production/provider clients are intentionally not required to expose a
    budget.  Evaluation wrappers may expose either ``remaining_calls`` or the
    existing ``max_calls``/``call_count`` pair.  Unknown budgets return ``None``
    and preserve the normal provider behavior.
    """

    raw_remaining = getattr(client, "remaining_calls", None)
    if isinstance(raw_remaining, int) and not isinstance(raw_remaining, bool):
        return max(0, raw_remaining)
    raw_max = getattr(client, "max_calls", None)
    raw_count = getattr(client, "call_count", None)
    if (
        isinstance(raw_max, int)
        and not isinstance(raw_max, bool)
        and isinstance(raw_count, int)
        and not isinstance(raw_count, bool)
    ):
        return max(0, raw_max - raw_count)
    return None


def llm_call_budget_available(
    client: LLMClient,
    *,
    required_calls: int = 1,
    reserve_calls: int = 0,
) -> bool:
    """Check an optional bounded-client budget without consuming a call."""

    if required_calls < 0 or reserve_calls < 0:
        raise ValueError("required_calls and reserve_calls must be non-negative")
    remaining = llm_calls_remaining(client)
    return remaining is None or remaining >= required_calls + reserve_calls


_USAGE_SINK: ContextVar[list[LLMUsageEvent] | None] = ContextVar(
    "yield_rca_llm_usage_sink",
    default=None,
)


@contextmanager
def capture_llm_usage() -> Iterator[list[LLMUsageEvent]]:
    events: list[LLMUsageEvent] = []
    parent = _USAGE_SINK.get()
    token = _USAGE_SINK.set(events)
    try:
        yield events
    finally:
        _USAGE_SINK.reset(token)
        if parent is not None:
            parent.extend(events)


def _record_usage(event: LLMUsageEvent) -> None:
    sink = _USAGE_SINK.get()
    if sink is not None:
        sink.append(event)


def load_prompt(prompt_name: str, prompt_version: str) -> str:
    resource_name = f"{prompt_name}_{prompt_version}.md"
    resource = files("yield_rca_core.prompts").joinpath(resource_name)
    if not resource.is_file():
        raise LLMConfigurationError(f"prompt not found: {resource_name}")
    return resource.read_text(encoding="utf-8")


class RuntimeLLMClient:
    """Provider-neutral caller facade with identity bound by injected runtime/config."""

    def __init__(self, runtime: ModelRuntime, *, model: str) -> None:
        if not model.strip():
            raise LLMConfigurationError("LLM model must not be empty")
        self.runtime = runtime
        self.provider = runtime.adapter.provider
        self.model = model

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        call_id = f"LLM_{uuid4().hex.upper()}"
        prompt = load_prompt(request.prompt_name, request.prompt_version)
        model_request = ModelRequest(
            provider=self.provider,
            model=self.model,
            messages=(
                ModelMessage("system", prompt),
                ModelMessage("user", json.dumps(
                    request.payload, ensure_ascii=False, separators=(",", ":"),
                )),
            ),
            temperature=request.temperature,
            logical_call_id=call_id,
            stage=request.prompt_name,
            agent=request.agent,
            prompt_version=request.prompt_version,
        )
        usage: LLMUsageEvent | None = None

        def record_usage(value: ModelUsage | None) -> None:
            nonlocal usage
            counts = value or ModelUsage()
            usage = LLMUsageEvent(
                call_id=call_id,
                agent=request.agent,
                provider=self.provider,
                model=self.model,
                prompt_version=request.prompt_version,
                prompt_tokens=counts.prompt_tokens,
                completion_tokens=counts.completion_tokens,
                total_tokens=counts.total_tokens,
                cached_tokens=counts.cached_tokens,
                reasoning_tokens=counts.reasoning_tokens,
                latency_ms=round((perf_counter() - started) * 1000.0, 3),
                status="success" if value is not None else "failed",
            )
            _record_usage(usage)

        result = self.runtime.complete_json(model_request, on_usage=record_usage)
        assert usage is not None
        return LLMResponse(data=result.data, usage=usage)


class DashScopeLLMClient(RuntimeLLMClient):
    """Backward-compatible Qwen factory; HTTP retries remain in QwenAdapter."""

    provider = "dashscope"

    def __init__(self, settings: LLMSettings) -> None:
        if settings.agent_mode != AgentMode.LLM.value:
            raise LLMConfigurationError("DashScope client requires llm agent mode")
        self.settings = settings
        super().__init__(
            ModelRuntime(QwenAdapter(
                base_url=settings.base_url,
                api_key=settings.api_key,
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            )),
            model=settings.model,
        )


class FakeLLMClient:
    """Deterministic no-cost client used to test the complete LLM path."""

    provider = "fake"
    model = "fake-qwen-plus"

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        if request.prompt_name == "planner":
            data = dict(request.payload["fallback_plan"])
        elif request.prompt_name == "intent_planner":
            data = dict(request.payload["deterministic_intent_plan"])
        elif request.prompt_name == "next_action_planner":
            data = dict(request.payload["deterministic_planner_decision"])
        elif request.prompt_name == "specialist_tool_planner":
            data = dict(request.payload["deterministic_specialist_decision"])
        elif request.prompt_name == "specialist_analysis":
            data = dict(request.payload["deterministic_specialist_analysis"])
        elif request.prompt_name == "specialist":
            finding = dict(request.payload["deterministic_finding"])
            data = {
                "summary": finding["summary"],
                "confidence": finding["confidence"],
                "evidence_ids": list(finding["evidence_ids"]),
                "engineering_interpretation": finding["summary"],
            }
        elif request.prompt_name == "hypothesis_candidate_generator":
            data = {
                "candidates": list(
                    request.payload["deterministic_candidate_proposals"]
                ),
                "analysis_summary": (
                    "The fake client preserves deterministic hypothesis behavior."
                ),
            }
        elif request.prompt_name == "causal_candidate_comparator":
            comparison = dict(request.payload.get("python_comparison", {}))
            data = {
                "preferred_candidate_index": comparison.get("preferred_candidate_index"),
                "comparison_explanation": comparison.get(
                    "comparison_explanation",
                    "Python comparison did not identify a unique candidate.",
                ),
                "selected_gap_id": None,
            }
        elif request.prompt_name == "causal_adversarial_challenge":
            data = {
                "challenges": [
                    {
                        "candidate_id": str(candidate.get("candidate_id", "")),
                        "strongest_alternative_lane_id": None,
                        "supporting_evidence_ids": [],
                        "contradicting_evidence_ids": [],
                        "unexplained_precursor_evidence_ids": [],
                        "distinguishing_gap_ids": [],
                        "challenge_explanation": (
                            "The fake client leaves competition unresolved so the "
                            "strict Confirmation Gate cannot over-confirm."
                        ),
                        "status": "unresolved",
                    }
                    for candidate in request.payload.get("candidates", [])
                    if isinstance(candidate, dict)
                ],
                "analysis_summary": "The fake client preserves the adversarial boundary.",
            }
        elif request.prompt_name == "rca_reasoning":
            data = {
                "ranked_candidates": list(request.payload["candidate_catalog"]),
                "analysis_summary": request.payload["deterministic_rationale"],
            }
        elif request.prompt_name == "improvement":
            data = {
                "engineering_summary": (
                    f"{request.payload['incident_summary']} "
                    f"{request.payload['fab_level_summary']}"
                ),
                "recommendation_ids": list(request.payload["recommendation_ids"]),
                "evidence_ids": list(request.payload["evidence_ids"]),
            }
        else:
            raise LLMOutputValidationError(f"unsupported fake prompt: {request.prompt_name}")
        serialized_input = json.dumps(request.payload, ensure_ascii=False)
        serialized_output = json.dumps(data, ensure_ascii=False)
        prompt_tokens = max(1, len(serialized_input) // 4)
        completion_tokens = max(1, len(serialized_output) // 4)
        usage = LLMUsageEvent(
            call_id=f"FAKE_{uuid4().hex.upper()}",
            agent=request.agent,
            provider=self.provider,
            model=self.model,
            prompt_version=request.prompt_version,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=round((perf_counter() - started) * 1000.0, 3),
        )
        _record_usage(usage)
        return LLMResponse(data=data, usage=usage)


def build_llm_client(settings: LLMSettings) -> LLMClient | None:
    if settings.agent_mode == AgentMode.DETERMINISTIC.value:
        return None
    if settings.agent_mode == AgentMode.FAKE.value:
        return FakeLLMClient()
    if settings.provider in {"dashscope", "qwen"}:
        return DashScopeLLMClient(settings)
    if settings.provider == "deepseek":
        adapter = DeepSeekAdapter(
            api_key=settings.api_key, base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds, max_retries=settings.max_retries,
        )
        adapter.validate_workflow(settings.model, min_context_tokens=settings.min_context_tokens)
        return RuntimeLLMClient(ModelRuntime(adapter), model=settings.model)
    raise LLMConfigurationError("unsupported configured LLM provider")
