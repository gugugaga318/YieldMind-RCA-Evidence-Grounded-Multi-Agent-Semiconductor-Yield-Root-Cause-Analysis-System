"""Framework-free contracts for deterministic SPC analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self

from yield_rca_core.models import SCHEMA_VERSION, ModelValidationError


class SpcChartType(StrEnum):
    I_MR = "I_MR"
    XBAR_S = "XBAR_S"
    XBAR_R = "XBAR_R"
    P = "P"


@dataclass(frozen=True)
class SpcSample:
    sample_id: str
    lot_id: str
    timestamp: str
    value: float
    wafer_id: str | None = None
    subgroup_id: str | None = None
    sample_size: int | None = None
    defect_count: int | None = None

    def __post_init__(self) -> None:
        if not self.sample_id or not self.lot_id or not self.timestamp:
            raise ModelValidationError("SPC sample identity fields must not be empty")
        if not isinstance(self.value, int | float):
            raise ModelValidationError("SPC sample value must be numeric")
        if self.sample_size is not None and self.sample_size <= 0:
            raise ModelValidationError("SPC sample_size must be positive")
        if self.defect_count is not None:
            if self.sample_size is None:
                raise ModelValidationError("defect_count requires sample_size")
            if not 0 <= self.defect_count <= self.sample_size:
                raise ModelValidationError("defect_count must be within sample_size")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "lot_id": self.lot_id,
            "wafer_id": self.wafer_id,
            "subgroup_id": self.subgroup_id,
            "timestamp": self.timestamp,
            "value": float(self.value),
            "sample_size": self.sample_size,
            "defect_count": self.defect_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            sample_id=data["sample_id"],
            lot_id=data["lot_id"],
            wafer_id=data.get("wafer_id"),
            subgroup_id=data.get("subgroup_id"),
            timestamp=data["timestamp"],
            value=float(data["value"]),
            sample_size=(int(data["sample_size"]) if data.get("sample_size") else None),
            defect_count=(
                int(data["defect_count"]) if data.get("defect_count") is not None else None
            ),
        )


@dataclass(frozen=True)
class SpcRuleViolation:
    rule_code: str
    description: str
    direction: str
    sample_ids: list[str]
    lot_ids: list[str]
    wafer_ids: list[str]
    start_timestamp: str
    end_timestamp: str
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.rule_code.startswith("NELSON_"):
            raise ModelValidationError("SPC rule_code must use the NELSON namespace")
        if not self.sample_ids or not self.lot_ids or not self.evidence_id:
            raise ModelValidationError("SPC violation must identify samples, Lots, and evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "description": self.description,
            "direction": self.direction,
            "sample_ids": list(self.sample_ids),
            "lot_ids": list(self.lot_ids),
            "wafer_ids": list(self.wafer_ids),
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            rule_code=str(data["rule_code"]),
            description=str(data["description"]),
            direction=str(data["direction"]),
            sample_ids=[str(item) for item in data["sample_ids"]],
            lot_ids=[str(item) for item in data["lot_ids"]],
            wafer_ids=[str(item) for item in data.get("wafer_ids", [])],
            start_timestamp=str(data["start_timestamp"]),
            end_timestamp=str(data["end_timestamp"]),
            evidence_id=str(data["evidence_id"]),
        )


@dataclass(frozen=True)
class SpcCapabilityResult:
    cp: float | None
    cpk: float | None
    pp: float | None
    ppk: float | None
    spec_lower: float | None
    spec_upper: float | None
    valid_for_decision: bool
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cp": self.cp,
            "cpk": self.cpk,
            "pp": self.pp,
            "ppk": self.ppk,
            "spec_lower": self.spec_lower,
            "spec_upper": self.spec_upper,
            "valid_for_decision": self.valid_for_decision,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        def optional_float(key: str) -> float | None:
            value = data.get(key)
            return float(value) if value is not None else None

        return cls(
            cp=optional_float("cp"),
            cpk=optional_float("cpk"),
            pp=optional_float("pp"),
            ppk=optional_float("ppk"),
            spec_lower=optional_float("spec_lower"),
            spec_upper=optional_float("spec_upper"),
            valid_for_decision=bool(data["valid_for_decision"]),
            warning=(str(data["warning"]) if data.get("warning") else None),
        )


@dataclass(frozen=True)
class SpcChartResult:
    chart_type: str
    parameter_name: str
    unit: str
    center_line: float
    lower_control_limit: float
    upper_control_limit: float
    sigma: float
    baseline_sample_count: int
    analysis_sample_count: int
    series: list[dict[str, Any]]
    violations: list[SpcRuleViolation]
    capability: SpcCapabilityResult | None = None
    secondary_chart: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            SpcChartType(self.chart_type)
        except ValueError as exc:
            raise ModelValidationError("unsupported SPC chart type") from exc
        if not self.parameter_name or self.baseline_sample_count < 2:
            raise ModelValidationError("SPC result requires a parameter and baseline")
        if self.analysis_sample_count <= 0 or not self.series:
            raise ModelValidationError("SPC result requires analysis samples")
        if self.lower_control_limit > self.upper_control_limit:
            raise ModelValidationError("SPC lower control limit exceeds upper limit")

    @property
    def status(self) -> str:
        return "OOC" if self.violations else "IN_CONTROL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "parameter_name": self.parameter_name,
            "unit": self.unit,
            "status": self.status,
            "center_line": self.center_line,
            "lower_control_limit": self.lower_control_limit,
            "upper_control_limit": self.upper_control_limit,
            "sigma": self.sigma,
            "baseline_sample_count": self.baseline_sample_count,
            "analysis_sample_count": self.analysis_sample_count,
            "series": list(self.series),
            "violations": [item.to_dict() for item in self.violations],
            "violated_rules": list(dict.fromkeys(item.rule_code for item in self.violations)),
            "point_violation_count": len(
                {sample_id for violation in self.violations for sample_id in violation.sample_ids}
            ),
            "capability": self.capability.to_dict() if self.capability else None,
            "secondary_chart": dict(self.secondary_chart),
            "warnings": list(self.warnings),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        capability = data.get("capability")
        return cls(
            chart_type=str(data["chart_type"]),
            parameter_name=str(data["parameter_name"]),
            unit=str(data["unit"]),
            center_line=float(data["center_line"]),
            lower_control_limit=float(data["lower_control_limit"]),
            upper_control_limit=float(data["upper_control_limit"]),
            sigma=float(data["sigma"]),
            baseline_sample_count=int(data["baseline_sample_count"]),
            analysis_sample_count=int(data["analysis_sample_count"]),
            series=[dict(item) for item in data["series"]],
            violations=[SpcRuleViolation.from_dict(item) for item in data.get("violations", [])],
            capability=(
                SpcCapabilityResult.from_dict(capability) if isinstance(capability, dict) else None
            ),
            secondary_chart=dict(data.get("secondary_chart", {})),
            warnings=[str(item) for item in data.get("warnings", [])],
            schema_version=str(data.get("schema_version", SCHEMA_VERSION)),
        )
