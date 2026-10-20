"""Server-owned model catalog and immutable per-job configuration boundary.

No model requests, environment mutation, credential persistence or RCA decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import replace
from threading import RLock
from typing import Any

from yield_rca_core.llm_gateway import LLMConfigurationError, LLMSettings, build_llm_client
from yield_rca_core.models import RCAState
from yield_rca_core.result_identity import configured_identity
from yield_rca_core.workflow import PurePythonRCAWorkflow, build_workflow

WorkflowFactory = Callable[[LLMSettings, str], PurePythonRCAWorkflow]
# Tuple records are immutable; labels are not Runtime provider identifiers.
OPTIONS = (
    (
        "dashscope",
        "Qwen",
        "qwen-plus",
        "Qwen Plus",
        "DASHSCOPE_API_KEY",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    (
        "deepseek",
        "DeepSeek",
        "deepseek-v4-pro",
        "DeepSeek V4 Pro",
        "DEEPSEEK_API_KEY",
        "https://api.deepseek.com",
    ),
)
CATALOG_VERSION = "web-model-selection-v1"


class ModelSelectionError(ValueError):
    def __init__(self, code: str, *, status_code: int = 422, state: RCAState | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.state = state


def validate_pair(provider: str, model: str) -> None:
    if not any(provider == p and model == m for p, _, m, *_ in OPTIONS):
        raise ModelSelectionError("MODEL_SELECTION_NOT_ALLOWED")


def _fingerprint(settings: LLMSettings) -> str:
    # Not a credential hash. Secrets never participate in persisted configuration.
    values = {
        name: getattr(settings, name)
        for name in (
            "agent_mode",
            "provider",
            "model",
            "base_url",
            "timeout_seconds",
            "max_retries",
            "min_context_tokens",
        )
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class ModelSelectionService:
    def __init__(self, defaults: LLMSettings, *, orchestration_mode: str, dataset: str):
        self.defaults = defaults
        self.orchestration_mode = orchestration_mode
        self.dataset = dataset

    def settings_for(self, provider: str, model: str) -> LLMSettings:
        validate_pair(provider, model)
        if self.defaults.agent_mode != "llm":
            raise ModelSelectionError("MODEL_SELECTION_REQUIRES_LLM_MODE", status_code=503)
        option = next(o for o in OPTIONS if o[0] == provider)
        # Explicit selections bind the provider's own credential and endpoint.
        key = os.getenv(option[4], "").strip()
        if not key:
            raise ModelSelectionError("MODEL_PROVIDER_NOT_CONFIGURED", status_code=503)
        settings = replace(
            self.defaults, provider=provider, model=model, api_key=key, base_url=option[5]
        )
        self.validate_capabilities(settings)
        return settings

    @staticmethod
    def validate_capabilities(settings: LLMSettings) -> None:
        if settings.agent_mode != "llm":
            return
        try:
            # Constructors and DeepSeek qualification only; complete_json is never called.
            # Qwen's approved qwen-plus profile has unknown advertised context/usage;
            # do not invent capability numbers or reject the accepted legacy contract.
            client = build_llm_client(settings)
            caps = client.runtime.adapter.capabilities
            if (
                "json_object" not in caps.output_modes
                or not {"system", "user"}.issubset(caps.message_roles)
                or caps.temperature_range[0] > 0
                or caps.temperature_range[1] < 2
            ):
                raise LLMConfigurationError("required workflow capabilities unavailable")
        except LLMConfigurationError as exc:
            raise ModelSelectionError("MODEL_CONFIGURATION_INVALID", status_code=503) from exc

    def catalog(self) -> dict[str, Any]:
        providers = []
        for provider, label, model, model_label, key_name, _ in OPTIONS:
            configured = bool(os.getenv(key_name, "").strip())
            reason = None
            try:
                self.settings_for(provider, model)
            except ModelSelectionError as exc:
                reason = exc.code
            providers.append(
                {
                    "provider": provider,
                    "display_name": label,
                    "configured": configured,
                    "models": [
                        {
                            "model": model,
                            "display_name": model_label,
                            "available": reason is None,
                            "unavailable_reason": reason,
                        }
                    ],
                }
            )
        default = {"provider": self.defaults.provider, "model": self.defaults.model}
        return {
            "catalog_version": CATALOG_VERSION,
            "providers": providers,
            "default_selection": default,
        }

    def freeze(self, selection: Mapping[str, str] | None) -> dict[str, Any]:
        settings = (
            self.settings_for(selection["provider"], selection["model"])
            if selection
            else self.defaults
        )
        self.validate_capabilities(settings)
        return {
            "agent_mode": settings.agent_mode,
            "provider": settings.provider,
            "model": settings.model,
            "orchestration_mode": self.orchestration_mode,
            "dataset": self.dataset,
            "model_selection": {
                "catalog_version": CATALOG_VERSION,
                "requested_provider": selection["provider"] if selection else None,
                "requested_model": selection["model"] if selection else None,
                "source": "explicit" if selection else "server_default",
                "settings_fingerprint": _fingerprint(settings),
            },
        }

    def resolve(self, config: dict[str, Any], request: dict[str, Any]) -> LLMSettings:
        if (
            config.get("dataset") != self.dataset
            or config.get("orchestration_mode") != self.orchestration_mode
            or config.get("agent_mode") != self.defaults.agent_mode
        ):
            raise ModelSelectionError("WORKER_RUNTIME_CONFIG_MISMATCH")
        provider, model = config.get("provider"), config.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ModelSelectionError("WORKER_RUNTIME_CONFIG_MISMATCH")
        forbidden = {
            "api_key",
            "base_url",
            "endpoint",
            "timeout",
            "timeout_seconds",
            "retry",
            "max_retries",
            "temperature",
            "context_limit",
            "params",
        }
        if forbidden.intersection(config):
            raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
        stamp = config.get("model_selection")
        explicit = request.get("model_selection")
        if explicit is not None and self.defaults.agent_mode != "llm":
            raise ModelSelectionError("MODEL_SELECTION_REQUIRES_LLM_MODE")
        if stamp is not None:
            required = {
                "catalog_version",
                "requested_provider",
                "requested_model",
                "source",
                "settings_fingerprint",
            }
            if not isinstance(stamp, dict) or set(stamp) != required:
                raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
            if stamp["catalog_version"] != CATALOG_VERSION:
                raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
            expected_request = (
                {"provider": stamp["requested_provider"], "model": stamp["requested_model"]}
                if stamp["source"] == "explicit"
                else None
            )
            if (
                stamp["source"] not in {"explicit", "server_default"}
                or explicit != expected_request
                or (expected_request and expected_request != {"provider": provider, "model": model})
                or (
                    not expected_request
                    and (
                        stamp["requested_provider"] is not None
                        or stamp["requested_model"] is not None
                    )
                )
            ):
                raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
        elif explicit is not None:
            raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
        # Reject user/producer-supplied transport configuration instead of using it.
        allowed = {
            "agent_mode",
            "provider",
            "model",
            "orchestration_mode",
            "dataset",
            "model_selection",
        }
        if stamp is not None and set(config) != allowed:
            raise ModelSelectionError("MODEL_SELECTION_SNAPSHOT_INVALID")
        if self.defaults.agent_mode == "llm":
            if stamp is not None and stamp["source"] == "explicit":
                settings = self.settings_for(provider, model)
            elif (provider, model) == (self.defaults.provider, self.defaults.model):
                settings = self.defaults
                self.validate_capabilities(settings)
            else:
                settings = self.settings_for(provider, model)
        else:
            if (provider, model) != (self.defaults.provider, self.defaults.model):
                raise ModelSelectionError("WORKER_RUNTIME_CONFIG_MISMATCH")
            settings = self.defaults
        if stamp is not None and stamp["settings_fingerprint"] != _fingerprint(settings):
            raise ModelSelectionError("WORKER_RUNTIME_CONFIG_MISMATCH")
        return settings


def workflow_factory(repository: Any, **dependencies: Any) -> WorkflowFactory:
    # Reuse provider-independent repositories/retrieval resources. Serialize assembly
    # because CSV repositories have a lazy cache; Agents/client are fresh per job.
    lock = RLock()

    def build(settings: LLMSettings, orchestration_mode: str) -> PurePythonRCAWorkflow:
        with lock:
            return build_workflow(
                repository,
                llm_settings=settings,
                orchestration_mode=orchestration_mode,
                **dependencies,
            )

    return build


def workflow_identity(workflow: PurePythonRCAWorkflow) -> dict[str, Any] | None:
    client = getattr(getattr(workflow, "supervisor", None), "llm_client", None)
    return configured_identity(client).to_dict() if client is not None else None


def selection_metadata(
    config: dict[str, Any], actual: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    stamp = config.get("model_selection")
    if not isinstance(stamp, dict):
        return None
    actual = actual if actual and actual.get("basis") == "runtime_config" else None
    expected = (config["provider"], config["model"])
    actual_pair = (actual.get("provider"), actual.get("model")) if actual else None
    return {
        "requested_provider": stamp["requested_provider"],
        "requested_model": stamp["requested_model"],
        "source": stamp["source"],
        "resolved_provider": expected[0],
        "resolved_model": expected[1],
        "actual_provider": actual_pair[0] if actual_pair else None,
        "actual_model": actual_pair[1] if actual_pair else None,
        "identity_match": actual_pair == expected if actual_pair else None,
    }


def verify_execution_identity(
    state: RCAState,
    config: dict[str, Any],
    identity: dict[str, Any] | None,
) -> None:
    """Check this attempt's client, never a prior retry's retained State identity."""
    metadata = selection_metadata(config, identity)
    if metadata is None or config["agent_mode"] != "llm":
        return
    if metadata["identity_match"] is not True:
        updated = replace(
            state, execution_metadata={**state.execution_metadata, "model_selection": metadata}
        )
        code = (
            "MODEL_IDENTITY_MISMATCH"
            if metadata["identity_match"] is False
            else "MODEL_IDENTITY_UNAVAILABLE"
        )
        raise ModelSelectionError(code, status_code=500, state=updated)


def attach_selection(
    state: RCAState,
    config: dict[str, Any],
    *,
    execution_identity: dict[str, Any] | None = None,
    enforce_match: bool = True,
) -> RCAState:
    # Actual is from the Runtime client or completed State, never requested fields.
    actual = state.execution_metadata.get("model_identity") or execution_identity
    metadata = selection_metadata(config, actual)
    if metadata is None:
        return state
    updated = replace(
        state, execution_metadata={**state.execution_metadata, "model_selection": metadata}
    )
    if enforce_match and config["agent_mode"] == "llm" and metadata["identity_match"] is not True:
        code = (
            "MODEL_IDENTITY_MISMATCH"
            if metadata["identity_match"] is False
            else "MODEL_IDENTITY_UNAVAILABLE"
        )
        raise ModelSelectionError(code, state=updated)
    return updated
