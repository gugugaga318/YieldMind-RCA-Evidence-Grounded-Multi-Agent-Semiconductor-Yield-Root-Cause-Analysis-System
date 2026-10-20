"""Authorship views, independent of RCA decision authority and model output JSON."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ResultIdentity:
    author: str
    provider: str | None = None
    model: str | None = None
    basis: str = "legacy_source"

    def __post_init__(self) -> None:
        if self.author not in {"model", "python_runtime", "user", "unknown"}:
            raise ValueError("invalid result author")
        if self.basis not in {"runtime_config", "legacy_source", "python_runtime"}:
            raise ValueError("invalid identity basis")
        if self.author != "model" and (self.provider is not None or self.model is not None):
            raise ValueError("non-model authors cannot carry model execution identity")
        if self.basis == "python_runtime" and self.author != "python_runtime":
            raise ValueError("Python identity basis requires Python authorship")
        if self.basis == "runtime_config" and (
            self.author != "model"
            or not isinstance(self.provider, str)
            or not self.provider.strip()
            or not isinstance(self.model, str)
            or not self.model.strip()
        ):
            raise ValueError("runtime model identity requires provider and model")
        if self.basis == "legacy_source" and (self.provider is not None or self.model is not None):
            raise ValueError("legacy source cannot establish actual provider/model")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResultIdentity:
        return cls(**dict(value))


def configured_identity(client: Any) -> ResultIdentity:
    """Read the injected Python client configuration, never response.data."""
    runtime = getattr(client, "runtime", None)
    adapter = getattr(runtime, "adapter", None)
    provider = getattr(adapter, "provider", None) or getattr(client, "provider", None)
    model = getattr(client, "model", None)
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        return ResultIdentity("model")
    return ResultIdentity("model", provider, model, "runtime_config")


def model_source(client: Any) -> str:
    """Keep the deployed Qwen and historical fake fixture compatibility values."""
    identity = configured_identity(client)
    return "qwen" if identity.provider in {None, "dashscope", "qwen", "fake"} else "model"


def resolve_identity(
    source: str | None,
    binding: Mapping[str, Any] | None = None,
) -> ResultIdentity:
    if source in {"qwen", "llm_qwen", "model", "llm_model"}:
        if binding is not None:
            identity = ResultIdentity.from_dict(binding)
            if identity.author != "model":
                raise ValueError("model source requires model execution binding")
            return identity
        return ResultIdentity("model")
    if source in {
        "python",
        "python_runtime",
        "python_controlled",
        "python_evidence_gate",
        "deterministic_only",
        "deterministic_typed_evidence",
        "deterministic_fallback",
        "python_legacy_compatibility",
        "system",
    }:
        return ResultIdentity("python_runtime", basis="python_runtime")
    return ResultIdentity("user" if source == "user" else "unknown")


def state_identity_view(state: Any) -> dict[str, dict[str, Any]]:
    """Read-only projection; no history rewrite, prompt input or authority selection.

    The explicit binding applies to the workflow's one injected model client.
    Legacy metadata.provider/model alone is not promoted into a new binding.
    Keys name concrete result locations, including historical Findings.
    """

    def mapping(value: object) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    binding = state.execution_metadata.get("model_identity")
    result: dict[str, dict[str, Any]] = {}

    def add(key: str, source: str | None) -> None:
        if isinstance(source, str):
            result[key] = resolve_identity(source, binding).to_dict()

    for finding in state.findings:
        prefix = f"findings/{finding.finding_id}"
        for name in ("hypothesis_candidate_generation", "adversarial_challenge_generation"):
            raw = mapping(finding.details.get(name))
            add(f"{prefix}/{name}", raw.get("source"))
        engine = mapping(finding.details.get("hypothesis_engine_result"))
        comparison = mapping(engine.get("candidate_comparison"))
        add(f"{prefix}/candidate_comparison", comparison.get("source"))
        profiles = finding.details.get("candidate_semantic_profiles")
        for profile in profiles if isinstance(profiles, (list, tuple)) else ():
            if isinstance(profile, Mapping) and isinstance(profile.get("candidate_id"), str):
                add(f"{prefix}/semantic_profiles/{profile['candidate_id']}", profile.get("source"))
        specialist = mapping(finding.details.get("specialist_v2"))
        add(f"{prefix}/specialist_analysis", specialist.get("analysis_source"))
    add("planner/terminal_proposal", state.execution_metadata.get("planner_stop_proposed_by"))
    add(
        "planner/terminal_question_updates",
        state.execution_metadata.get("terminal_question_updates_source"),
    )
    for decision_id, raw_origin in mapping(state.execution_metadata.get("planner_origins")).items():
        origin = mapping(raw_origin)
        add(f"planner/{decision_id}/proposal", origin.get("decision"))
        add(f"planner/{decision_id}/question_updates", origin.get("question_updates"))
    for notice_index, notice in enumerate(state.capability_notices):
        add(f"capability_notices/{notice_index}", notice.request_source)
    return result
