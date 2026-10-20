"""Opt-in bounded Challenge diagnostics, separate from State and model facts.

No Prompt/HTTP capture. Only an explicit caller scope can retain diagnostics;
artifact persistence is explicit. Failures of observation never decide RCA.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from yield_rca_core.model_telemetry import capture_model_telemetry

MAX_RESPONSE_BYTES = 16384
MAX_GENERATION_BYTES = 65536
MAX_TOTAL_BYTES = 524288
_FIELDS = {
    "candidate_id",
    "alternative_candidate_id",
    "evidence_probe_lane_id",
    "strongest_alternative_lane_id",
    "strongest_alternative",
    "challenge_kind",
    "mechanism_relation",
    "supporting_evidence_ids",
    "contradicting_evidence_ids",
    "unexplained_precursor_evidence_ids",
    "distinguishing_gap_ids",
    "distinguishing_questions",
    "challenge_explanation",
    "explanation",
    "status",
    "entity",
    "entities",
    "scope",
    "time_window",
    "timestamp",
    "reason",
    "rationale",
}
_SENSITIVE = re.compile(
    r"(?i)authorization|api.?key|secret|password|credential|headers|access.?token"
)
_CAPTURE: ContextVar[Any] = ContextVar("challenge_diagnostic_capture", default=None)
_GENERATION: ContextVar[Any] = ContextVar("challenge_diagnostic_generation", default=None)
_LANE_CHECK_OBSERVER: ContextVar[Any] = ContextVar("challenge_lane_check_observer", default=None)
_VALIDATION: ContextVar[Any] = ContextVar("challenge_diagnostic_validation", default=None)


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class ChallengeCapture:
    include_response_json: bool = False
    secrets: tuple[str, ...] = field(default=(), repr=False)
    max_generations: int = 20
    records: list[dict[str, Any]] = field(default_factory=list)
    dropped_count: int = 0
    bytes_retained: int = 0
    truncation_count: int = 0

    def clean(self, value: Any, *, max_nodes: int = 512) -> Any:
        nodes = 0

        def walk(item: Any, depth: int = 0) -> Any:
            nonlocal nodes
            nodes += 1
            if nodes > max_nodes or depth > 8:
                self.truncation_count += 1
                return {"omitted": "diagnostic_structure_limit"}
            if isinstance(item, str):
                for secret in sorted(self.secrets, key=len, reverse=True):
                    if secret:
                        item = item.replace(secret, "[REDACTED]")
                item = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", item)
                item = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", item)
                if len(item) > 1024:
                    self.truncation_count += 1
                    return item[:1024] + "[DIAGNOSTIC_TRUNCATED]"
                return item
            if isinstance(item, Mapping):
                pairs = list(item.items())[:64]
                out = {
                    str(walk(str(k), depth + 1)): (
                        "[REDACTED]" if _SENSITIVE.search(str(k)) else walk(v, depth + 1)
                    )
                    for k, v in pairs
                }
                if len(item) > 64:
                    self.truncation_count += 1
                    out["_diagnostic_omitted_fields"] = len(item) - 64
                return out
            if isinstance(item, (tuple, list)):
                out = [walk(v, depth + 1) for v in item[:64]]
                if len(item) > 64:
                    self.truncation_count += 1
                    out.append({"diagnostic_omitted_items": len(item) - 64})
                return out
            if item is None or type(item) in (bool, int, float):
                return item
            return {"omitted": "unsupported_diagnostic_type"}

        return walk(value)

    def retain(self, record: dict[str, Any]) -> None:
        # Everything is already sanitized at collection; sanitize once more
        # before persistence so error paths cannot bypass the boundary.
        record = self.clean(record, max_nodes=8192)
        size = len(_encoded(record))
        if size > MAX_GENERATION_BYTES:
            record = {
                "request_id": record.get("request_id"),
                "generation": record.get("generation"),
                "omitted": "generation_size_limit",
                "diagnostic_bytes": size,
            }
            self.dropped_count += 1
            size = len(_encoded(record))
        if (
            len(self.records) >= self.max_generations
            or self.bytes_retained + size > MAX_TOTAL_BYTES
        ):
            self.dropped_count += 1
            return
        self.records.append(record)
        self.bytes_retained += size

    def write(self, path: Path) -> bool:
        """Explicit sidecar write. Failure is observable, never a workflow error."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            document = {
                "schema_version": "1.0",
                "kind": "challenge_diagnostics",
                "include_response_json": self.include_response_json,
                "complete": self.dropped_count == 0 and self.truncation_count == 0,
                "truncation_count": self.truncation_count,
                "dropped_count": self.dropped_count,
                "records": self.records,
            }
            path.write_bytes(_encoded(document) + b"\n")
            return True
        except Exception:
            self.dropped_count += 1
            return False


