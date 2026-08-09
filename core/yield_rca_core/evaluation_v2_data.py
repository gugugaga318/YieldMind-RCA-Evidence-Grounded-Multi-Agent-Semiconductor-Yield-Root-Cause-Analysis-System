"""Independent Synthetic V2 data generation and data-quality validation.

The hidden incident catalog is the only causal source of truth.  Document
payloads may see one historical incident, while query wording providers receive
only a redacted observation projection.  Python owns qrels, partitions, causal
truth, and review checkpoints.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from yield_rca_core.llm_gateway import LLMClient, LLMRequest
from yield_rca_core.models import AgentKind

SCHEMA_VERSION = "2.0"
DATASET_ID = "synthetic-semiconductor-causal-v2"
QUERY_PROMPT_NAME = "synthetic_v2_query_wording"
QUERY_PROMPT_VERSION = "v1"
REVIEW_PENDING = "PENDING"
REVIEW_ACCEPTED = "ACCEPTED"


class EvaluationV2DataError(ValueError):
    """Raised when V2 data violates an isolation or quality contract."""


@dataclass(frozen=True)
class GeneratedSurfaceQuery:
    """Surface text returned by a deterministic or paid wording provider."""

    query_key: str
    text: str


class SurfaceQueryProvider(Protocol):
    """Bounded writer that only receives redacted Query Writer payloads."""

    provider_name: str
    model_name: str
    paid_call_count: int

    def generate(self, payloads: list[dict[str, Any]]) -> list[GeneratedSurfaceQuery]: ...


class TemplateSurfaceQueryProvider:
    """Byte-stable no-cost Query Writer used for committed Synthetic V2 data."""

    provider_name = "deterministic_observation_template"
    model_name = "none"
    paid_call_count = 0

    def generate(self, payloads: list[dict[str, Any]]) -> list[GeneratedSurfaceQuery]:
        generated: list[GeneratedSurfaceQuery] = []
        for payload in payloads:
            observation = payload["observation"]
            lot_id = observation.get("source_lot_id", "")
            stage = observation["detected_operation"]
            symptoms = ", ".join(observation["symptom_types"])
            measurement = observation["known_measurements"][0]
            signal = (
                f"{measurement['metric_name']} was {measurement['observed']} {measurement['unit']}"
            )
            metadata_note = ""
            if payload.get("metadata_quality") == "missing":
                metadata_note = " Equipment identity is not available."
            elif payload.get("metadata_quality") == "noisy":
                metadata_note = " The operator note may contain an incorrect module hint."

            task = payload["requested_task"]
            if task == "historical_match":
                text = (
                    f"Lot {lot_id} first showed {symptoms} after {stage}; {signal}. "
                    "Find a reviewed historical case with a comparable evidence pattern."
                    f"{metadata_note}"
                )
            elif task == "procedure_guidance":
                text = (
                    f"During review of {lot_id}, engineers observed {symptoms} after {stage}; "
                    f"{signal}. Which approved containment and verification procedure applies?"
                    f"{metadata_note}"
                )
            elif task == "engineering_note_lookup":
                text = (
                    f"An investigation at {stage} reported {symptoms}, with {signal}. "
                    "Retrieve an engineering note that explains how to separate detection "
                    f"location from causal attribution.{metadata_note}"
                )
            elif task == "full_rca":
                text = (
                    f"Investigate Lot {lot_id}: {symptoms} appeared after {stage}, and {signal}. "
                    "Identify the root cause and impact Lots; treat the observed stage as a hint, "
                    f"not a confirmed cause.{metadata_note}"
                )
            else:
                raise EvaluationV2DataError(f"unsupported requested task: {task}")
            generated.append(GeneratedSurfaceQuery(payload["query_key"], text))
        return generated


class QwenSurfaceQueryProvider:
    """Explicit opt-in Qwen wording provider with a visible paid-call ceiling."""

    provider_name = "qwen_surface_wording"

    def __init__(
        self,
        client: LLMClient,
        *,
        batch_size: int = 8,
        max_paid_calls: int = 8,
    ) -> None:
        if batch_size <= 0 or max_paid_calls <= 0:
            raise EvaluationV2DataError("batch size and paid-call cap must be positive")
        self.client = client
        self.model_name = client.model
        self.batch_size = batch_size
        self.max_paid_calls = max_paid_calls
        self.paid_call_count = 0

    def generate(self, payloads: list[dict[str, Any]]) -> list[GeneratedSurfaceQuery]:
        generated: list[GeneratedSurfaceQuery] = []
        for offset in range(0, len(payloads), self.batch_size):
            if self.paid_call_count >= self.max_paid_calls:
                raise EvaluationV2DataError("V2 wording exceeded paid LLM-call cap")
            batch = payloads[offset : offset + self.batch_size]
            response = self.client.complete_json(
                LLMRequest(
                    agent=AgentKind.KNOWLEDGE.value,
                    prompt_name=QUERY_PROMPT_NAME,
                    prompt_version=QUERY_PROMPT_VERSION,
                    payload={"queries": batch},
                    temperature=0.2,
                )
            )
            self.paid_call_count += 1
            raw_queries = response.data.get("queries")
            if not isinstance(raw_queries, list):
                raise EvaluationV2DataError("Qwen wording output must contain queries[]")
            for raw in raw_queries:
                if not isinstance(raw, dict):
                    raise EvaluationV2DataError("Qwen wording item must be an object")
                generated.append(
                    GeneratedSurfaceQuery(
                        query_key=str(raw.get("query_key", "")).strip(),
                        text=str(raw.get("text", "")).strip(),
                    )
                )
        return generated


_OPERATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("1000", "Coat and expose", "Lithography", "Scanner"),
    ("2000", "Pattern transfer", "Dry Etch", "Plasma Etcher"),
    ("3000", "Dose implantation", "Ion Implant", "Implanter"),
    ("4000", "Barrier deposition", "PVD", "PVD Cluster"),
    ("5000", "Dielectric deposition", "CVD", "CVD Reactor"),
    ("6000", "Copper electroplating", "Electroplating", "Plating Tool"),
    ("6400", "Copper planarization", "Cu CMP", "CMP Polisher"),
    ("7000", "Post-process clean", "Wet Clean", "Wet Bench"),
    ("8000", "Inline measurement", "Metrology", "Metrology Tool"),
    ("9000", "Electrical parametric test", "WAT", "Parametric Tester"),
)

_OPERATION_BY_MODULE = {module: operation for operation, _, module, _ in _OPERATIONS}
_OPERATION_NAME = {operation: name for operation, name, _, _ in _OPERATIONS}
_EQUIPMENT_TYPE = {module: equipment_type for _, _, module, equipment_type in _OPERATIONS}


def _blueprints() -> list[dict[str, Any]]:
    """Return concise reviewed facts expanded into the hidden Incident schema."""

    return [
        {
            "id": "IF_V2_001",
            "partition": "calibration",
            "category": "same_step",
            "lane": "same_step",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Cu CMP",
            "detected_equipment": "CMP_CU_11",
            "causal_equipment": "CMP_CU_11",
            "cluster": "surface_geometry",
            "symptoms": ["directional micro-scratch bands", "localized copper residue"],
            "metric": ("scratch density", 0.82, "count/mm2"),
            "cause_signal": "conditioner_motor_torque",
            "root_cause": "CMP_CU_11 pad conditioner bearing drag caused intermittent pad glazing",
            "actions": ["replace the conditioner bearing", "qualify pad break-in stability"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
        },
        {
            "id": "IF_V2_002",
            "partition": "calibration",
            "category": "upstream",
            "lane": "upstream_route",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Electroplating",
            "detected_equipment": "CMP_CU_12",
            "causal_equipment": "PLATE_CU_04",
            "cluster": "surface_geometry",
            "symptoms": ["edge dishing asymmetry", "post-polish thickness crescent"],
            "metric": ("edge-center thickness delta", 41.0, "nm"),
            "cause_signal": "anode_contact_voltage",
            "root_cause": (
                "PLATE_CU_04 anode contact intermittency created an upstream copper profile skew"
            ),
            "actions": ["renew the anode contact set", "verify pre-CMP copper map uniformity"],
            "impact_count": 3,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "complete",
        },
        {
            "id": "IF_V2_003",
            "partition": "calibration",
            "category": "shared_resource",
            "lane": "shared_resource",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Wet Clean",
            "detected_equipment": "CMP_CU_13",
            "causal_equipment": "WET_BACK_07",
            "cluster": "surface_geometry",
            "symptoms": ["parallel edge scratches", "transfer-path recurrence"],
            "metric": ("edge scratch count", 19.0, "count/wafer"),
            "cause_signal": "transfer_arm_vibration",
            "root_cause": (
                "WET_BACK_07 transfer-arm bearing vibration marked wafers during "
                "post-clean handling"
            ),
            "actions": [
                "repair the transfer-arm bearing",
                "inspect lots sharing WET_BACK_07 exposure",
            ],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "missing",
            "shared_resource": {
                "resource_type": "equipment",
                "resource_id": "WET_BACK_07",
            },
        },
        {
            "id": "IF_V2_004",
            "partition": "calibration",
            "category": "metrology_artifact",
            "lane": "global_semantic",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Metrology",
            "detected_equipment": "CMP_CU_14",
            "causal_equipment": "MET_FILM_03",
            "cluster": "parametric_shift",
            "symptoms": ["apparent removal-rate step", "stable electrical distribution"],
            "metric": ("reported copper thickness", 84.0, "nm"),
            "cause_signal": "reference_wafer_offset",
            "root_cause": (
                "MET_FILM_03 reference-wafer offset produced a false post-CMP thickness shift"
            ),
            "actions": ["recalibrate the optical model", "remeasure retained reference wafers"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "noisy",
        },
        {
            "id": "IF_V2_005",
            "partition": "calibration",
            "category": "conflicting_evidence",
            "lane": "same_step",
            "status": "inconclusive",
            "detected_module": "Cu CMP",
            "causal_module": "Cu CMP",
            "detected_equipment": "CMP_CU_15",
            "causal_equipment": "CMP_CU_15",
            "cluster": "surface_geometry",
            "symptoms": ["center residue islands", "normal endpoint duration"],
            "metric": ("residue area ratio", 3.8, "percent"),
            "cause_signal": "slurry_delivery_pressure",
            "root_cause": "unverified CMP slurry delivery restriction",
            "actions": [
                "collect pump pressure traces",
                "run confirmation wafers before attribution",
            ],
            "impact_count": 0,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "conflicting": True,
        },
        {
            "id": "IF_V2_006",
            "partition": "calibration",
            "category": "insufficient_evidence",
            "lane": "global_semantic",
            "status": "inconclusive",
            "detected_module": "Dry Etch",
            "causal_module": "Unresolved",
            "detected_equipment": "ETCH_METAL_08",
            "causal_equipment": "UNRESOLVED",
            "cluster": "parametric_shift",
            "symptoms": ["isolated sidewall roughness", "no chamber recurrence"],
            "metric": ("roughness index", 1.7, "a.u."),
            "cause_signal": "unresolved_signal",
            "root_cause": "UNRESOLVED because current-lot equipment evidence is missing",
            "actions": ["retain the wafer for review", "do not promote a historical match"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "missing",
            "unavailable_sources": ["FDC feature history"],
        },
        {
            "id": "IF_V2_007",
            "partition": "calibration",
            "category": "same_step",
            "lane": "same_step",
            "status": "supported",
            "detected_module": "Dry Etch",
            "causal_module": "Dry Etch",
            "detected_equipment": "ETCH_VIA_09",
            "causal_equipment": "ETCH_VIA_09",
            "cluster": "parametric_shift",
            "symptoms": ["via CD widening", "wafer-edge selectivity loss"],
            "metric": ("via CD bias", 12.0, "nm"),
            "cause_signal": "chamber_wall_temperature",
            "root_cause": "ETCH_VIA_09 chamber-wall control drift changed polymer balance",
            "actions": ["repair wall-temperature control", "restore seasoning qualification"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
        },
        {
            "id": "IF_V2_008",
            "partition": "test",
            "category": "upstream",
            "lane": "upstream_route",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Electroplating",
            "detected_equipment": "CMP_CU_21",
            "causal_equipment": "PLATE_CU_09",
            "cluster": "surface_geometry",
            "symptoms": ["radial erosion growth", "incoming copper map tilt"],
            "metric": ("erosion range", 36.0, "nm"),
            "cause_signal": "electrolyte_agitation_speed",
            "root_cause": (
                "PLATE_CU_09 agitation controller drift distorted the incoming copper profile"
            ),
            "actions": ["replace the agitation encoder", "qualify incoming copper uniformity"],
            "impact_count": 3,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "noisy",
        },
        {
            "id": "IF_V2_009",
            "partition": "test",
            "category": "shared_resource",
            "lane": "shared_resource",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Wet Clean",
            "detected_equipment": "CMP_CU_22",
            "causal_equipment": "WET_BACK_12",
            "cluster": "surface_geometry",
            "symptoms": ["repeating arc scratches", "rinse-chamber recurrence"],
            "metric": ("arc defect count", 14.0, "count/wafer"),
            "cause_signal": "rinse_nozzle_particle_index",
            "root_cause": "WET_BACK_12 rinse chamber particle shedding marked wafers after clean",
            "actions": [
                "clean and qualify the rinse chamber",
                "inspect all lots sharing its exposure window",
            ],
            "impact_count": 3,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "shared_resource": {
                "resource_type": "chamber",
                "resource_id": "WET_BACK_12_CH01",
            },
        },
        {
            "id": "IF_V2_010",
            "partition": "test",
            "category": "global_cross_module",
            "lane": "global_semantic",
            "status": "supported",
            "detected_module": "WAT",
            "causal_module": "Ion Implant",
            "detected_equipment": "WAT_CELL_07",
            "causal_equipment": "IMP_WELL_03",
            "cluster": "parametric_shift",
            "symptoms": ["threshold-voltage tail shift", "normal metal resistance"],
            "metric": ("Vt p99 shift", 47.0, "mV"),
            "cause_signal": "beam_dose_integrator",
            "root_cause": "IMP_WELL_03 dose-integrator drift shifted well implant dose",
            "actions": ["recalibrate the dose integrator", "review lots since the last beam check"],
            "impact_count": 2,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "missing",
        },
        {
            "id": "IF_V2_011",
            "partition": "test",
            "category": "global_cross_module",
            "lane": "global_semantic",
            "status": "supported",
            "detected_module": "WAT",
            "causal_module": "Lithography",
            "detected_equipment": "WAT_CELL_08",
            "causal_equipment": "LITHO_SCN_06",
            "cluster": "parametric_shift",
            "symptoms": ["serpentine leakage clusters", "die-row periodicity"],
            "metric": ("leakage fail rate", 6.4, "percent"),
            "cause_signal": "reticle_scatter_index",
            "root_cause": "LITHO_SCN_06 reticle haze repeated a line-space bridging signature",
            "actions": ["clean and reinspect the reticle", "screen lots exposed after haze growth"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "noisy",
        },
        {
            "id": "IF_V2_012",
            "partition": "test",
            "category": "impact_lot_truth",
            "lane": "upstream_route",
            "status": "supported",
            "detected_module": "Metrology",
            "causal_module": "CVD",
            "detected_equipment": "MET_FILM_11",
            "causal_equipment": "CVD_ILD_17",
            "cluster": "parametric_shift",
            "symptoms": ["odd-slot film thinning", "stable downstream polish rate"],
            "metric": ("film thickness delta", -28.0, "nm"),
            "cause_signal": "susceptor_temperature_error",
            "root_cause": (
                "CVD_ILD_17 susceptor sensor intermittency reduced odd-slot deposition rate"
            ),
            "actions": [
                "replace the susceptor sensor",
                "hold lots sharing chamber and time exposure",
            ],
            "impact_count": 3,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "complete",
        },
        {
            "id": "IF_V2_013",
            "partition": "test",
            "category": "unsupported_data_source",
            "lane": "shared_resource",
            "status": "inconclusive",
            "detected_module": "Cu CMP",
            "causal_module": "Unresolved",
            "detected_equipment": "CMP_CU_23",
            "causal_equipment": "UNRESOLVED",
            "cluster": "parametric_shift",
            "symptoms": ["short-lived removal-rate dip", "normal subsequent lots"],
            "metric": ("endpoint extension", 9.0, "s"),
            "cause_signal": "chemical_batch_trace",
            "root_cause": "UNRESOLVED because chemical batch genealogy is unavailable",
            "actions": ["report the unavailable source", "avoid inferring a chemical batch"],
            "impact_count": 0,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "unavailable_sources": ["chemical batch genealogy"],
        },
        {
            "id": "IF_V2_014",
            "partition": "test",
            "category": "same_step",
            "lane": "same_step",
            "status": "supported",
            "detected_module": "WAT",
            "causal_module": "WAT",
            "detected_equipment": "WAT_CELL_12",
            "causal_equipment": "WAT_CELL_12",
            "cluster": "parametric_shift",
            "symptoms": ["contact-resistance spikes", "site-order recurrence"],
            "metric": ("contact resistance p95", 18.0, "ohm"),
            "cause_signal": "probe_contact_repeatability",
            "root_cause": "WAT_CELL_12 probe-card contamination caused false resistance spikes",
            "actions": ["clean and qualify the probe card", "retest retained wafers"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "complete",
        },
    ]


def _no_answer_blueprints() -> list[dict[str, Any]]:
    return [
        {
            "id": "IF_V2_NA_01",
            "partition": "calibration",
            "task": "procedure_guidance",
            "module": "SiC Trench Etch",
            "operation": "Cryogenic trench formation",
            "equipment": "ETCH_SIC_01",
            "symptoms": ["micro-trench corner cracking", "low-temperature sidewall haze"],
            "metric": ("corner crack density", 0.9, "count/mm2"),
        },
        {
            "id": "IF_V2_NA_02",
            "partition": "test",
            "task": "historical_match",
            "module": "Advanced Packaging",
            "operation": "Backside power reveal",
            "equipment": "BGRIND_22",
            "symptoms": ["buried rail reveal voids", "localized silicon tearing"],
            "metric": ("reveal void fraction", 2.6, "percent"),
        },
        {
            "id": "IF_V2_NA_03",
            "partition": "calibration",
            "task": "engineering_note_lookup",
            "module": "High-NA EUV",
            "operation": "Anamorphic overlay control",
            "equipment": "EUV_HNA_02",
            "symptoms": ["field-edge overlay rotation", "scan-direction asymmetry"],
            "metric": ("overlay vector magnitude", 4.8, "nm"),
        },
        {
            "id": "IF_V2_NA_04",
            "partition": "test",
            "task": "historical_match",
            "module": "Hybrid Bonding",
            "operation": "Plasma-activated bonding",
            "equipment": "BOND_HYB_09",
            "symptoms": ["interfacial nano-void ring", "post-anneal edge separation"],
            "metric": ("bond void ratio", 1.4, "percent"),
        },
    ]


def default_incident_catalog() -> dict[str, Any]:
    """Build the complete hidden Incident catalog from concise reviewed facts."""

    families: list[dict[str, Any]] = []
    for index, item in enumerate(_blueprints(), start=1):
        family_id = item["id"]
        detected_module = item["detected_module"]
        causal_module = item["causal_module"]
        detected_operation = _OPERATION_BY_MODULE[detected_module]
        causal_operation = (
            _OPERATION_BY_MODULE[causal_module]
            if causal_module in _OPERATION_BY_MODULE
            else detected_operation
        )
        metric_name, observed, unit = item["metric"]
        source_lot_id = f"LOT_V2_{index:02d}_SRC"
        supporting = [
            {
                "evidence_id": f"EV_V2_{index:02d}_MES_ROUTE",
                "evidence_type": "MES_PROCESS_HISTORY",
                "summary": "Source Lot route and equipment exposure are available.",
            },
            {
                "evidence_id": f"EV_V2_{index:02d}_OBSERVATION",
                "evidence_type": "DEFECT_OR_METROLOGY",
                "summary": "The user-visible symptom and measurement are reproducible.",
            },
        ]
        if item["status"] == "supported":
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_CAUSAL_SIGNAL",
                    "evidence_type": "FDC_OR_OPERATIONAL_CONFIRMATION",
                    "summary": "Current-Lot operational evidence supports the causal attribution.",
                }
            )
        contradicting = []
        if item.get("conflicting"):
            contradicting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_CONTRADICTION",
                    "evidence_type": "FDC_NORMAL_EXCLUSION",
                    "summary": "A second independent trace remains normal and blocks confirmation.",
                }
            )
        impact_lots = [
            f"LOT_V2_{index:02d}_IMP_{number:02d}" for number in range(1, item["impact_count"] + 1)
        ]
        guide_prefix = "SOP" if item["guidance_type"] == "SOP" else "NOTE"
        families.append(
            {
                "incident_family_id": family_id,
                "partition": item["partition"],
                "synthetic": True,
                "scenario_category": item["category"],
                "expected_status": item["status"],
                "discovery_lane": item["lane"],
                "metadata_quality": item["metadata_quality"],
                "observation_record": {
                    "source_lot_id": source_lot_id,
                    "product_id": f"PROD_V2_{index:02d}",
                    "detected_module": detected_module,
                    "detected_operation": detected_operation,
                    "detected_operation_name": _OPERATION_NAME[detected_operation],
                    "detected_equipment_id": item["detected_equipment"],
                    "detected_at": f"2026-06-{index + 1:02d}T12:00:00+00:00",
                    "symptom_types": list(item["symptoms"]),
                    "known_measurements": [
                        {
                            "metric_name": metric_name,
                            "observed": observed,
                            "unit": unit,
                        }
                    ],
                    "known_defect_attributes": {
                        "symptom_cluster": item["cluster"],
                        "pattern": item["symptoms"][0],
                    },
                },
                "causal_record": {
                    "causal_module": causal_module,
                    "causal_operation": causal_operation,
                    "causal_equipment_id": item["causal_equipment"],
                    "root_cause": item["root_cause"],
                    "corrective_actions": list(item["actions"]),
                    "cause_signal": item["cause_signal"],
                },
                "process_route": [
                    {
                        "sequence_no": sequence,
                        "operation_no": operation,
                        "operation_name": name,
                        "module": module,
                    }
                    for sequence, (operation, name, module, _) in enumerate(_OPERATIONS, start=1)
                ],
                "resource_relations": (
                    [dict(item["shared_resource"])] if item.get("shared_resource") else []
                ),
                "supporting_evidence": supporting,
                "contradicting_evidence": contradicting,
                "neutral_evidence": [
                    {
                        "evidence_id": f"EV_V2_{index:02d}_NEUTRAL",
                        "evidence_type": "UNRELATED_NORMAL_SIGNAL",
                        "summary": "A downstream control metric remains inside its normal range.",
                    }
                ],
                "impact_lot_truth": impact_lots,
                "unavailable_data_sources": list(item.get("unavailable_sources", [])),
                "knowledge_asset_links": [
                    {
                        "asset_id": f"RCA_V2_{index:03d}",
                        "document_type": "RCA_CASE",
                        "role": "primary_historical_event",
                        "relevance": 3,
                    },
                    {
                        "asset_id": f"{guide_prefix}_V2_{index:03d}",
                        "document_type": item["guidance_type"],
                        "role": "incident_guidance",
                        "relevance": 3,
                    },
                ],
            }
        )

    for index, item in enumerate(_no_answer_blueprints(), start=1):
        metric_name, observed, unit = item["metric"]
        families.append(
            {
                "incident_family_id": item["id"],
                "partition": item["partition"],
                "synthetic": True,
                "scenario_category": "retrieval_no_answer",
                "expected_status": "no_answer",
                "discovery_lane": "global_semantic",
                "metadata_quality": "missing",
                "requested_task": item["task"],
                "observation_record": {
                    "source_lot_id": f"LOT_V2_NA_{index:02d}",
                    "product_id": f"PROD_V2_NA_{index:02d}",
                    "detected_module": item["module"],
                    "detected_operation": item["operation"],
                    "detected_operation_name": item["operation"],
                    "detected_equipment_id": item["equipment"],
                    "detected_at": f"2026-07-{index:02d}T12:00:00+00:00",
                    "symptom_types": list(item["symptoms"]),
                    "known_measurements": [
                        {
                            "metric_name": metric_name,
                            "observed": observed,
                            "unit": unit,
                        }
                    ],
                    "known_defect_attributes": {
                        "symptom_cluster": "unrepresented_technology",
                        "pattern": item["symptoms"][0],
                    },
                },
                "causal_record": None,
                "process_route": [],
                "resource_relations": [],
                "supporting_evidence": [],
                "contradicting_evidence": [],
                "neutral_evidence": [],
                "impact_lot_truth": [],
                "unavailable_data_sources": [],
                "knowledge_asset_links": [],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "synthetic": True,
        "limitations": (
            "Synthetic benchmark for causal-retrieval engineering; it is not validation "
            "against confidential or production Fab data."
        ),
        "incident_families": families,
    }


def load_incident_catalog(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise EvaluationV2DataError("incident catalog must be a JSON object")
    validate_incident_catalog(raw)
    return raw


def validate_incident_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationV2DataError("unsupported V2 incident schema")
    if catalog.get("dataset_id") != DATASET_ID or catalog.get("synthetic") is not True:
        raise EvaluationV2DataError("catalog must be explicitly Synthetic V2")
    families = catalog.get("incident_families")
    if not isinstance(families, list) or not families:
        raise EvaluationV2DataError("incident catalog requires incident_families[]")
    ids: set[str] = set()
    for family in families:
        if not isinstance(family, dict):
            raise EvaluationV2DataError("incident family must be an object")
        family_id = str(family.get("incident_family_id", ""))
        if not family_id or family_id in ids:
            raise EvaluationV2DataError("incident_family_id values must be non-empty and unique")
        ids.add(family_id)
        if family.get("partition") not in {"calibration", "test"}:
            raise EvaluationV2DataError(f"{family_id} has invalid partition")
        if family.get("synthetic") is not True:
            raise EvaluationV2DataError(f"{family_id} is not marked synthetic")
        observation = family.get("observation_record")
        if not isinstance(observation, dict):
            raise EvaluationV2DataError(f"{family_id} requires observation_record")
        required_observation = {
            "source_lot_id",
            "product_id",
            "detected_module",
            "detected_operation",
            "detected_at",
            "symptom_types",
            "known_measurements",
        }
        missing = sorted(key for key in required_observation if not observation.get(key))
        if missing:
            raise EvaluationV2DataError(f"{family_id} observation missing {missing}")
        if family["expected_status"] != "no_answer":
            causal = family.get("causal_record")
            if not isinstance(causal, dict) or not causal.get("root_cause"):
                raise EvaluationV2DataError(f"{family_id} requires causal_record")
            if len(family.get("knowledge_asset_links", [])) != 2:
                raise EvaluationV2DataError(f"{family_id} requires RCA and guidance assets")


def document_writer_payloads(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Project one approved historical incident into one asset-writing payload."""

    validate_incident_catalog(catalog)
    payloads: list[dict[str, Any]] = []
    for family in catalog["incident_families"]:
        if family["expected_status"] == "no_answer":
            continue
        for link in family["knowledge_asset_links"]:
            payloads.append(
                {
                    "asset_id": link["asset_id"],
                    "document_type": link["document_type"],
                    "incident_family_id": family["incident_family_id"],
                    "observation_record": dict(family["observation_record"]),
                    "causal_record": dict(family["causal_record"]),
                    "supporting_evidence": [dict(item) for item in family["supporting_evidence"]],
                    "contradicting_evidence": [
                        dict(item) for item in family["contradicting_evidence"]
                    ],
                    "corrective_actions": list(family["causal_record"]["corrective_actions"]),
                    "engineering_boundary": (
                        "Historical similarity proposes a hypothesis; current-Lot operational "
                        "evidence remains required for root-cause confirmation."
                    ),
                    "synthetic": True,
                }
            )
    return payloads


