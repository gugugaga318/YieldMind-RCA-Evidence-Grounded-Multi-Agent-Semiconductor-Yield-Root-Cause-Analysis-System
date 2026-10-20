"""Project existing Runtime events into the Batch 0 benchmark call contract.

Offline consumer only. Missing caller retry/repair/business facts stay unknown.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from model_benchmark_contract import CallRecord, HTTPAttempt, ValidationObservation


def runtime_call_records(
    telemetry: dict[str, Any],
    *,
    source_ref: str = "execution_metadata.model_telemetry",
) -> list[CallRecord]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in telemetry.get("events", []):
        grouped[event["logical_call_id"]].append(event)
    complete = telemetry.get("complete") is True and telemetry.get("dropped_count") == 0
    records = []
    for call_id, events in grouped.items():
        logical = [e for e in events if e["kind"] == "logical_call"]
        results = [e for e in events if e["kind"] == "model_result"]
        if len(logical) != 1 or len(results) > 1:
            raise ValueError("call identity requires one logical event and at most one result")
        identity = logical[0]
        if any(
            (e["provider"], e["model"]) != (identity["provider"], identity["model"]) for e in events
        ):
            raise ValueError("inconsistent Runtime identity within one call")
        result = results[0] if results else {}
        attempts = [e for e in events if e["kind"] == "http_attempt"]
        usage = result.get("usage") or {}
        reported = usage.get("reported_fields", [])
        outcome = result.get("outcome")
        validation = ValidationObservation(
            stage="json_decode",
            outcome=(
                "passed"
                if outcome == "success"
                else "failed"
                if outcome == "invalid_json"
                else "not_observed"
            ),
            attempted_items=1 if outcome in {"success", "invalid_json"} else None,
            failed_items=(
                int(outcome == "invalid_json") if outcome in {"success", "invalid_json"} else None
            ),
            reason_codes=["invalid_json"] if outcome == "invalid_json" else [],
            source_ref=source_ref if outcome in {"success", "invalid_json"} else None,
        )
        records.append(
            CallRecord(
                logical_call_id=call_id,
                usage_call_id=call_id,
                agent=identity.get("agent") or "unknown",
                prompt_name=identity.get("stage") or "unknown",
                prompt_version=identity.get("prompt_version") or "unknown",
                provider=identity["provider"],
                model=identity["model"],
                operation_id=None,
                caller_retry_index=None,
                output_attempt_index=None,
                http_attempt_count=len(attempts) if complete else None,
                http_attempts=[
                    HTTPAttempt(
                        attempt=e["http_attempt"],
                        status_code=e.get("status_code"),
                        outcome=e["outcome"],
                        normalized_category=e.get("error_category"),
                        request_id=None,
                        latency_ms=e.get("latency_ms"),
                        source_ref=source_ref,
                    )
                    for e in attempts
                ],
                http_attempts_complete=complete,
                transport_outcome=(
                    "success"
                    if outcome in {"success", "invalid_json"}
                    else "failed"
                    if attempts and attempts[-1]["outcome"] != "success"
                    else "not_observed"
                ),
                failure_category=result.get("error_category"),
                stop_reason=result.get("stop_reason"),
                input_tokens=usage.get("prompt_tokens") if "prompt_tokens" in reported else None,
                output_tokens=usage.get("completion_tokens")
                if "completion_tokens" in reported
                else None,
                total_tokens=usage.get("total_tokens") if "total_tokens" in reported else None,
                latency_ms=result.get("latency_ms"),
                cost=None,
                validation=[validation],
                source_refs=[source_ref],
            )
        )
    return records
