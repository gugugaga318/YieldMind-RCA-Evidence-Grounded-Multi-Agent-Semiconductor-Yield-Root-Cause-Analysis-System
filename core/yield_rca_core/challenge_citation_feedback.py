"""Bounded, ephemeral citation repair facts from existing validator observations.

No validation policy, replacement citations, persistence, or model calls live here.
Only a rejected Lane citation check can populate the next repair input.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from yield_rca_core import challenge_diagnostics as diagnostics
from yield_rca_core.llm_gateway import LLMOutputValidationError

MAX_FEEDBACK_CHARS = 6000
MAX_FAILURES = 2
MAX_CHECKS = 8
_ROLES = (
    "supporting_evidence_ids", "contradicting_evidence_ids",
    "unexplained_precursor_evidence_ids",
)
_REQUIREMENT = (
    "Regardless of status, the cited Evidence set must contain at least one item "
    "matching the target Lane under the existing Lane/entity/time contract. "
    "Recheck and reselect citations; other-Lane Evidence may remain as comparison "
    "context if otherwise valid. A matching exposure fact does not establish "
    "parameter anomaly or a physical mechanism. Choose Evidence yourself from the "
    "supplied register; these diagnostics do not prescribe replacement IDs."
)


def _chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _cleaner(client: Any) -> diagnostics.ChallengeCapture:
    secrets = tuple(
        value for name, value in os.environ.items()
        if diagnostics._SENSITIVE.search(name) and value
    )
    for _ in range(4):
        key = getattr(getattr(getattr(client, "runtime", None), "adapter", None), "api_key", None)
        if isinstance(key, str) and key:
            secrets += (key,)
        client = getattr(client, "delegate", None)
        if client is None:
            break
    return diagnostics.ChallengeCapture(secrets=secrets)


@contextmanager
def capture_citation_failure(
    payload: Any, failures: list[dict[str, Any]], *, client: Any = None,
    lane_contexts: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Observe the original matcher once; never revalidate or change its verdict."""
    checks: list[dict[str, Any]] = []
    codes: set[str] = set()
    target: dict[str, Any] = {}
    count = 0
    raw = payload if isinstance(payload, Mapping) else {}

    def observe(evidence, lane, **result):
        nonlocal count
        if result["matched"]:
            return
        count += 1
        if result["code"]:
            codes.add(result["code"])
        if not target:
            target.update({
                key: lane.get(key)
                for key in ("lane_id", "operation", "equipment", "chamber", "recipe", "time_window")
            })
        entities = [
            e for e in evidence.entities
            if e.entity_type in {"operation", "equipment", "chamber", "recipe"}
        ]
        checks.append({
            "evidence_id": evidence.evidence_id,
            "actual_lane_id": evidence.metadata.get("lane_id"),
            "actual_entity": [
                {"entity_type": e.entity_type, "entity_id": e.entity_id}
                for e in entities[:16]
            ],
            "omitted_entity_count": max(0, len(entities) - 16),
            "actual_time": evidence.timestamp,
            "roles": [
                role for role in _ROLES
                if isinstance(raw.get(role), list)
                and evidence.evidence_id in [str(v).strip() for v in raw[role]]
            ],
            "rejection_code": result["code"],
            "mismatch_field": result.get("mismatch_field"),
            "lane_match": result["lane_match"],
            "entity_match": result["entity_match"],
            "time_match": result["time_match"],
        })
        checks.sort(key=lambda check: check["evidence_id"])
        del checks[MAX_CHECKS:]

    with diagnostics.observe_lane_checks(observe):
        try:
            yield
        except LLMOutputValidationError as exc:
            # This is the original validation error, not an independent decision.
            if "Entity/time-consistent Evidence" in str(exc) and len(failures) < MAX_FAILURES:
                try:
                    if not target:
                        alternative = raw.get("evidence_probe_lane_id")
                        if alternative is None:
                            alternative = raw.get("strongest_alternative_lane_id")
                        if alternative is None:
                            alternative = raw.get("strongest_alternative")
                        lane_id = str(alternative).strip() if alternative is not None else None
                        lane = (lane_contexts or {}).get(lane_id, {"lane_id": lane_id})
                        target = {
                            key: lane.get(key) for key in
                            (
                                "lane_id", "operation", "equipment",
                                "chamber", "recipe", "time_window",
                            )
                        }
                    record = _cleaner(client).clean({
                        "candidate_id": raw.get("candidate_id"),
                        "status": raw.get("status"),
                        "target_lane": target,
                        "rejection_codes": sorted(codes) or ["missing_required_evidence"],
                        "offending_evidence": checks,
                        "omitted_evidence_checks": max(0, count - len(checks)),
                        "requirement": _REQUIREMENT,
                    }, max_nodes=2048)
                    # Omit whole diagnostic entries, never truncate Evidence facts
                    # in the actual register or turn partial IDs into new IDs.
                    limit = MAX_FEEDBACK_CHARS // MAX_FAILURES - 2
                    while _chars(record) > limit and record["offending_evidence"]:
                        record["offending_evidence"].pop()
                        record["omitted_evidence_checks"] += 1
                    if _chars(record) > limit:
                        record = {
                            "rejection_codes": sorted(codes) or ["missing_required_evidence"],
                            "omitted": "citation_feedback_size_limit",
                            "requirement": _REQUIREMENT,
                        }
                    failures.append(record)
                except Exception:
                    pass  # Feedback construction must not replace the original failure.
            raise