def _query_specs(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for family in catalog["incident_families"]:
        family_id = family["incident_family_id"]
        if family["expected_status"] == "no_answer":
            specs.append(
                {
                    "query_key": f"QK_{family_id}",
                    "query_id": f"Q_V2_{family_id}_NO_ANSWER",
                    "incident_family_id": family_id,
                    "partition": family["partition"],
                    "requested_task": family["requested_task"],
                    "no_answer": True,
                    "target_asset_id": "",
                    "target_document_type": _document_type_for_task(family["requested_task"]),
                    "observation_record": family["observation_record"],
                    "metadata_quality": family["metadata_quality"],
                }
            )
            continue
        rca_link, guidance_link = family["knowledge_asset_links"]
        specs.extend(
            [
                {
                    "query_key": f"QK_{family_id}_RCA",
                    "query_id": f"Q_V2_{family_id}_RCA",
                    "incident_family_id": family_id,
                    "partition": family["partition"],
                    "requested_task": "historical_match",
                    "no_answer": False,
                    "target_asset_id": rca_link["asset_id"],
                    "target_document_type": "RCA_CASE",
                    "observation_record": family["observation_record"],
                    "metadata_quality": family["metadata_quality"],
                },
                {
                    "query_key": f"QK_{family_id}_GUIDE",
                    "query_id": f"Q_V2_{family_id}_GUIDE",
                    "incident_family_id": family_id,
                    "partition": family["partition"],
                    "requested_task": (
                        "procedure_guidance"
                        if guidance_link["document_type"] == "SOP"
                        else "engineering_note_lookup"
                    ),
                    "no_answer": False,
                    "target_asset_id": guidance_link["asset_id"],
                    "target_document_type": guidance_link["document_type"],
                    "observation_record": family["observation_record"],
                    "metadata_quality": family["metadata_quality"],
                },
                {
                    "query_key": f"QK_{family_id}_FULL_RCA",
                    "query_id": f"Q_V2_{family_id}_FULL_RCA",
                    "incident_family_id": family_id,
                    "partition": family["partition"],
                    "requested_task": "full_rca",
                    "no_answer": False,
                    "target_asset_id": "",
                    "target_document_type": "",
                    "observation_record": family["observation_record"],
                    "metadata_quality": family["metadata_quality"],
                },
            ]
        )
    return specs


def query_writer_payloads(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the complete and exclusive payload visible to a Query Writer."""

    validate_incident_catalog(catalog)
    return [
        {
            "query_key": spec["query_key"],
            "requested_task": spec["requested_task"],
            "language": "en",
            "metadata_quality": spec["metadata_quality"],
            "observation": {
                "source_lot_id": spec["observation_record"]["source_lot_id"],
                "detected_module": spec["observation_record"]["detected_module"],
                "detected_operation": spec["observation_record"]["detected_operation_name"],
                "detected_at": spec["observation_record"]["detected_at"],
                "symptom_types": list(spec["observation_record"]["symptom_types"]),
                "known_measurements": [
                    dict(item) for item in spec["observation_record"]["known_measurements"]
                ],
                "known_defect_attributes": dict(
                    spec["observation_record"].get("known_defect_attributes", {})
                ),
            },
            "synthetic": True,
        }
        for spec in _query_specs(catalog)
    ]


def _document_type_for_task(task: str) -> str:
    return {
        "historical_match": "RCA_CASE",
        "procedure_guidance": "SOP",
        "engineering_note_lookup": "ENGINEERING_NOTE",
    }[task]


def _write_document(payload: dict[str, Any], index: int) -> dict[str, Any]:
    observation = payload["observation_record"]
    causal = payload["causal_record"]
    document_type = payload["document_type"]
    symptoms = "; ".join(observation["symptom_types"])
    evidence = "; ".join(item["summary"] for item in payload["supporting_evidence"])
    if document_type == "RCA_CASE":
        title = f"Synthetic reviewed event {index:03d}: {observation['detected_module']} signal"
        content = (
            "SYNTHETIC RCA CASE. A prior production-like event presented with "
            f"{symptoms}. The signal was detected at {observation['detected_operation_name']}, "
            "but the investigation kept that location as a ranking hint. "
            f"Evidence reviewed: {evidence} Root cause: {causal['root_cause']}. "
            f"Corrective action: {'; '.join(payload['corrective_actions'])}. "
            f"Boundary: {payload['engineering_boundary']}"
        )
        case_id = payload["asset_id"]
    elif document_type == "SOP":
        title = f"Synthetic containment procedure {index:03d}"
        content = (
            "SYNTHETIC SOP. Trigger pattern: "
            f"{symptoms}. Preserve the source Lot and detection timestamp; compare the actual "
            "route; retain candidates from same-step, upstream, shared-resource, and global "
            "lanes; verify current-Lot operational signals; record unavailable sources; stop "
            "with an inconclusive result when contradictions remain. "
            f"Historical engineering context: {causal['root_cause']}. "
            f"Boundary: {payload['engineering_boundary']}"
        )
        case_id = ""
    else:
        title = f"Synthetic causal-scope engineering note {index:03d}"
        content = (
            "SYNTHETIC ENGINEERING NOTE. An observation containing "
            f"{symptoms} can be detected at {observation['detected_operation_name']} without "
            "originating there. Preserve route distance, configured resource relations, and "
            "metrology repeatability as separate features. In this reviewed example the final "
            f"attribution was {causal['root_cause']}. "
            f"Boundary: {payload['engineering_boundary']}"
        )
        case_id = ""
    return {
        "asset_id": payload["asset_id"],
        "document_id": f"DOC_{payload['asset_id']}",
        "case_id": case_id,
        "incident_family_id": payload["incident_family_id"],
        "document_type": document_type,
        "title": title,
        "content": content,
        "module": causal["causal_module"],
        "observed_module": observation["detected_module"],
        "equipment_type": _EQUIPMENT_TYPE.get(causal["causal_module"], "Unknown"),
        "operation": causal["causal_operation"],
        "tags": [
            observation["known_defect_attributes"]["symptom_cluster"],
            observation["detected_module"],
            causal["causal_module"],
            "Synthetic V2",
        ],
        "created_at": "2025-01-15T00:00:00+00:00",
        "validation_status": "CONFIRMED",
        "publication_policy": "BUILTIN_SYNTHETIC_SEED",
        "synthetic": True,
    }


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.casefold()).strip()


def _index_queries(
    generated: list[GeneratedSurfaceQuery], specs: list[dict[str, Any]], catalog: dict[str, Any]
) -> dict[str, str]:
    expected = {spec["query_key"] for spec in specs}
    mapping: dict[str, str] = {}
    normalized_texts: set[str] = set()
    family_by_id = {item["incident_family_id"]: item for item in catalog["incident_families"]}
    for item in generated:
        text = item.text.strip()
        normalized = _normalize(text)
        if item.query_key not in expected or not normalized:
            raise EvaluationV2DataError("wording provider returned an unknown or empty query")
        if item.query_key in mapping or normalized in normalized_texts:
            raise EvaluationV2DataError("wording provider returned duplicate queries")
        spec = next(value for value in specs if value["query_key"] == item.query_key)
        family = family_by_id[spec["incident_family_id"]]
        forbidden = [spec.get("target_asset_id", ""), spec["query_id"]]
        causal = family.get("causal_record")
        if isinstance(causal, dict):
            forbidden.extend(
                [
                    causal["root_cause"],
                    causal["causal_equipment_id"],
                    *causal["corrective_actions"],
                ]
            )
            if causal["causal_module"] != family["observation_record"]["detected_module"]:
                forbidden.append(causal["causal_module"])
        for value in forbidden:
            token = _normalize(str(value))
            if token and token in normalized:
                raise EvaluationV2DataError(
                    f"query {spec['query_id']} leaks hidden answer term: {value}"
                )
        mapping[item.query_key] = text
        normalized_texts.add(normalized)
    if set(mapping) != expected:
        raise EvaluationV2DataError("wording provider did not return every query key")
    return mapping


def _hard_negatives(
    family: dict[str, Any],
    *,
    document_type: str,
    assets: list[dict[str, Any]],
    count: int = 3,
) -> list[str]:
    target_ids = {link["asset_id"] for link in family.get("knowledge_asset_links", [])}
    cluster = family["observation_record"]["known_defect_attributes"]["symptom_cluster"]
    same_cluster = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and cluster in asset["tags"]
    ]
    same_observed_module = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and asset["observed_module"] == family["observation_record"]["detected_module"]
        and asset not in same_cluster
    ]
    remainder = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and asset not in same_cluster
        and asset not in same_observed_module
    ]
    ordered = same_cluster + same_observed_module + remainder
    return [asset["asset_id"] for asset in ordered[:count]]


def build_evaluation_v2_dataset(
    catalog: dict[str, Any], provider: SurfaceQueryProvider
) -> dict[str, Any]:
    """Build all Retrieval V2, RCA V2, review, and shared Fab artifacts."""

    validate_incident_catalog(catalog)
    doc_payloads = document_writer_payloads(catalog)
    assets = [_write_document(payload, index) for index, payload in enumerate(doc_payloads, 1)]
    specs = _query_specs(catalog)
    query_payloads = query_writer_payloads(catalog)
    query_text = _index_queries(provider.generate(query_payloads), specs, catalog)
    family_by_id = {item["incident_family_id"]: item for item in catalog["incident_families"]}

    retrieval_queries: list[dict[str, Any]] = []
    qrels: dict[str, list[dict[str, Any]]] = {}
    qrel_reviews: list[dict[str, Any]] = []
    scenario_text: dict[str, str] = {}
    for spec in specs:
        if spec["requested_task"] == "full_rca":
            scenario_text[spec["incident_family_id"]] = query_text[spec["query_key"]]
            continue
        family = family_by_id[spec["incident_family_id"]]
        hard_negatives = _hard_negatives(
            family,
            document_type=spec["target_document_type"],
            assets=assets,
            count=4 if spec["no_answer"] else 3,
        )
        retrieval_queries.append(
            {
                "query_id": spec["query_id"],
                "incident_family_id": spec["incident_family_id"],
                "text": query_text[spec["query_key"]],
                "language": "en",
                "question_kind": spec["requested_task"],
                "requested_document_type": spec["target_document_type"],
                "no_answer": spec["no_answer"],
                "hard_negative_asset_ids": hard_negatives,
                "partition": spec["partition"],
                "metadata_policy": "governance_hard_causal_metadata_soft",
                "metadata_quality": spec["metadata_quality"],
                "query_time_cutoff": "2026-08-01T00:00:00+00:00",
            }
        )
        judgments: list[dict[str, Any]] = []
        if not spec["no_answer"]:
            judgments.append({"asset_id": spec["target_asset_id"], "relevance": 3})
            sibling_id = next(
                link["asset_id"]
                for link in family["knowledge_asset_links"]
                if link["asset_id"] != spec["target_asset_id"]
            )
            judgments.append({"asset_id": sibling_id, "relevance": 1})
        judgments.extend({"asset_id": value, "relevance": 0} for value in hard_negatives)
        qrels[spec["query_id"]] = judgments
        for judgment in judgments:
            qrel_reviews.append(
                {
                    "query_id": spec["query_id"],
                    "asset_id": judgment["asset_id"],
                    "provisional_relevance": judgment["relevance"],
                    "decision": REVIEW_PENDING,
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                    "derivation": "Python hidden Incident-to-Asset graph",
                }
            )

    partitions: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "partition_key": "incident_family_id_and_primary_target",
        "synthetic": True,
        "partitions": {},
    }
    for partition in ("calibration", "test"):
        families = [
            family["incident_family_id"]
            for family in catalog["incident_families"]
            if family["partition"] == partition
        ]
        queries = [
            query["query_id"] for query in retrieval_queries if query["partition"] == partition
        ]
        targets = sorted(
            {
                judgment["asset_id"]
                for query_id, judgments in qrels.items()
                if query_id in queries
                for judgment in judgments
                if judgment["relevance"] >= 2
            }
        )
        partitions["partitions"][partition] = {
            "incident_family_ids": sorted(families),
            "query_ids": sorted(queries),
            "primary_target_asset_ids": targets,
        }

    scenarios: list[dict[str, Any]] = []
    scenario_reviews: list[dict[str, Any]] = []
    for family in catalog["incident_families"]:
        if family["expected_status"] == "no_answer":
            continue
        rca_target = family["knowledge_asset_links"][0]["asset_id"]
        negatives = _hard_negatives(family, document_type="RCA_CASE", assets=assets, count=3)
        required_evidence = [item["evidence_id"] for item in family["supporting_evidence"]]
        required_warnings: list[str] = []
        if family["expected_status"] == "inconclusive":
            required_warnings.append("WARN_RCA_INCONCLUSIVE")
        if family["contradicting_evidence"]:
            required_warnings.append("WARN_RCA_CONFLICTING_EVIDENCE")
        if family["unavailable_data_sources"]:
            required_warnings.append("WARN_UNSUPPORTED_DATA_SOURCE")
        scenario_id = f"RCA_V2_{family['incident_family_id']}"
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "incident_family_id": family["incident_family_id"],
                "partition": family["partition"],
                "synthetic": True,
                "query": scenario_text[family["incident_family_id"]],
                "source_lot_id": family["observation_record"]["source_lot_id"],
                "observation_scope": family["observation_record"],
                "scenario_category": family["scenario_category"],
                "expected_status": family["expected_status"],
                "expected_root_cause": family["causal_record"]["root_cause"],
                "expected_causal_module": family["causal_record"]["causal_module"],
                "expected_discovery_lane": family["discovery_lane"],
                "required_evidence_ids": required_evidence,
                "contradicting_evidence_ids": [
                    item["evidence_id"] for item in family["contradicting_evidence"]
                ],
                "required_warning_ids": required_warnings,
                "expected_impact_lots": family["impact_lot_truth"],
                "unavailable_data_sources": family["unavailable_data_sources"],
                "knowledge_candidate_asset_ids": [rca_target, *negatives],
                "hard_negative_asset_ids": negatives,
                "knowledge_is_not_causal_proof": True,
            }
        )
        scenario_reviews.append(
            {
                "scenario_id": scenario_id,
                "incident_family_id": family["incident_family_id"],
                "decision": REVIEW_PENDING,
                "reviewer": "",
                "reviewed_at": "",
                "root_cause_review": REVIEW_PENDING,
                "evidence_chain_review": REVIEW_PENDING,
                "impact_scope_review": REVIEW_PENDING,
                "notes": "",
            }
        )

    corpus = {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": DATASET_ID,
        "synthetic": True,
        "limitations": catalog["limitations"],
        "publication_policy": "BUILTIN_SYNTHETIC_SEED",
        "documents": assets,
    }
    ground_truth = {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": DATASET_ID,
        "synthetic": True,
        "limitations": catalog["limitations"],
        "relevance_threshold": 2,
        "queries": retrieval_queries,
        "qrels": qrels,
        "generation_contract": {
            "document_writer_scope": "one approved Synthetic historical event or guide",
            "query_writer_saw_root_cause": False,
            "query_writer_saw_causal_module": False,
            "query_writer_saw_solution": False,
            "query_writer_saw_target_ids": False,
            "query_writer_saw_qrels": False,
            "query_writer_saw_document_text": False,
            "qrels_generated_by_python": True,
        },
    }
    qrel_review = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "synthetic": True,
        "review_gate": "HUMAN_DATASET_REVIEW_REQUIRED",
        "instructions": (
            "A domain reviewer must accept, reject, or adjudicate every provisional relation "
            "before Long Task C release evaluation."
        ),
        "reviews": qrel_reviews,
    }
    rca_scenarios = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "base_dataset": "data/seeds/causal_scope_v2",
        "synthetic": True,
        "limitations": catalog["limitations"],
        "scenarios": scenarios,
    }
    scenario_review = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "synthetic": True,
        "review_gate": "HUMAN_DATASET_REVIEW_REQUIRED",
        "instructions": (
            "Review observation realism, causal truth, operational Evidence chain, and impact "
            "scope before Long Task C."
        ),
        "reviews": scenario_reviews,
    }
    seed_tables = _build_seed_tables(catalog, assets)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "synthetic": True,
        "limitations": catalog["limitations"],
        "generation": {
            "query_wording_provider": provider.provider_name,
            "model": provider.model_name,
            "prompt_version": f"{QUERY_PROMPT_NAME}_{QUERY_PROMPT_VERSION}",
            "paid_call_count": provider.paid_call_count,
            "raw_prompts_stored": False,
            "raw_responses_stored": False,
            "qrels_generated_by_python": True,
            "partitions_generated_by_python": True,
        },
        "counts": {
            "incident_families": len(catalog["incident_families"]),
            "answerable_families": sum(
                item["expected_status"] != "no_answer" for item in catalog["incident_families"]
            ),
            "documents": len(assets),
            "retrieval_queries": len(retrieval_queries),
            "no_answer_queries": sum(query["no_answer"] for query in retrieval_queries),
            "rca_scenarios": len(scenarios),
        },
    }
    return {
        "catalog": catalog,
        "corpus": corpus,
        "ground_truth": ground_truth,
        "partitions": partitions,
        "qrel_review": qrel_review,
        "rca_scenarios": rca_scenarios,
        "scenario_review": scenario_review,
        "seed_tables": seed_tables,
        "manifest": manifest,
        "query_writer_payloads": query_payloads,
        "document_writer_payloads": doc_payloads,
    }


def _build_seed_tables(
    catalog: dict[str, Any], assets: list[dict[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    """Build one shared, internally consistent Synthetic Fab world."""

    tables: dict[str, list[dict[str, str]]] = {name: [] for name in _CSV_FIELDS}
    equipment_catalog: dict[str, tuple[str, str]] = {}
    for operation, name, module, equipment_type in _OPERATIONS:
        equipment_id = f"EQ_V2_{operation}"
        equipment_catalog[equipment_id] = (module, equipment_type)
        tables["operation_master"].append(
            {
                "operation_no": operation,
                "operation_name": name,
                "module": module,
                "process_area": "Synthetic Fab V2",
                "material": "",
                "canonical_equipment_type": equipment_type,
                "is_critical": "true",
            }
        )

    start = datetime(2026, 6, 1, tzinfo=UTC)
    for index, family in enumerate(
        (item for item in catalog["incident_families"] if item["expected_status"] != "no_answer"),
        start=1,
    ):
        observation = family["observation_record"]
        causal = family["causal_record"]
        product_id = observation["product_id"]
        route_id = f"ROUTE_{product_id}"
        family_start = start + timedelta(days=index)
        lot_ids = [
            observation["source_lot_id"],
            *family["impact_lot_truth"],
            f"LOT_V2_{index:02d}_CTRL_01",
            f"LOT_V2_{index:02d}_CTRL_02",
        ]
        for sequence, route in enumerate(family["process_route"], start=1):
            tables["process_route"].append(
                {
                    "route_id": route_id,
                    "product_id": product_id,
                    "operation_no": route["operation_no"],
                    "sequence_no": str(sequence),
                    "module": route["module"],
                    "operation_name": route["operation_name"],
                    "is_critical": "true",
                }
            )
        for lot_offset, lot_id in enumerate(lot_ids):
            lot_start = family_start + timedelta(hours=lot_offset * 3)
            wafer_id = f"{lot_id}_W01"
            tables["lot_master"].append(
                {
                    "lot_id": lot_id,
                    "product_id": product_id,
                    "technology": "Synthetic 28nm logic",
                    "route_id": route_id,
                    "wafer_qty": "1",
                    "lot_type": "SYNTHETIC_EVALUATION",
                    "priority": "5",
                    "status": "COMPLETE",
                    "current_operation_no": "9000",
                    "created_at": (lot_start - timedelta(hours=2)).isoformat(),
                    "started_at": lot_start.isoformat(),
                    "finished_at": (lot_start + timedelta(hours=20)).isoformat(),
                }
            )
            tables["wafer_master"].append(
                {
                    "wafer_id": wafer_id,
                    "lot_id": lot_id,
                    "wafer_no": "1",
                    "slot": "1",
                    "status": "COMPLETE",
                }
            )
            for op_index, (operation, name, module, equipment_type) in enumerate(_OPERATIONS):
                equipment_id = f"EQ_V2_{operation}"
                exposed = (
                    lot_id == observation["source_lot_id"] or lot_id in family["impact_lot_truth"]
                )
                if (
                    exposed
                    and operation == causal["causal_operation"]
                    and causal["causal_equipment_id"] != "UNRESOLVED"
                ):
                    equipment_id = causal["causal_equipment_id"]
                if (
                    lot_id == observation["source_lot_id"]
                    and operation == observation["detected_operation"]
                ):
                    equipment_id = observation["detected_equipment_id"]
                equipment_catalog[equipment_id] = (module, equipment_type)
                chamber_id = f"{equipment_id}_CH01"
                recipe_id = f"RCP_V2_{operation}"
                executed = lot_start + timedelta(hours=op_index * 2)
                tables["process_history"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "route_id": route_id,
                        "operation_no": operation,
                        "operation_name": name,
                        "module": module,
                        "equipment_id": equipment_id,
                        "chamber_id": chamber_id,
                        "recipe_id": recipe_id,
                        "recipe_version": "V2.1",
                        "started_at": executed.isoformat(),
                        "ended_at": (executed + timedelta(minutes=45)).isoformat(),
                        "operator_id": "SYNTHETIC_AUTO",
                        "process_result": "COMPLETE",
                    }
                )
                tables["recipe_history"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": operation,
                        "equipment_id": equipment_id,
                        "chamber_id": chamber_id,
                        "recipe_id": recipe_id,
                        "recipe_version": "V2.1",
                        "executed_at": executed.isoformat(),
                    }
                )
            is_exposed = (
                lot_id == observation["source_lot_id"] or lot_id in family["impact_lot_truth"]
            )
            if causal["causal_equipment_id"] != "UNRESOLVED":
                observed_value = (
                    118.0 if is_exposed and family["expected_status"] == "supported" else 101.0
                )
                tables["fdc_feature"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": causal["causal_operation"],
                        "equipment_id": (
                            causal["causal_equipment_id"]
                            if is_exposed
                            else f"EQ_V2_{causal['causal_operation']}"
                        ),
                        "chamber_id": (
                            f"{causal['causal_equipment_id']}_CH01"
                            if is_exposed
                            else f"EQ_V2_{causal['causal_operation']}_CH01"
                        ),
                        "recipe_id": f"RCP_V2_{causal['causal_operation']}",
                        "recipe_version": "V2.1",
                        "parameter_name": causal["cause_signal"],
                        "baseline_value": "100.0",
                        "observed_value": f"{observed_value:.1f}",
                        "delta_percent": f"{observed_value - 100.0:.1f}",
                        "unit": "normalized",
                        "trend_slope": "0.2" if is_exposed else "0.0",
                        "ooc_flag": str(observed_value >= 115.0).lower(),
                        "severity": "HIGH" if observed_value >= 115.0 else "NORMAL",
                        "measured_at": (lot_start + timedelta(hours=10)).isoformat(),
                    }
                )
            metric = observation["known_measurements"][0]
            tables["metrology_result"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "operation_no": observation["detected_operation"],
                    "measurement_stage": "POST_PROCESS",
                    "metric_name": metric["metric_name"],
                    "measured_value": str(
                        metric["observed"] if lot_id == observation["source_lot_id"] else 0.0
                    ),
                    "unit": metric["unit"],
                    "spec_low": "-10.0",
                    "spec_high": "10.0",
                    "pass_fail": "FAIL" if lot_id == observation["source_lot_id"] else "PASS",
                    "metrology_tool": observation["detected_equipment_id"],
                    "measured_at": (lot_start + timedelta(hours=17)).isoformat(),
                }
            )
            tables["defect_summary"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "inspection_operation_no": observation["detected_operation"],
                    "defect_type": observation["symptom_types"][0],
                    "defect_count": "18" if lot_id == observation["source_lot_id"] else "2",
                    "defect_density": "0.8" if lot_id == observation["source_lot_id"] else "0.05",
                    "pattern_type": observation["known_defect_attributes"]["pattern"],
                    "location_region": "MIXED",
                    "inspected_at": (lot_start + timedelta(hours=18)).isoformat(),
                }
            )
            tables["wat_result"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "test_item": "V2_PARAMETRIC_CHECK",
                    "parameter_name": "yield_proxy",
                    "measured_value": "0.82" if lot_id == observation["source_lot_id"] else "0.98",
                    "spec_low": "0.90",
                    "spec_high": "1.00",
                    "pass_fail": "FAIL" if lot_id == observation["source_lot_id"] else "PASS",
                    "fail_mode": "SYNTHETIC_SIGNAL"
                    if lot_id == observation["source_lot_id"]
                    else "",
                    "tested_at": (lot_start + timedelta(hours=20)).isoformat(),
                }
            )
        source_lot = observation["source_lot_id"]
        tables["hold_history"].append(
            {
                "hold_id": f"HOLD_{family['incident_family_id']}",
                "lot_id": source_lot,
                "wafer_id": "",
                "hold_type": "ENGINEERING",
                "hold_code": "SYNTHETIC_V2_REVIEW",
                "hold_reason": "Causal-scope evaluation signal",
                "hold_comment": "Synthetic evaluation Lot; detected location is not causal proof.",
                "created_by": "SYNTHETIC_AUTO",
                "created_at": (family_start + timedelta(hours=19)).isoformat(),
                "released_by": "",
                "released_at": "",
                "release_comment": "",
            }
        )
        if (
            family["expected_status"] == "supported"
            and causal["causal_equipment_id"] != "UNRESOLVED"
        ):
            tables["ooc_event"].append(
                {
                    "feature_id": f"OOC_{family['incident_family_id']}",
                    "equipment_id": causal["causal_equipment_id"],
                    "chamber_id": f"{causal['causal_equipment_id']}_CH01",
                    "operation_no": causal["causal_operation"],
                    "parameter_name": causal["cause_signal"],
                    "alarm_type": "SYNTHETIC_V2_OOC",
                    "severity": "HIGH",
                    "triggered_at": (family_start + timedelta(hours=10)).isoformat(),
                    "description": "Synthetic current-Lot operational confirmation.",
                }
            )

    for operation, name, module, _ in _OPERATIONS:
        tables["recipe_master"].append(
            {
                "recipe_id": f"RCP_V2_{operation}",
                "recipe_version": "V2.1",
                "recipe_name": f"Synthetic {name}",
                "module": module,
                "recipe_family": f"V2_{module.upper().replace(' ', '_')}",
                "status": "RELEASED",
                "owner": "SYNTHETIC_DATA_TEAM",
                "released_at": "2025-01-01T00:00:00+00:00",
            }
        )
    for equipment_id, (module, equipment_type) in sorted(equipment_catalog.items()):
        operation = _OPERATION_BY_MODULE.get(module, "8000")
        chamber_id = f"{equipment_id}_CH01"
        tables["equipment_master"].append(
            {
                "equipment_id": equipment_id,
                "equipment_type": equipment_type,
                "module": module,
                "process_area": "Synthetic Fab V2",
                "material": "",
                "vendor": "SYNTHETIC",
                "model": "V2",
                "location": "SYNTHETIC_FAB",
                "status": "ACTIVE",
                "installed_at": "2024-01-01T00:00:00+00:00",
            }
        )
        tables["chamber_master"].append(
            {
                "chamber_id": chamber_id,
                "equipment_id": equipment_id,
                "chamber_name": chamber_id,
                "chamber_type": "PROCESS",
                "status": "ACTIVE",
                "installed_at": "2024-01-01T00:00:00+00:00",
            }
        )
        tables["equipment_capability"].append(
            {
                "equipment_id": equipment_id,
                "chamber_id": chamber_id,
                "operation_no": operation,
                "module": module,
                "material": "",
                "recipe_family": f"V2_{module.upper().replace(' ', '_')}",
                "qualification_status": "QUALIFIED",
            }
        )

    for asset in assets:
        if asset["document_type"] == "RCA_CASE":
            family = next(
                item
                for item in catalog["incident_families"]
                if item["incident_family_id"] == asset["incident_family_id"]
            )
            tables["rca_case"].append(
                {
                    "case_id": asset["asset_id"],
                    "title": asset["title"],
                    "technology": "Synthetic 28nm logic",
                    "module": asset["module"],
                    "equipment_type": asset["equipment_type"],
                    "symptom": "; ".join(family["observation_record"]["symptom_types"]),
                    "root_cause": family["causal_record"]["root_cause"],
                    "solution": "; ".join(family["causal_record"]["corrective_actions"]),
                    "confidence": "0.90" if family["expected_status"] == "supported" else "0.40",
                    "created_at": asset["created_at"],
                    "validation_status": "CONFIRMED",
                }
            )
        tables["knowledge_document"].append(
            {
                "document_id": asset["document_id"],
                "case_id": asset["case_id"],
                "document_type": asset["document_type"],
                "title": asset["title"],
                "content": asset["content"],
                "tags": ";".join(asset["tags"]),
                "created_at": asset["created_at"],
                "validation_status": "CONFIRMED",
            }
        )
    return tables


_CSV_FIELDS: dict[str, list[str]] = {
    "lot_master": [
        "lot_id",
        "product_id",
        "technology",
        "route_id",
        "wafer_qty",
        "lot_type",
        "priority",
        "status",
        "current_operation_no",
        "created_at",
        "started_at",
        "finished_at",
    ],
    "wafer_master": ["wafer_id", "lot_id", "wafer_no", "slot", "status"],
    "operation_master": [
        "operation_no",
        "operation_name",
        "module",
        "process_area",
        "material",
        "canonical_equipment_type",
        "is_critical",
    ],
    "process_route": [
        "route_id",
        "product_id",
        "operation_no",
        "sequence_no",
        "module",
        "operation_name",
        "is_critical",
    ],
    "equipment_master": [
        "equipment_id",
        "equipment_type",
        "module",
        "process_area",
        "material",
        "vendor",
        "model",
        "location",
        "status",
        "installed_at",
    ],
    "chamber_master": [
        "chamber_id",
        "equipment_id",
        "chamber_name",
        "chamber_type",
        "status",
        "installed_at",
    ],
    "equipment_capability": [
        "equipment_id",
        "chamber_id",
        "operation_no",
        "module",
        "material",
        "recipe_family",
        "qualification_status",
    ],
    "recipe_master": [
        "recipe_id",
        "recipe_version",
        "recipe_name",
        "module",
        "recipe_family",
        "status",
        "owner",
        "released_at",
    ],
    "recipe_history": [
        "lot_id",
        "wafer_id",
        "operation_no",
        "equipment_id",
        "chamber_id",
        "recipe_id",
        "recipe_version",
        "executed_at",
    ],
    "process_history": [
        "lot_id",
        "wafer_id",
        "route_id",
        "operation_no",
        "operation_name",
        "module",
        "equipment_id",
        "chamber_id",
        "recipe_id",
        "recipe_version",
        "started_at",
        "ended_at",
        "operator_id",
        "process_result",
    ],
    "hold_history": [
        "hold_id",
        "lot_id",
        "wafer_id",
        "hold_type",
        "hold_code",
        "hold_reason",
        "hold_comment",
        "created_by",
        "created_at",
        "released_by",
        "released_at",
        "release_comment",
    ],
    "fdc_feature": [
        "lot_id",
        "wafer_id",
        "operation_no",
        "equipment_id",
        "chamber_id",
        "recipe_id",
        "recipe_version",
        "parameter_name",
        "baseline_value",
        "observed_value",
        "delta_percent",
        "unit",
        "trend_slope",
        "ooc_flag",
        "severity",
        "measured_at",
    ],
    "ooc_event": [
        "feature_id",
        "equipment_id",
        "chamber_id",
        "operation_no",
        "parameter_name",
        "alarm_type",
        "severity",
        "triggered_at",
        "description",
    ],
    "defect_summary": [
        "lot_id",
        "wafer_id",
        "inspection_operation_no",
        "defect_type",
        "defect_count",
        "defect_density",
        "pattern_type",
        "location_region",
        "inspected_at",
    ],
    "metrology_result": [
        "lot_id",
        "wafer_id",
        "operation_no",
        "measurement_stage",
        "metric_name",
        "measured_value",
        "unit",
        "spec_low",
        "spec_high",
        "pass_fail",
        "metrology_tool",
        "measured_at",
    ],
    "wat_result": [
        "lot_id",
        "wafer_id",
        "test_item",
        "parameter_name",
        "measured_value",
        "spec_low",
        "spec_high",
        "pass_fail",
        "fail_mode",
        "tested_at",
    ],
    "rca_case": [
        "case_id",
        "title",
        "technology",
        "module",
        "equipment_type",
        "symptom",
        "root_cause",
        "solution",
        "confidence",
        "created_at",
        "validation_status",
    ],
    "knowledge_document": [
        "document_id",
        "case_id",
        "document_type",
        "title",
        "content",
        "tags",
        "created_at",
        "validation_status",
    ],
}


def _merge_review(
    existing: dict[str, Any], generated: dict[str, Any], keys: tuple[str, ...]
) -> dict[str, Any]:
    prior = {
        tuple(str(item.get(key, "")) for key in keys): item
        for item in existing.get("reviews", [])
        if isinstance(item, dict)
    }
    for item in generated["reviews"]:
        key = tuple(str(item.get(value, "")) for value in keys)
        previous = prior.get(key)
        if not previous:
            continue
        if "provisional_relevance" in item and previous.get("provisional_relevance") != item.get(
            "provisional_relevance"
        ):
            continue
        for field in (
            "decision",
            "reviewer",
            "reviewed_at",
            "notes",
            "root_cause_review",
            "evidence_chain_review",
            "impact_scope_review",
        ):
            if field in previous:
                item[field] = previous[field]
    return generated


def write_evaluation_v2_dataset(
    built: dict[str, Any],
    *,
    knowledge_dir: Path,
    evaluation_dir: Path,
    seed_dir: Path,
    preserve_reviews: bool = True,
) -> None:
    """Write deterministic versioned artifacts while preserving human decisions."""

    knowledge_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    seed_dir.mkdir(parents=True, exist_ok=True)
    _write_json(evaluation_dir / "incident_families_v2.json", built["catalog"])
    _write_json(knowledge_dir / "corpus.json", built["corpus"])
    _write_json(knowledge_dir / "generation_manifest.json", built["manifest"])
    _write_knowledge_csvs(knowledge_dir, built["corpus"]["documents"], built["seed_tables"])
    _write_json(evaluation_dir / "retrieval_ground_truth_v2.json", built["ground_truth"])
    _write_json(evaluation_dir / "retrieval_partitions_v2.json", built["partitions"])

    review_path = evaluation_dir / "retrieval_qrel_review_v2.json"
    qrel_review = built["qrel_review"]
    if preserve_reviews and review_path.exists():
        qrel_review = _merge_review(
            json.loads(review_path.read_text(encoding="utf-8")),
            qrel_review,
            ("query_id", "asset_id"),
        )
    _write_json(review_path, qrel_review)
    _write_json(evaluation_dir / "rca_scenarios_v2.json", built["rca_scenarios"])

    scenario_review_path = evaluation_dir / "rca_scenario_review_v2.json"
    scenario_review = built["scenario_review"]
    if preserve_reviews and scenario_review_path.exists():
        scenario_review = _merge_review(
            json.loads(scenario_review_path.read_text(encoding="utf-8")),
            scenario_review,
            ("scenario_id",),
        )
    _write_json(scenario_review_path, scenario_review)
    for table_name, rows in built["seed_tables"].items():
        _write_csv(seed_dir / f"{table_name}.csv", rows, _CSV_FIELDS[table_name])
    _write_json(
        seed_dir / "ground_truth.json",
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": DATASET_ID,
            "synthetic": True,
            "limitations": built["catalog"]["limitations"],
            "incident_families": [
                {
                    "incident_family_id": family["incident_family_id"],
                    "source_lot_id": family["observation_record"]["source_lot_id"],
                    "detected_module": family["observation_record"]["detected_module"],
                    "causal_module": family["causal_record"]["causal_module"],
                    "root_cause": family["causal_record"]["root_cause"],
                    "impact_lots": family["impact_lot_truth"],
                    "expected_status": family["expected_status"],
                }
                for family in built["catalog"]["incident_families"]
                if family["expected_status"] != "no_answer"
            ],
        },
    )


def _write_knowledge_csvs(
    knowledge_dir: Path,
    assets: list[dict[str, Any]],
    seed_tables: dict[str, list[dict[str, str]]],
) -> None:
    _write_csv(
        knowledge_dir / "rca_case.csv",
        seed_tables["rca_case"],
        _CSV_FIELDS["rca_case"],
    )
    rows = [
        {
            "document_id": asset["document_id"],
            "case_id": asset["case_id"],
            "document_type": asset["document_type"],
            "title": asset["title"],
            "content": asset["content"],
            "tags": ";".join(asset["tags"]),
            "created_at": asset["created_at"],
            "validation_status": asset["validation_status"],
        }
        for asset in assets
    ]
    _write_csv(
        knowledge_dir / "knowledge_document.csv",
        rows,
        _CSV_FIELDS["knowledge_document"],
    )


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class DataQualityReport:
    """Independent structural result; human acceptance is intentionally separate."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, Any]

    @property
    def structural_pass(self) -> bool:
        return not self.errors

    @property
    def human_review_complete(self) -> bool:
        pending_qrels = int(self.metrics.get("pending_qrel_reviews", 1))
        pending_scenarios = int(self.metrics.get("pending_scenario_reviews", 1))
        return pending_qrels == 0 and pending_scenarios == 0


def validate_evaluation_v2_dataset(built: dict[str, Any]) -> DataQualityReport:
    """Audit data isolation, difficulty, partitioning, density, and review coverage."""

    errors: list[str] = []
    warnings: list[str] = []
    catalog = built["catalog"]
    corpus = built["corpus"]
    ground_truth = built["ground_truth"]
    partitions = built["partitions"]["partitions"]
    scenarios = built["rca_scenarios"]["scenarios"]
    assets = corpus["documents"]
    assets_by_id = {item["asset_id"]: item for item in assets}
    family_by_id = {item["incident_family_id"]: item for item in catalog["incident_families"]}

    forbidden_query_payload_keys = {
        "causal_record",
        "causal_module",
        "causal_equipment_id",
        "root_cause",
        "corrective_actions",
        "solution",
        "target_asset_id",
        "asset_id",
        "document_id",
        "document_text",
        "content",
        "qrels",
    }
    for payload in built["query_writer_payloads"]:
        serialized = json.dumps(payload, ensure_ascii=False)
        visible_keys = set(_walk_keys(payload))
        leaked = sorted(visible_keys & forbidden_query_payload_keys)
        if leaked:
            errors.append(f"Query Writer payload {payload['query_key']} exposes {leaked}")
        family_id = (
            payload["query_key"]
            .removeprefix("QK_")
            .split("_RCA")[0]
            .split("_GUIDE")[0]
            .split("_FULL")[0]
        )
        family = family_by_id.get(family_id)
        if family and isinstance(family.get("causal_record"), dict):
            causal = family["causal_record"]
            for value in (causal["root_cause"], causal["causal_equipment_id"]):
                if value and value in serialized:
                    errors.append(f"Query Writer payload {payload['query_key']} leaks {value}")

    calibration_families = set(partitions["calibration"]["incident_family_ids"])
    test_families = set(partitions["test"]["incident_family_ids"])
    if calibration_families & test_families:
        errors.append("calibration/test incident families overlap")
    calibration_targets = set(partitions["calibration"]["primary_target_asset_ids"])
    test_targets = set(partitions["test"]["primary_target_asset_ids"])
    if calibration_targets & test_targets:
        errors.append("calibration/test primary target assets overlap")

    query_ids: set[str] = set()
    normalized_queries: set[str] = set()
    candidate_pool_sizes: list[int] = []
    target_overlaps: list[float] = []
    for query in ground_truth["queries"]:
        query_id = query["query_id"]
        if query_id in query_ids:
            errors.append(f"duplicate retrieval query {query_id}")
        query_ids.add(query_id)
        normalized_query = _normalize(query["text"])
        if normalized_query in normalized_queries:
            errors.append(f"near-duplicate normalized query {query_id}")
        normalized_queries.add(normalized_query)
        pool = [
            asset
            for asset in assets
            if asset["validation_status"] == "CONFIRMED"
            and asset["document_type"] == query["requested_document_type"]
            and asset["created_at"] <= query["query_time_cutoff"]
        ]
        candidate_pool_sizes.append(len(pool))
        if len(pool) < 4:
            errors.append(f"query {query_id} has a trivial hard-constraint pool of {len(pool)}")
        if len(query["hard_negative_asset_ids"]) < 3:
            errors.append(f"query {query_id} requires at least three hard negatives")
        pool_ids = {item["asset_id"] for item in pool}
        if not set(query["hard_negative_asset_ids"]).issubset(pool_ids):
            errors.append(f"query {query_id} has out-of-scope hard negatives")
        judgments = ground_truth["qrels"].get(query_id, [])
        relevant = [item for item in judgments if item["relevance"] >= 2]
        if query["no_answer"]:
            if relevant:
                errors.append(f"no-answer query {query_id} has a relevant qrel")
            if not pool:
                errors.append(f"no-answer query {query_id} has an empty in-scope pool")
        elif len(relevant) != 1:
            errors.append(f"answerable query {query_id} requires one primary target")
        else:
            target = assets_by_id.get(relevant[0]["asset_id"])
            if target is None:
                errors.append(f"query {query_id} references unknown target")
            else:
                target_tokens = set(_normalize(target["content"]).split())
                query_tokens = set(normalized_query.split())
                target_overlaps.append(
                    len(target_tokens & query_tokens) / max(1, len(target_tokens | query_tokens))
                )
                if _has_shared_sentence(query["text"], target["content"]):
                    errors.append(f"query {query_id} copies a target-document sentence")
            family = family_by_id[query["incident_family_id"]]
            causal = family["causal_record"]
            query_normalized = _normalize(query["text"])
            for value in (
                causal["root_cause"],
                causal["causal_equipment_id"],
                *causal["corrective_actions"],
            ):
                token = _normalize(value)
                if token and token in query_normalized:
                    errors.append(f"query {query_id} leaks causal or solution text")

    expected_reviews = {
        (query_id, judgment["asset_id"], judgment["relevance"])
        for query_id, judgments in ground_truth["qrels"].items()
        for judgment in judgments
    }
    actual_reviews = {
        (item["query_id"], item["asset_id"], item["provisional_relevance"])
        for item in built["qrel_review"]["reviews"]
    }
    if expected_reviews != actual_reviews:
        errors.append("qrel review coverage does not exactly match provisional qrels")

    required_categories = {
        "same_step",
        "upstream",
        "shared_resource",
        "metrology_artifact",
        "global_cross_module",
        "conflicting_evidence",
        "insufficient_evidence",
        "impact_lot_truth",
        "unsupported_data_source",
    }
    actual_categories = {item["scenario_category"] for item in scenarios}
    missing_categories = sorted(required_categories - actual_categories)
    if missing_categories:
        errors.append(f"RCA V2 missing scenario categories {missing_categories}")
    for scenario in scenarios:
        if len(scenario["hard_negative_asset_ids"]) < 3:
            errors.append(f"scenario {scenario['scenario_id']} requires three hypotheses")
        if not scenario["knowledge_is_not_causal_proof"]:
            errors.append(f"scenario {scenario['scenario_id']} weakens Knowledge Evidence gate")
        if (
            scenario["expected_status"] == "supported"
            and len(scenario["required_evidence_ids"]) < 3
        ):
            errors.append(f"scenario {scenario['scenario_id']} lacks operational Evidence")

    supported = [item for item in scenarios if item["expected_status"] == "supported"]
    cross_module = [
        item
        for item in supported
        if item["expected_causal_module"] != item["observation_scope"]["detected_module"]
    ]
    cross_module_ratio = len(cross_module) / max(1, len(supported))
    if cross_module_ratio < 0.30:
        errors.append("fewer than 30% of supported RCA scenarios are cross-Module")

    scenario_review_ids = {item["scenario_id"] for item in built["scenario_review"]["reviews"]}
    if scenario_review_ids != {item["scenario_id"] for item in scenarios}:
        errors.append("scenario review coverage is incomplete")
    pending_qrels = sum(
        item["decision"] != REVIEW_ACCEPTED for item in built["qrel_review"]["reviews"]
    )
    pending_scenarios = sum(
        item["decision"] != REVIEW_ACCEPTED
        or item["root_cause_review"] != REVIEW_ACCEPTED
        or item["evidence_chain_review"] != REVIEW_ACCEPTED
        or item["impact_scope_review"] != REVIEW_ACCEPTED
        for item in built["scenario_review"]["reviews"]
    )
    if pending_qrels or pending_scenarios:
        warnings.append(
            "Human review checkpoint is open; Long Task C evaluation must remain blocked."
        )

    metrics = {
        "incident_families": len(catalog["incident_families"]),
        "retrieval_queries": len(ground_truth["queries"]),
        "rca_scenarios": len(scenarios),
        "supported_rca_scenarios": len(supported),
        "cross_module_supported_scenarios": len(cross_module),
        "cross_module_supported_ratio": round(cross_module_ratio, 4),
        "candidate_pool_min": min(candidate_pool_sizes, default=0),
        "candidate_pool_mean": round(
            sum(candidate_pool_sizes) / max(1, len(candidate_pool_sizes)), 2
        ),
        "candidate_pool_max": max(candidate_pool_sizes, default=0),
        "lexical_overlap_mean": round(sum(target_overlaps) / max(1, len(target_overlaps)), 4),
        "lexical_overlap_p95": round(_percentile(target_overlaps, 0.95), 4),
        "pending_qrel_reviews": pending_qrels,
        "pending_scenario_reviews": pending_scenarios,
    }
    return DataQualityReport(tuple(errors), tuple(warnings), metrics)


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            *(str(key) for key in value),
            *(nested for item in value.values() for nested in _walk_keys(item)),
        ]
    if isinstance(value, list):
        return [nested for item in value for nested in _walk_keys(item)]
    return []


def _has_shared_sentence(query: str, content: str) -> bool:
    query_sentences = {
        _normalize(item) for item in re.split(r"[.!?。！？]+", query) if len(_normalize(item)) >= 30
    }
    content_sentences = {
        _normalize(item)
        for item in re.split(r"[.!?。！？]+", content)
        if len(_normalize(item)) >= 30
    }
    return bool(query_sentences & content_sentences)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def catalog_sha256(catalog: dict[str, Any]) -> str:
    canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def data_quality_markdown(report: DataQualityReport) -> str:
    status = "PASS" if report.structural_pass else "FAIL"
    review = "COMPLETE" if report.human_review_complete else "PENDING"
    lines = [
        "# Evaluation V2 Data Quality Checkpoint",
        "",
        "> Synthetic benchmark only; this is not production-Fab validation.",
        "",
        f"- Structural gate: **{status}**",
        f"- Human review gate: **{review}**",
        "- Long Task C release evaluation: **BLOCKED** until human review is complete",
        "",
        "## Metrics",
        "",
    ]
    lines.extend(f"- `{key}`: {value}" for key, value in sorted(report.metrics.items()))
    lines.extend(["", "## Structural errors", ""])
    lines.extend(f"- {value}" for value in report.errors)
    if not report.errors:
        lines.append("- None")
    lines.extend(["", "## Checkpoint warnings", ""])
    lines.extend(f"- {value}" for value in report.warnings)
    if not report.warnings:
        lines.append("- None")
    return "\n".join(lines) + "\n"
