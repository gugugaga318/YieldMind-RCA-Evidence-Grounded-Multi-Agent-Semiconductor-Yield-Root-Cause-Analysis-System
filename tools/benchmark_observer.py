"""Opt-in, thread-local Benchmark observation; never imported by production.

Uses Python call/return observation of frozen boundaries. No function is replaced,
no requests mutated and no retry performed. A dropped observation invalidates coverage.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from threading import Lock

OPS = {
    ("supervisor", "_review_specialist_finding"): "specialist_review",
    ("intent_planner", "plan_with_diagnostics"): "intent",
    ("hypothesis_candidate_generator", "generate"): "candidate",
    ("causal_adversarial", "generate"): "challenge",
    ("causal_candidate_comparison", "compare"): "comparator",
    ("next_action_planner", "_decide"): "next_action",
    ("specialist_v2", "_choose_tool"): "specialist_tool",
    ("specialist_v2", "_analyze"): "specialist_analysis",
    ("planner_agent", "plan"): "planner",
    ("improvement_agent", "analyze"): "improvement",
}
BOUNDARIES = {
    ("hypothesis_engine", "analyze"),
    ("causal_confirmation", "confirm_candidate"),
    ("rca_reasoning_agent", "analyze"),
    ("investigation_finalizer", "finalize_investigation"),
    ("investigation_finalizer", "finalize_non_competition_llm_react_terminal"),
    ("workflow", "run"),
}
TAXONOMY = (
    "unknown_evidence_id",
    "lane_mismatch",
    "entity_mismatch",
    "time_mismatch",
    "scope_mismatch",
    "support_role_mismatch",
    "semantic_profile_error",
    "missing_required_evidence",
    "malformed_challenge_relation",
    "other_validation_failure",
)


def serial(value):
    if hasattr(value, "to_dict"):
        return serial(value.to_dict())
    if is_dataclass(value):
        return serial(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def clean(value):
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(r"secret|api.?key|authorization|password|access.?token", k, re.I)
                else clean(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)[:6000]
    return value


def codes(messages):
    """Labels existing error facts, never re-runs or relaxes a validator."""
    text = json.dumps(serial(messages)).casefold()
    result = {c for c in TAXONOMY if c in text}
    patterns = {
        "unknown_evidence_id": ["unknown evidence", "unknown_evidence", "unknown supporting"],
        "semantic_profile_error": ["semantic profile", "semantic_profile", "semantic coherence"],
        "support_role_mismatch": ["both supporting and contradict", "support role"],
        "lane_mismatch": ["lane mismatch"],
        "entity_mismatch": ["entity mismatch"],
        "time_mismatch": ["time mismatch"],
        "scope_mismatch": ["scope mismatch"],
    }
    for code, tokens in patterns.items():
        if any(x in text for x in tokens):
            result.add(code)
    return sorted(result or ({"other_validation_failure"} if messages else set()))


def candidates(items, profiles=(), *, raw=False):
    profiles = serial(profiles)
    result = []
    indexed = {}
    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        key = profile.get("candidate_index")
        if isinstance(key, int) and not isinstance(key, bool):
            indexed[key] = None if key in indexed else profile
    for index, item in enumerate(serial(items or [])):
        if not isinstance(item, dict):
            result.append({"raw_index": index, "malformed": True})
            continue
        kept = {
            k: item[k]
            for k in (
                "recipe_version",
                "time_window",
                "component",
                "sub_step",
                "components",
                "sub_steps",
                "candidate_id",
                "hypothesis_id",
                "root_cause",
                "causal_explanation",
                "supporting_evidence_ids",
                "contradicting_evidence_ids",
                "semantic_profile",
                "status",
                "confirmation",
                "confirmation_status",
                "confirmation_gate",
                "rank",
                "rejection_reasons",
            )
            if k in item
        }
        profile = item.get("semantic_profile")
        if profile is None:
            profile = (
                indexed.get(index) if raw else (profiles[index] if index < len(profiles) else None)
            )
        if profile is not None:
            kept["semantic_profile"] = profile
        # Lexical lineage anchor only, not the evaluator's canonical mechanism ID.
        kept["lineage_key"] = sha(str(item.get("root_cause", "")).strip().casefold())
        kept["raw_index"] = index
        result.append(kept)
    return result


def fallback_facts(state):
    """Existing executed-fallback markers, never a new fallback decision."""
    facts = []
    meta = state.get("execution_metadata") or {}
    if meta.get("orchestration_fallback_reason"):
        facts.append({"source": "orchestration", "reason": meta["orchestration_fallback_reason"]})

    def visit(value, path):
        if not isinstance(value, dict):
            return
        if value.get("fallback_reason"):
            facts.append({"source": path, "reason": value["fallback_reason"]})
        for key, child in value.items():
            if isinstance(child, dict):
                visit(child, path + "/" + key)

    for finding in state.get("findings", []):
        visit(finding.get("details", {}), "finding/" + str(finding.get("finding_id")))
    return facts


class Capture:
    def __init__(self, max_events=2000, max_bytes=4_000_000):
        self.operations = []
        self.snapshots = []
        self.diagnostics = []
        self.dropped_count = 0
        self.max_events, self.max_bytes, self.used = max_events, max_bytes, 0
        self.active, self.calls = {}, {}
        self.previous = None
        self.last_candidate_operation_id = None
        self.candidate_bindings = {}

    def append(self, target, value):
        raw = serial(value)
        value = clean(raw)
        # Any lossy string bound invalidates capture; redaction itself is intentional.
        if any(len(v) > 6000 for v in _strings(raw)):
            self.dropped_count += 1
        size = len(json.dumps(value).encode())
        if (
            len(self.snapshots) + len(self.diagnostics) >= self.max_events
            or self.used + size > self.max_bytes
        ):
            self.dropped_count += 1
            return
        self.used += size
        target.append(value)

    def nearest(self, frame):
        while frame:
            if id(frame) in self.active:
                return self.active[id(frame)]
            frame = frame.f_back
        return None

    def errors(self, frame):
        return [
            *serial(frame.f_locals.get("validation_errors", [])),
            *serial(frame.f_locals.get("candidate_errors", [])),
            *serial(frame.f_locals.get("semantic_validation_errors", [])),
        ]

    def trace(self, frame, event, arg):
        try:
            module = frame.f_globals.get("__name__", "")
            key = (module.rsplit(".", 1)[-1], frame.f_code.co_name)
            if event == "call":
                if key == ("hypothesis_candidate_generator", "_candidate_competition_assessment"):
                    op = self.nearest(frame.f_back)
                    self.append(
                        self.snapshots,
                        {
                            "stage": "admitted_candidate",
                            "operation_id": op["operation_id"] if op else None,
                            "attempt_index": len(op["attempts"]) - 1 if op else None,
                            "boundary": (
                                "validated/closure-assessed proposals before competition assessment"
                            ),
                            "candidates": candidates(
                                frame.f_locals.get("proposals", []),
                                frame.f_locals.get("semantic_profiles", []),
                            ),
                        },
                    )
                    return None
                if module.startswith("yield_rca_core.") and key in OPS:
                    op = {
                        "operation_id": f"op_{len(self.operations) + 1}",
                        "stage": OPS[key],
                        "attempts": [],
                        "outcome": "started",
                        "error_codes": [],
                    }
                    if len(self.operations) >= self.max_events:
                        self.dropped_count += 1
                        return None
                    self.operations.append(op)
                    self.active[id(frame)] = op
                    if op["stage"] == "candidate":
                        self.last_candidate_operation_id = op["operation_id"]
                    return self.trace
                if key[1] == "complete_json" and id(frame.f_back) in self.active:
                    op = self.active[id(frame.f_back)]
                    prior = op["attempts"][-1] if op["attempts"] else None
                    request = frame.f_locals.get("request")
                    ordinal = getattr(request, "payload", {}).get(
                        "output_attempt", frame.f_back.f_locals.get("attempt", 1)
                    )
                    kind = (
                        "initial"
                        if prior is None
                        else "caller_retry"
                        if prior["output_ordinal"] == ordinal
                        else "repair"
                    )
                    if prior:
                        errors = self.errors(frame.f_back)[prior["error_offset"] :]
                        prior["validation_codes"] = codes(errors)
                        prior["error_fingerprints"] = [sha(e) for e in errors]
                        prior["validation_outcome"] = (
                            "transport_failed" if kind == "caller_retry" else "rejected"
                        )
                    attempt = {
                        "index": len(op["attempts"]),
                        "kind": kind,
                        "output_ordinal": ordinal,
                        "error_fingerprints": [],
                        "stage": getattr(request, "prompt_name", op["stage"]),
                        "logical_call_id": None,
                        "json_outcome": "started",
                        "validation_outcome": "unknown",
                        "validation_codes": [],
                        "request_hash": sha(serial(request)),
                        "exception_type": None,
                        "error_offset": len(self.errors(frame.f_back)),
                    }
                    op["attempts"].append(attempt)
                    self.calls[id(frame)] = (op, attempt)
                    return self.trace
                if module.startswith("yield_rca_core.") and key in BOUNDARIES:
                    return self.trace
                return None
            if event == "exception":
                if id(frame) in self.calls:
                    self.calls[id(frame)][1]["exception_type"] = arg[0].__name__
                return self.trace
            if event != "return":
                return self.trace
            if id(frame) in self.calls:
                op, attempt = self.calls.pop(id(frame))
                if arg is not None and hasattr(arg, "data"):
                    attempt["json_outcome"] = "decoded"
                    attempt["logical_call_id"] = getattr(arg.usage, "call_id", None)
                    attempt["response_hash"] = sha(serial(arg.data))
                else:
                    attempt["json_outcome"] = "no_decoded_response"
                if op["stage"] == "candidate":
                    data = arg.data if arg is not None and hasattr(arg, "data") else {}
                    self.append(
                        self.snapshots,
                        {
                            "stage": "initial_raw_candidate",
                            "operation_id": op["operation_id"],
                            "attempt_index": attempt["index"],
                            "attempt_kind": attempt["kind"],
                            "json_outcome": attempt["json_outcome"],
                            "candidates": candidates(
                                data.get("candidates", []),
                                data.get("candidate_semantic_profiles", []),
                                raw=True,
                            ),
                        },
                    )
                return None
            if id(frame) in self.active:
                op = self.active.pop(id(frame))
                errors = self.errors(frame)
                op["error_codes"] = codes(errors)
                invalid = (
                    arg is None
                    or getattr(arg, "candidate_output_invalid", False)
                    or getattr(arg, "output_invalid", False)
                )
                fallback = bool(isinstance(arg, tuple) and len(arg) == 3 and arg[2] is True)
                op["outcome"] = "fallback" if fallback else "rejected" if invalid else "accepted"
                if op["attempts"]:
                    last = op["attempts"][-1]
                    last["validation_outcome"] = "rejected" if invalid or fallback else "accepted"
                    # Historical errors on a successful repair do not mean the repair failed.
                    last["validation_codes"] = (
                        codes(errors[last["error_offset"] :]) if invalid or fallback else []
                    )
                    last["error_fingerprints"] = (
                        [sha(e) for e in errors[last["error_offset"] :]]
                        if invalid or fallback
                        else []
                    )
                if op["stage"] == "candidate" and arg is not None:
                    self.candidate_bindings[sha(serial(getattr(arg, "candidates", [])))] = (
                        op["operation_id"],
                        len(op["attempts"]) - 1,
                    )
                    self.append(
                        self.snapshots,
                        {
                            "stage": "candidate_generation_output",
                            "operation_id": op["operation_id"],
                            "attempt_index": len(op["attempts"]) - 1,
                            "candidates": candidates(
                                getattr(arg, "candidates", []),
                                getattr(arg, "candidate_semantic_profiles", []),
                            ),
                            "lineage": serial(getattr(arg, "candidate_lineage", [])),
                            "competition_status": getattr(arg, "competition_status", None),
                        },
                    )
                    self.append(
                        self.diagnostics,
                        {
                            "operation_id": op["operation_id"],
                            "stage": "candidate",
                            "codes": op["error_codes"],
                            "rejected_candidates": serial(getattr(arg, "rejected_candidates", [])),
                            "closure": serial(
                                getattr(arg, "candidate_evidence_closure_history", [])
                            ),
                        },
                    )
                return None
            if key == ("hypothesis_engine", "analyze") and isinstance(arg, dict):
                # Actual Engine input after competition and output after ranking,
                # not final State inference.
                binding = self.candidate_bindings.get(
                    sha(serial(frame.f_locals.get("external_candidates", []))), (None, None)
                )
                profiles = serial(frame.f_locals.get("candidate_semantic_profiles", []))
                inputs = serial(frame.f_locals.get("external_candidates", []))
                profile_by_root = {
                    str(item.get("root_cause", "")): profile
                    for item, profile in zip(inputs or [], profiles, strict=False)
                }
                for stage, items in [
                    ("competition_survival", frame.f_locals.get("external_candidates", [])),
                    ("ranking_survival", arg.get("candidates", [])),
                ]:
                    enriched = [
                        {
                            **item,
                            "semantic_profile": profile_by_root.get(
                                str(item.get("root_cause", ""))
                            ),
                        }
                        for item in serial(items or [])
                    ]
                    self.append(
                        self.snapshots,
                        {
                            "stage": stage,
                            "operation_id": binding[0],
                            "attempt_index": binding[1],
                            "candidates": candidates(enriched),
                        },
                    )
                self.append(
                    self.snapshots,
                    {
                        "stage": "confirmation_status",
                        "operation_id": binding[0],
                        "attempt_index": binding[1],
                        "decision_gate": serial(arg.get("decision_gate")),
                        "selected_candidate_id": (frame.f_locals.get("selected") or {}).get(
                            "hypothesis_id"
                        ),
                        "candidates": candidates(
                            [
                                {
                                    **item,
                                    "semantic_profile": profile_by_root.get(
                                        str(item.get("root_cause", ""))
                                    ),
                                }
                                for item in arg.get("candidates", [])
                            ]
                        ),
                    },
                )
            if key == ("workflow", "run") and arg is not None:
                state = serial(arg)
                self.append(
                    self.snapshots,
                    {
                        "stage": "finalizer_output",
                        "authoritative_rca_result": state.get("authoritative_rca_result"),
                        "authoritative_hypothesis_id": state.get("authoritative_hypothesis_id"),
                        "job_status": (state.get("job") or {}).get("status"),
                        "fallback_ledger": fallback_facts(state),
                    },
                )
        except Exception:
            self.dropped_count += 1  # Observer must never alter business outcome.
        return self.trace

    def to_dict(self):
        raw = {
            "operations": self.operations,
            "snapshots": self.snapshots,
            "diagnostics": self.diagnostics,
        }

        def truncated(value):
            if isinstance(value, str):
                return int(len(value) > 6000)
            if isinstance(value, dict):
                return sum(truncated(v) for v in value.values())
            if isinstance(value, list):
                return sum(truncated(v) for v in value)
            return 0

        truncation_count = truncated(raw)
        bounded = clean(raw)
        overflow = len(json.dumps(bounded).encode()) > self.max_bytes
        if overflow:
            bounded = {k: [] for k in raw}
        return {
            **bounded,
            "schema_version": "benchmark-observer/1",
            "complete": self.dropped_count == 0 and not truncation_count and not overflow,
            "truncation_count": truncation_count,
            "dropped_count": self.dropped_count + int(overflow),
        }


@contextmanager
def capture_benchmark(**kwargs):
    capture = Capture(**kwargs)
    old = sys.gettrace()
    if old is not None:
        raise RuntimeError("Benchmark capture requires a dedicated worker without another tracer")
    sys.settrace(capture.trace)
    try:
        yield capture
    finally:
        sys.settrace(old)


_GT_GUARD_DEPTH = 0
_GT_GUARD_LOCK = Lock()
_GT_HOOK_INSTALLED = False


def install_gt_read_guard():
    """Install only in disposable execution process, never in evaluator.

    Python audit boundary (not an OS sandbox). Also forbids child-process escape.
    """
    global _GT_HOOK_INSTALLED
    if _GT_HOOK_INSTALLED:
        return

    def guard(event, args):
        if not _GT_GUARD_DEPTH:
            return
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            raise PermissionError("Benchmark execution cannot launch subprocesses")
        if event == "open" and isinstance(args[0], (str, bytes, Path)):
            path = (
                str(Path(args[0].decode() if isinstance(args[0], bytes) else args[0]).resolve())
                .replace("\\", "/")
                .casefold()
            )
            if any(
                x in path
                for x in (
                    "benchmark_phase0_scoring",
                    "benchmark_scoring_v1",
                    "ground_truth",
                    "external_score",
                    "/private/",
                )
            ):
                raise PermissionError("Scoring-only data is unavailable to execution")

    sys.addaudithook(guard)
    _GT_HOOK_INSTALLED = True


@contextmanager
def runtime_read_guard():
    install_gt_read_guard()
    global _GT_GUARD_DEPTH
    with _GT_GUARD_LOCK:
        _GT_GUARD_DEPTH += 1
    try:
        yield
    finally:
        with _GT_GUARD_LOCK:
            _GT_GUARD_DEPTH -= 1


def repair_metrics(capture, *, per_stage=True):
    operations = [o for o in capture["operations"] if o["attempts"]]
    repaired = [o for o in operations if any(a["kind"] == "repair" for a in o["attempts"])]
    pairs = [
        (a, b)
        for o in operations
        for a, b in zip(o["attempts"], o["attempts"][1:], strict=False)
        if b["kind"] == "repair"
    ]

    def ratio(n, d):
        return {"numerator": n, "denominator": d, "value": n / d if d else None}

    return {
        "by_stage": {
            stage: repair_metrics(
                {**capture, "operations": [o for o in operations if o["stage"] == stage]},
                per_stage=False,
            )
            for stage in sorted({o["stage"] for o in operations})
        }
        if per_stage
        else {},
        "first_pass_json_success_rate": ratio(
            sum(o["attempts"][0]["json_outcome"] == "decoded" for o in operations), len(operations)
        ),
        "first_pass_success_rate": ratio(
            sum(o["attempts"][0]["validation_outcome"] == "accepted" for o in operations),
            len(operations),
        ),
        "repair_rate": ratio(len(repaired), len(operations)),
        "repair_success_rate": ratio(
            sum(o["outcome"] == "accepted" for o in repaired), len(repaired)
        ),
        "repeated_same_error_rate": ratio(
            sum(
                bool(
                    (
                        (set(a["validation_codes"]) & set(b["validation_codes"]))
                        - {"other_validation_failure"}
                    )
                    or (set(a.get("error_fingerprints", [])) & set(b.get("error_fingerprints", [])))
                )
                for a, b in pairs
            ),
            len(pairs),
        ),
        "caller_retries": sum(
            a["kind"] == "caller_retry" for o in operations for a in o["attempts"]
        ),
        "output_repairs": sum(a["kind"] == "repair" for o in operations for a in o["attempts"]),
        "coverage_complete": capture["complete"],
    }
