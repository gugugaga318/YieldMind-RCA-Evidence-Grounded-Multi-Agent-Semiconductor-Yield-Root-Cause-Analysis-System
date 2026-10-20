"""Acquisition facts, separate from causal support and source availability.

Only tool rows create receipts. Neither specialist prose nor lane lifecycle is
an observation. Legacy Evidence without receipts remains readable.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from yield_rca_core.evidence_models import ModelValidationError

if TYPE_CHECKING:
    from yield_rca_core.evidence_models import Evidence

TABLES = ("defect_summary", "wat_result", "metrology_result")
STATUSES = frozenset(
    {
        "not_acquired",
        "not_observed",
        "unavailable",
        "observed_normal",
        "observed_abnormal",
        "observed_unclassified",
    }
)


@dataclass(frozen=True)
class OutcomeObservation:
    source_table: str
    lot_id: str
    status: str
    record_count: int = 0
    normal_record_count: int = 0
    abnormal_record_count: int = 0
    time_start: str | None = None
    time_end: str | None = None

    def __post_init__(self) -> None:
        if self.source_table not in TABLES or not self.lot_id or self.status not in STATUSES:
            raise ModelValidationError("invalid outcome observation identity/status")
        counts = (self.record_count, self.normal_record_count, self.abnormal_record_count)
        if any(type(n) is not int or n < 0 for n in counts):
            raise ModelValidationError("observation counts must be non-negative integers")
        if self.normal_record_count + self.abnormal_record_count > self.record_count:
            raise ModelValidationError("classified observation counts exceed records")
        if self.status in {"not_acquired", "not_observed", "unavailable"}:
            if any(counts):
                raise ModelValidationError("unobserved status cannot carry observed records")
        elif not self.record_count:
            raise ModelValidationError("observed status requires records")
        if self.status == "observed_normal" and self.normal_record_count != self.record_count:
            raise ModelValidationError("observed normal requires explicit normal records")
        if self.status == "observed_abnormal" and not self.abnormal_record_count:
            raise ModelValidationError("observed abnormal requires abnormal records")
        if self.status == "observed_unclassified" and (
            self.abnormal_record_count or self.normal_record_count == self.record_count
        ):
            raise ModelValidationError("unclassified status inconsistent with counts")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OutcomeObservation:
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise ModelValidationError(f"invalid outcome observation: {exc}") from exc


def quality_receipt(
    lot_ids: Sequence[str], tables: Mapping[str, Sequence[Mapping[str, str]]], request_id: str
) -> dict[str, Any]:
    # The query is a set of Lots even when a caller repeats an ID.
    lot_ids = sorted(set(lot_ids))
    observations = []
    for table in TABLES:
        for lot_id in lot_ids:
            rows = [r for r in tables[table] if r["lot_id"] == lot_id]
            if table == "defect_summary":
                abnormal = sum(float(r.get("defect_count") or 0) > 0 for r in rows)
                normal = sum(
                    r.get("defect_count") not in (None, "") and float(r["defect_count"]) == 0
                    for r in rows
                )
                time_key = "inspected_at"
            else:
                abnormal = sum(r.get("pass_fail") == "false" for r in rows)
                normal = sum(r.get("pass_fail") == "true" for r in rows)
                time_key = "tested_at" if table == "wat_result" else "measured_at"
            status = (
                "not_observed"
                if not rows
                else "observed_abnormal"
                if abnormal
                else "observed_normal"
                if normal == len(rows)
                else "observed_unclassified"
            )
            times = sorted(r[time_key] for r in rows if r.get(time_key))
            observations.append(
                OutcomeObservation(
                    table,
                    lot_id,
                    status,
                    len(rows),
                    normal,
                    abnormal,
                    times[0] if times else None,
                    times[-1] if times else None,
                ).to_dict()
            )
    return dict(
        schema_version="outcome-observation/1.0",
        request_id=request_id,
        source_available=True,
        queried_lot_ids=list(lot_ids),
        time_filter_applied=False,
        observations=observations,
    )


def validate_receipt(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "outcome-observation/1.0":
        raise ModelValidationError("unsupported outcome observation receipt")
    if not value.get("request_id") or type(value.get("source_available")) is not bool:
        raise ModelValidationError("receipt requires request/source provenance")
    lots = value.get("queried_lot_ids", ())
    if not isinstance(lots, (list, tuple)) or not lots or len(set(lots)) != len(lots):
        raise ModelValidationError("receipt requires unique queried lots")
    observations = value.get("observations", ())
    keys = []
    for raw in observations:
        item = OutcomeObservation.from_dict(raw)
        keys.append((item.source_table, item.lot_id))
        if item.status == "not_acquired":
            raise ModelValidationError("query receipt cannot claim an unqueried observation")
        if (item.status == "unavailable") == value["source_available"]:
            raise ModelValidationError("observation and source availability disagree")
    expected = {(table, lot) for table in TABLES for lot in lots}
    if len(keys) != len(expected) or set(keys) != expected:
        raise ModelValidationError("receipt must cover every queried lot/domain exactly once")


def spatial_observations(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    fields = (
        "defect_type",
        "pattern_type",
        "location_region",
        "lot_id",
        "wafer_id",
        "inspection_operation_no",
        "inspected_at",
        "defect_count",
        "defect_density",
    )
    return [
        dict(
            source_table="defect_summary",
            source_record_hash=hashlib.sha256(
                json.dumps(dict(row), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            **{key: row.get(key) for key in fields},
        )
        for row in rows
    ]


def lane_outcome_coverage(
    evidence: Sequence[Evidence], lot_ids: Sequence[str], *, limit: int = 4
) -> dict[str, Any]:
    lots = set(lot_ids)
    receipts: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    legacy = False
    for item in evidence:
        receipt = item.metadata.get("outcome_observation_receipt")
        if isinstance(receipt, Mapping):
            key = (
                receipt["request_id"],
                tuple(receipt["queried_lot_ids"]),
                tuple(tuple(sorted(o.items())) for o in receipt["observations"]),
            )
            receipts.setdefault(key, receipt)
        elif item.source_tool == "summarize_defect_wat":
            legacy = True
    observations = []
    acquired = set()
    attempted = set()
    for receipt in receipts.values():
        request_id = receipt["request_id"]
        for item in receipt["observations"]:
            if item["lot_id"] in lots:
                observations.append({"request_id": request_id, **dict(item)})
                attempted.add(item["lot_id"])
                if receipt["source_available"]:
                    acquired.add(item["lot_id"])
    missing = sorted(lots - acquired)
    status = (
        "unknown"
        if not lots
        else "acquired"
        if not missing
        else "unknown"
        if legacy
        else "partial"
        if acquired
        else "unavailable"
        if attempted == lots
        else "partial"
        if attempted
        else "not_acquired"
    )
    return dict(
        status=status,
        comparison_lot_count=len(lots),
        acquired_lot_count=len(acquired),
        not_acquired_lot_ids=sorted(lots - attempted)[:limit] if not legacy else [],
        unknown_lot_ids=missing[:limit] if legacy else [],
        missing_lot_count=len(missing),
        observations=observations[:limit],
        observation_count=len(observations),
        observations_omitted=max(0, len(observations) - limit),
        status_counts_by_source=_status_counts(observations),
        interpretation="Acquisition coverage only; no causal, recipe or time alignment inferred.",
    )


def _status_counts(observations: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        table: dict(Counter(o["status"] for o in observations if o["source_table"] == table))
        for table in TABLES
    }


def compact_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Full per-lot receipts stay in State; projection never implies all-normal."""
    return dict(
        schema_version=receipt["schema_version"],
        request_id=receipt["request_id"],
        source_available=receipt["source_available"],
        queried_lot_ids=list(receipt["queried_lot_ids"][:4]),
        queried_lot_count=len(receipt["queried_lot_ids"]),
        time_filter_applied=receipt["time_filter_applied"],
        status_counts_by_source=_status_counts(receipt["observations"]),
        observation_count=len(receipt["observations"]),
        full_observations="State.Evidence.metadata.outcome_observation_receipt",
    )