@contextmanager
def capture_challenge_diagnostics(
    *,
    include_response_json: bool = False,
    secrets: tuple[str, ...] = (),
    max_generations: int = 20,
) -> Iterator[ChallengeCapture]:
    if not 0 <= max_generations <= 20:
        raise ValueError("Challenge diagnostics max_generations must be between 0 and 20")
    known = tuple(value for name, value in os.environ.items() if _SENSITIVE.search(name) and value)
    capture = ChallengeCapture(include_response_json, secrets + known, max_generations)
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


@contextmanager
def challenge_diagnostic_artifact(
    path: Path,
    *,
    enabled: bool = False,
    include_response_json: bool = False,
) -> Iterator[None]:
    if not enabled:
        yield
        return
    with capture_challenge_diagnostics(include_response_json=include_response_json) as capture:
        try:
            yield
        finally:
            if not capture.write(path):
                try:
                    warnings.warn(
                        "Challenge diagnostic artifact could not be saved",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                except Exception:
                    pass  # Even warnings-as-errors cannot alter the workflow.


def _safe_observer(function):
    @wraps(function)
    def safe(*args, **kwargs):
        if _CAPTURE.get() is None:
            return None
        try:
            return function(*args, **kwargs)
        except Exception:
            _CAPTURE.get().dropped_count += 1
            return None

    return safe


@_safe_observer
def begin_attempt(attempt: int) -> None:
    generation = _GENERATION.get()
    if generation is not None:
        generation["attempts"].append(
            {
                "attempt": attempt,
                "is_repair": attempt > 1,
                "logical_call_started": False,
                "logical_call_id": None,
                "outcome": "construction",
                "validations": [],
            }
        )


def _attempt():
    generation = _GENERATION.get()
    return generation["attempts"][-1] if generation and generation["attempts"] else None


@_safe_observer
def call_started() -> None:
    attempt = _attempt()
    if attempt is not None:
        attempt["logical_call_started"] = True


@_safe_observer
def response_received(response: Any) -> None:
    attempt = _attempt()
    if attempt is None:
        return
    capture = _CAPTURE.get()
    attempt["logical_call_id"] = getattr(response.usage, "call_id", None)
    data = response.data
    raw = data.get("challenges") if isinstance(data, Mapping) else None
    attempt["model_output"] = capture.clean(
        {
            "challenges": [
                {key: value for key, value in item.items() if key in _FIELDS}
                if isinstance(item, Mapping)
                else item
                for item in raw[:64]
            ]
            if isinstance(raw, list)
            else raw,
            "challenge_count": len(raw) if isinstance(raw, list) else None,
        }
    )
    if isinstance(raw, list) and len(raw) > 64:
        capture.truncation_count += 1
    if capture.include_response_json:
        cleaned = capture.clean(data)
        if len(_encoded(cleaned)) > MAX_RESPONSE_BYTES:
            capture.truncation_count += 1
        attempt["parsed_response_json"] = (
            cleaned
            if len(_encoded(cleaned)) <= MAX_RESPONSE_BYTES
            else {"omitted": "response_size_limit", "limit_bytes": MAX_RESPONSE_BYTES}
        )


@_safe_observer
def attempt_outcome(outcome: str, error: str | None = None) -> None:
    attempt = _attempt()
    if attempt is not None:
        attempt["outcome"] = outcome
        if error is not None:
            attempt["error"] = _CAPTURE.get().clean(error)
        if outcome == "validation_rejected":
            attempt["rejection_codes"] = sorted(
                {
                    code
                    for validation in attempt["validations"]
                    for code in validation.get("rejection_codes", [])
                }
            ) or _codes(error or "", {"evidence_checks": []})


def _generation_begin(challenger: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    capture = _CAPTURE.get()
    # Obtain the configured credential, including under the existing cap wrapper.
    client = challenger.llm_client
    for _ in range(4):
        key = getattr(getattr(getattr(client, "runtime", None), "adapter", None), "api_key", None)
        if isinstance(key, str) and key:
            capture.secrets += (key,)
        client = getattr(client, "delegate", None)
        if client is None:
            break
    return {
        "generation": len(capture.records) + 1,
        "request_id": capture.clean(kwargs.get("request_id")),
        "provider": capture.clean(getattr(challenger.llm_client, "provider", None)),
        "model": capture.clean(getattr(challenger.llm_client, "model", None)),
        "attempts": [],
    }


def observe_generation(function):
    @wraps(function)
    def wrapped(challenger, **kwargs):
        capture = _CAPTURE.get()
        if capture is None:
            return function(challenger, **kwargs)
        try:
            record = _generation_begin(challenger, kwargs)
        except Exception:
            capture.dropped_count += 1
            return function(challenger, **kwargs)
        token = _GENERATION.set(record)
        result = None
        # Reuse existing model events solely to correlate failed calls too.
        with capture_model_telemetry(max_events=16) as facts:
            try:
                result = function(challenger, **kwargs)
                return result
            finally:
                try:
                    call_ids = [e.logical_call_id for e in facts.events if e.kind == "logical_call"]
                    started = [a for a in record["attempts"] if a["logical_call_started"]]
                    for index, attempt in enumerate(started):
                        if index < len(call_ids):
                            attempt["logical_call_id"] = call_ids[index]
                        attempt["logical_call_id_available"] = (
                            attempt["logical_call_id"] is not None
                        )
                    record["attempt_count"] = getattr(result, "attempt_count", None)
                    record["output_invalid"] = getattr(result, "output_invalid", None)
                    record["model_event_dropped_count"] = facts.dropped_count
                    capture.retain(record)
                except Exception:
                    capture.dropped_count += 1
                finally:
                    _GENERATION.reset(token)

    return wrapped


@contextmanager
def observe_lane_checks(observer) -> Iterator[None]:
    """Ephemeral repair listener; independent of opt-in diagnostic persistence."""
    token = _LANE_CHECK_OBSERVER.set(observer)
    try:
        yield
    finally:
        _LANE_CHECK_OBSERVER.reset(token)


def _notify_lane_check(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        observer = _LANE_CHECK_OBSERVER.get()
        if observer is not None:
            try:
                observer(*args, **kwargs)
            except Exception:
                pass  # Observation cannot change the predicate's result.
        return function(*args, **kwargs)
    return wrapped


@_notify_lane_check
@_safe_observer
def lane_check(
    evidence: Any,
    lane: Mapping[str, Any],
    *,
    matched: bool,
    code: str | None,
    lane_match: bool | None,
    entity_match: bool | None,
    time_match: bool | None,
    mismatch_field: str | None = None,
) -> None:
    validation = _VALIDATION.get()
    if validation is None:
        return
    capture = _CAPTURE.get()
    if len(validation["evidence_checks"]) >= 64:
        capture.truncation_count += 1
        return
    validation["evidence_checks"].append(
        capture.clean(
            {
                "evidence_id": evidence.evidence_id,
                "matched": matched,
                "rejection_code": code,
                "mismatch_field": mismatch_field,
                "expected_entity": {
                    k: lane.get(k) for k in ("operation", "equipment", "chamber", "recipe")
                },
                "actual_evidence_entity": [
                    {"entity_type": e.entity_type, "entity_id": e.entity_id}
                    for e in evidence.entities
                ],
                "expected_lane_id": lane.get("lane_id"),
                "actual_lane_id": evidence.metadata.get("lane_id"),
                "expected_time_scope": lane.get("time_window"),
                "actual_evidence_time_scope": evidence.timestamp,
                "lane_match": lane_match,
                "entity_match": entity_match,
                "time_match": time_match,
            }
        )
    )


def _codes(message: str, record: dict[str, Any]) -> list[str]:
    if "unknown Evidence IDs" in message:
        return ["unknown_evidence_id"]
    if "Entity/time-consistent Evidence" in message:
        codes = {e["rejection_code"] for e in record["evidence_checks"] if e["rejection_code"]}
        return sorted(codes) or ["missing_required_evidence"]
    if "both supporting and contradicting" in message or "precursor references" in message:
        return ["support_role_mismatch"]
    if "different causal Lane" in message or "Gap owned by another" in message:
        return ["scope_mismatch"]
    if "unknown causal Lane" in message or "unknown strongest alternative Lane" in message:
        return ["lane_mismatch"]
    if "requires distinguishing Evidence" in message:
        return ["missing_required_evidence"]
    if any(
        text in message
        for text in (
            "mechanism_relation",
            "challenge_kind",
            "alternative_candidate_id",
            "two-Candidate mechanism",
            "one-Candidate mechanism",
            "lane_probe requires",
        )
    ):
        return ["malformed_challenge_relation"]
    return ["other_validation_failure"]


def observe_validation(function):
    @wraps(function)
    def wrapped(payload, **kwargs):
        capture = _CAPTURE.get()
        attempt = _attempt()
        if capture is None or attempt is None:
            return function(payload, **kwargs)
        try:
            raw = payload if isinstance(payload, Mapping) else {}
            # Mirror the existing alias precedence, including explicit empty values.
            alternative = raw.get("evidence_probe_lane_id")
            if alternative is None:
                alternative = raw.get("strongest_alternative_lane_id")
            if alternative is None:
                alternative = raw.get("strongest_alternative")
            alternative_id = (str(alternative).strip() or None) if alternative is not None else None
            gap_ids = raw.get("distinguishing_gap_ids", [])
            record = {
                "expected_lane_context": capture.clean(
                    (kwargs.get("lane_contexts") or {}).get(alternative_id)
                ),
                "selected_gap_context": capture.clean(
                    {
                        key: value
                        for key, value in (kwargs.get("gap_by_id") or {}).items()
                        if isinstance(gap_ids, list) and key in gap_ids
                    }
                ),
                "challenge_id": (
                    f"attempt-{attempt['attempt']}:challenge-{len(attempt['validations'])}"
                ),
                "candidate_id": capture.clean(raw.get("candidate_id")),
                "alternative_lane_id": capture.clean(alternative_id),
                "model_output": capture.clean({k: v for k, v in raw.items() if k in _FIELDS}),
                "cited_evidence_ids": capture.clean(
                    {
                        k: raw.get(k, [])
                        for k in (
                            "supporting_evidence_ids",
                            "contradicting_evidence_ids",
                            "unexplained_precursor_evidence_ids",
                        )
                    }
                ),
                "evidence_checks": [],
            }
        except Exception:
            capture.dropped_count += 1
            return function(payload, **kwargs)
        token = _VALIDATION.set(record)
        try:
            result = function(payload, **kwargs)
            record["accepted"] = True
            record["rejection_codes"] = []
            return result
        except Exception as exc:
            record["accepted"] = False
            try:
                message = str(exc)
                record["rejection_codes"] = _codes(message, record)
                record["rejection_detail"] = capture.clean(message)
            except Exception:
                capture.dropped_count += 1
            raise
        finally:
            _VALIDATION.reset(token)
            try:
                if len(attempt["validations"]) < 64:
                    attempt["validations"].append(record)
                else:
                    capture.dropped_count += 1
            except Exception:
                capture.dropped_count += 1

    return wrapped
