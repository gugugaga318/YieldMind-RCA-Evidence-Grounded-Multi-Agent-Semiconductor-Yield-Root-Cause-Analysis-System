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
            unit = str(measurement["unit"]).rstrip(".")
            signal = f"{measurement['metric_name']} measured {measurement['observed']} {unit}"
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
            "historical_signature": [
                "parallel polish tracks following platen motion",
                "incomplete metal clearing in isolated zones",
            ],
            "metric": ("scratch density", 0.82, "count/mm2"),
            "cause_signal": "conditioner_motor_torque",
            "root_cause": "CMP_CU_11 pad conditioner bearing drag caused intermittent pad glazing",
            "actions": ["replace the conditioner bearing", "qualify pad break-in stability"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "hard_negative_relevance_overrides": {
                "RCA_V2_002": 1,
                "RCA_V2_003": 1,
                "RCA_V2_005": 1,
            },
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
            "historical_signature": [
                "perimeter recess with a one-sided radial profile",
                "crescent-shaped film-map imbalance after polish",
            ],
            "metric": ("edge-center thickness delta", 41.0, "nm"),
            "cause_signal": "anode_contact_voltage",
            "root_cause": (
                "PLATE_CU_04 anode contact intermittency created an upstream copper profile skew"
            ),
            "actions": ["renew the anode contact set", "verify pre-CMP copper map uniformity"],
            "impact_count": 3,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "complete",
            "pre_cmp_metric": ("incoming copper thickness tilt", 38.0, "nm"),
            "secondary_relevant_asset_ids": ["RCA_V2_003"],
            "excluded_candidate_asset_ids": ["RCA_V2_008"],
            "hard_negative_relevance_overrides": {
                "RCA_V2_001": 1,
                "RCA_V2_005": 1,
                "RCA_V2_009": 1,
            },
        },
        {
            "id": "IF_V2_003",
            "partition": "calibration",
            "category": "shared_resource",
            "lane": "shared_resource",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Cu CMP",
            "detected_equipment": "CMP_CU_13",
            "causal_equipment": "CMP_CU_13",
            "cluster": "surface_geometry",
            "symptoms": ["one-sided over-polish arc", "azimuthal thickness crescent"],
            "historical_signature": [
                "single-sector material loss following head rotation",
                "crescent film profile emerging only after planarization",
            ],
            "metric": ("within-wafer removal asymmetry", 34.0, "nm"),
            "cause_signal": "carrier_head_orbit_runout",
            "root_cause": ("CMP_CU_13 carrier-head orbit runout caused azimuthal over-polish"),
            "actions": [
                "repair and dynamically balance the carrier head",
                "inspect lots sharing CMP_CU_13 exposure",
            ],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "missing",
            "pre_cmp_normal_metric": ("incoming copper thickness tilt", 2.0, "nm"),
            "secondary_relevant_asset_ids": ["RCA_V2_002"],
            "excluded_candidate_asset_ids": ["RCA_V2_008"],
            "hard_negative_relevance_overrides": {
                "RCA_V2_001": 1,
                "RCA_V2_005": 1,
                "RCA_V2_009": 1,
            },
            "shared_resource": {
                "resource_type": "equipment",
                "resource_id": "CMP_CU_13",
            },
        },
        {
            "id": "IF_V2_004",
            "partition": "calibration",
            "category": "metrology_artifact",
            "lane": "global_semantic",
            "status": "supported",
            "detected_module": "Metrology",
            "causal_module": "Metrology",
            "detected_equipment": "MET_FILM_03",
            "causal_equipment": "MET_FILM_03",
            "cluster": "parametric_shift",
            "symptoms": ["apparent removal-rate step", "stable electrical distribution"],
            "historical_signature": [
                "abrupt optical film readback displacement",
                "unchanged electrical monitors during the reported process shift",
            ],
            "metric": ("reported post-planarization film thickness", 84.0, "nm"),
            "cause_signal": "reference_wafer_offset",
            "root_cause": (
                "MET_FILM_03 reference-wafer offset produced a false post-CMP thickness shift"
            ),
            "actions": ["recalibrate the optical model", "remeasure retained reference wafers"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "noisy",
            "secondary_relevant_asset_ids": ["RCA_V2_007", "RCA_V2_003"],
            "excluded_candidate_asset_ids": ["RCA_V2_012"],
            "independent_remeasure": {
                "metrology_tool": "MET_FILM_04",
                "measured_value": 100.0,
                "spec_low": 90.0,
                "spec_high": 110.0,
            },
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "parameter_name": "removal_rate_proxy",
                },
                {
                    "module": "CVD",
                    "parameter_name": "deposition_rate_proxy",
                },
            ],
            "wat_expected_fail": False,
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
            "historical_signature": [
                "scattered unremoved metal patches near wafer center",
                "completion timing remaining inside its control band",
            ],
            "metric": ("residue area ratio", 3.8, "percent"),
            "cause_signal": "slurry_pump_pressure_drop_index",
            "root_cause": "unverified CMP slurry delivery restriction",
            "actions": [
                "collect pump pressure traces",
                "run confirmation wafers before attribution",
            ],
            "impact_count": 0,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "conflicting": True,
            "causal_signal_observed_value": 116.0,
            "causal_signal_evidence_summary": (
                "A transient slurry-pump pressure-drop index exceeded its control limit."
            ),
            "contradicting_evidence_summary": (
                "The independent slurry flow meter and CMP endpoint duration remained normal, "
                "so the suspected delivery restriction cannot be confirmed."
            ),
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "parameter_name": "independent_slurry_flow_meter",
                },
                {
                    "module": "Cu CMP",
                    "parameter_name": "endpoint_duration_proxy",
                },
            ],
            "hard_negative_relevance_overrides": {
                "RCA_V2_001": 1,
                "RCA_V2_002": 1,
            },
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
            "historical_signature": [
                "single-wafer profile texture excursion",
                "absence of repeated signatures on adjacent chamber runs",
            ],
            "metric": ("roughness index", 1.7, "a.u."),
            "cause_signal": "unresolved_signal",
            "root_cause": "UNRESOLVED because current-lot equipment evidence is missing",
            "actions": ["retain the wafer for review", "do not promote a historical match"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "missing",
            "unavailable_sources": ["Dry Etch FDC feature history"],
            "control_lots_share_detected_equipment": True,
            "normal_recurrence_control_summary": (
                "Two adjacent control Lots ran on ETCH_METAL_08 without the sidewall defect, "
                "showing no chamber-level recurrence while leaving a one-wafer transient open."
            ),
            "hard_negative_relevance_overrides": {"RCA_V2_004": 1},
        },
        {
            "id": "IF_V2_007",
            "partition": "calibration",
            "category": "same_step",
            "lane": "same_step",
            "status": "supported",
            "detected_module": "CVD",
            "causal_module": "CVD",
            "detected_equipment": "CVD_FILM_07",
            "causal_equipment": "CVD_FILM_07",
            "cluster": "parametric_shift",
            "symptoms": ["true deposited-film thinning", "capacitance monitor shift"],
            "historical_signature": [
                "repeatable film loss on independent optical measurement",
                "electrical capacitance movement consistent with reduced dielectric thickness",
            ],
            "metric": ("deposited film thickness loss", 18.0, "nm"),
            "cause_signal": "chamber_temperature_bias",
            "root_cause": "CVD_FILM_07 chamber-temperature drift reduced deposition rate",
            "actions": ["repair chamber-temperature control", "requalify deposition rate"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "secondary_relevant_asset_ids": ["RCA_V2_004"],
            "hard_negative_relevance_overrides": {"RCA_V2_010": 1},
            "impact_metric_values": [16.0, 14.0],
            "wat_impact_lots_fail": True,
            "control_lots_share_detected_equipment": True,
            "impact_correlation_summary": (
                "The source Lot and two exposed Lots share CVD_FILM_07 temperature OOC, "
                "independently measured film loss, and same-direction electrical movement."
            ),
            "control_recovery_summary": (
                "Two later control Lots on CVD_FILM_07 have normal temperature, film thickness, "
                "and WAT after chamber-temperature recovery."
            ),
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
            "symptoms": ["radial erosion growth", "post-polish thickness tilt"],
            "historical_signature": [
                "radially increasing recessed topography after planarization",
                "pre-polish metal profile leaning across the wafer",
            ],
            "metric": ("erosion range", 36.0, "nm"),
            "cause_signal": "electrolyte_agitation_speed",
            "root_cause": (
                "PLATE_CU_09 agitation controller drift distorted the incoming copper profile"
            ),
            "actions": ["replace the agitation encoder", "qualify incoming copper uniformity"],
            "impact_count": 3,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "noisy",
            "pre_cmp_metric": ("incoming copper thickness tilt", 33.0, "nm"),
            "secondary_relevant_asset_ids": ["RCA_V2_002", "RCA_V2_003"],
            "hard_negative_relevance_overrides": {"RCA_V2_001": 1},
            "impact_metric_values": [34.0, 31.0, 29.0],
            "control_lots_share_causal_equipment": True,
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_21",
                    "parameter_name": "removal_rate_proxy",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_21",
                    "parameter_name": "endpoint_duration_proxy",
                },
            ],
            "normal_process_exclusion_summary": (
                "CMP_CU_21 removal-rate and endpoint signals remain normal on the source Lot."
            ),
            "impact_correlation_summary": (
                "The source Lot and three affected Lots share PLATE_CU_09 agitation OOC, "
                "Pre-CMP copper tilt, and post-CMP erosion."
            ),
            "control_recovery_summary": (
                "Two later control Lots on PLATE_CU_09 have normal agitation, incoming copper "
                "profiles, and post-CMP results after controller recovery."
            ),
        },
        {
            "id": "IF_V2_009",
            "partition": "test",
            "category": "shared_resource",
            "lane": "shared_resource",
            "status": "supported",
            "detected_module": "Cu CMP",
            "causal_module": "Cu CMP",
            "detected_equipment": "CMP_CU_22",
            "causal_equipment": "CMP_CU_22",
            "cluster": "surface_geometry",
            "symptoms": ["repeating arc scratches", "stable scratch orientation"],
            "historical_signature": [
                "curved surface marks recurring with rinse exposure",
                "defect orientation tied to chamber rotation geometry",
            ],
            "metric": ("arc defect count", 14.0, "count/wafer"),
            "cause_signal": "rinse_nozzle_particle_index",
            "root_cause": ("CMP_CU_22 post-polish rinse chamber particle shedding marked wafers"),
            "actions": [
                "clean and qualify the rinse chamber",
                "inspect all lots sharing its exposure window",
            ],
            "impact_count": 3,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "secondary_relevant_asset_ids": ["RCA_V2_001", "RCA_V2_003"],
            "hard_negative_relevance_overrides": {"RCA_V2_002": 1},
            "impact_metric_values": [13.0, 12.0, 11.0],
            "source_defect_count": 14,
            "impact_defect_counts": [13, 12, 11],
            "control_lots_share_detected_equipment": True,
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_22",
                    "parameter_name": "carrier_head_orbit_runout",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_22",
                    "parameter_name": "conditioner_motor_torque",
                },
            ],
            "normal_process_exclusion_summary": (
                "CMP_CU_22 carrier-head runout and pad-conditioner torque remain normal."
            ),
            "impact_correlation_summary": (
                "The source Lot and three affected Lots share CMP_CU_22_CH01 particle OOC "
                "and matching arc orientation, curvature, and location distributions."
            ),
            "control_recovery_summary": (
                "Two later control Lots on CMP_CU_22_CH01 have normal particle indicators "
                "and defect counts after rinse-chamber cleaning."
            ),
            "shared_resource": {
                "resource_type": "chamber",
                "resource_id": "CMP_CU_22_CH01",
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
            "historical_signature": [
                "upper-tail transistor turn-on displacement",
                "interconnect control structures remaining nominal",
            ],
            "metric": ("Vt p99 shift", 47.0, "mV"),
            "cause_signal": "beam_dose_integrator",
            "root_cause": "IMP_WELL_03 dose-integrator drift shifted well implant dose",
            "actions": ["recalibrate the dose integrator", "review lots since the last beam check"],
            "impact_count": 2,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "missing",
            "secondary_relevant_asset_ids": ["RCA_V2_011"],
            "hard_negative_relevance_overrides": {"RCA_V2_014": 1},
            "impact_metric_values": [43.0, 39.0],
            "wat_impact_lots_fail": True,
            "control_lots_share_causal_equipment": True,
            "independent_wat_retest": {"test_equipment_id": "WAT_CELL_09"},
            "normal_wat_signals": [
                {
                    "test_item": "METAL_CONTROL",
                    "parameter_name": "metal_line_resistance",
                    "measured_value": 0.98,
                    "spec_low": 0.90,
                    "spec_high": 1.10,
                },
                {
                    "test_item": "SPATIAL_CONTROL",
                    "parameter_name": "reticle_field_periodicity_index",
                    "measured_value": 0.02,
                    "spec_low": 0.00,
                    "spec_high": 0.10,
                },
            ],
            "independent_wat_retest_summary": (
                "Independent retest on WAT_CELL_09 reproduces the Vt shift on all three "
                "affected Lots, excluding a single-tester artifact."
            ),
            "normal_electrical_exclusion_summary": (
                "Metal resistance and reticle-field periodicity controls remain normal."
            ),
            "impact_correlation_summary": (
                "The source Lot and two affected Lots share IMP_WELL_03 dose-integrator OOC "
                "and same-direction Vt shifts."
            ),
            "control_recovery_summary": (
                "Two later control Lots on IMP_WELL_03 have normal dose integration and Vt "
                "after recalibration."
            ),
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
            "historical_signature": [
                "meandering electrical short pattern across repeated fields",
                "failure cadence matching exposure-row spacing",
            ],
            "metric": ("leakage fail rate", 6.4, "percent"),
            "cause_signal": "reticle_scatter_index",
            "root_cause": "LITHO_SCN_06 reticle haze repeated a line-space bridging signature",
            "actions": ["clean and reinspect the reticle", "screen lots exposed after haze growth"],
            "impact_count": 2,
            "guidance_type": "SOP",
            "metadata_quality": "noisy",
            "secondary_relevant_asset_ids": ["RCA_V2_014"],
            "hard_negative_relevance_overrides": {"RCA_V2_010": 1},
            "observation_spec": (0.0, 1.0),
            "impact_metric_values": [5.8, 5.1],
            "wat_impact_lots_fail": True,
            "control_lots_share_causal_equipment": True,
            "independent_wat_retest": {"test_equipment_id": "WAT_CELL_10"},
            "normal_wat_signals": [
                {
                    "test_item": "DEVICE_CONTROL",
                    "parameter_name": "Vt p99 shift",
                    "measured_value": 3.0,
                    "spec_low": -10.0,
                    "spec_high": 10.0,
                },
                {
                    "test_item": "PROBE_CONTROL",
                    "parameter_name": "probe_contact_resistance",
                    "measured_value": 1.0,
                    "spec_low": 0.8,
                    "spec_high": 1.2,
                },
                {
                    "test_item": "SEQUENCE_CONTROL",
                    "parameter_name": "test_sequence_correlation_index",
                    "measured_value": 0.03,
                    "spec_low": 0.0,
                    "spec_high": 0.1,
                },
            ],
            "independent_wat_retest_summary": (
                "Independent retest on WAT_CELL_10 reproduces the same leakage rate and "
                "spatial periodicity on all affected Lots."
            ),
            "normal_electrical_exclusion_summary": (
                "Vt shift, probe contact resistance, and test-sequence correlation remain "
                "normal, weakening implant and probe-card hypotheses."
            ),
            "impact_correlation_summary": (
                "The source Lot and two affected Lots share LITHO_SCN_06 reticle-scatter OOC "
                "and leakage periodicity aligned to exposure-field spacing."
            ),
            "control_recovery_summary": (
                "Two later control Lots on LITHO_SCN_06 have normal reticle scatter and leakage "
                "after reticle cleaning."
            ),
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
            "historical_signature": [
                "alternating wafer positions with reduced dielectric deposition",
                "subsequent planarization controls showing no rate loss",
            ],
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
            "secondary_relevant_asset_ids": ["RCA_V2_007", "RCA_V2_004"],
            "observation_spec": (-10.0, 10.0),
            "impact_metric_values": [-28.0, -28.0, -28.0],
            "wat_impact_lots_fail": True,
            "wafer_qty": 6,
            "odd_slot_metric_values": [-28.0, -26.0, -24.0],
            "all_lots_share_detected_equipment": True,
            "control_lots_share_causal_equipment": True,
            "independent_metrology_confirmation": {
                "metrology_tool": "MET_FILM_12",
            },
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "parameter_name": "removal_rate_proxy",
                },
                {
                    "module": "Cu CMP",
                    "parameter_name": "endpoint_duration_proxy",
                },
            ],
            "independent_metrology_confirmation_summary": (
                "Independent MET_FILM_12 remeasurement reproduces the odd-slot film-loss "
                "pattern while even slots remain normal."
            ),
            "normal_process_exclusion_summary": (
                "Downstream CMP removal-rate and endpoint signals remain normal."
            ),
            "impact_correlation_summary": (
                "The source Lot and three affected Lots share CVD_ILD_17 sensor OOC and "
                "film loss on odd wafer slots only."
            ),
            "control_recovery_summary": (
                "Two later control Lots on CVD_ILD_17 have normal FDC and all six wafer slots "
                "inside film-thickness specification after sensor replacement."
            ),
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
            "historical_signature": [
                "brief planarization efficiency reduction without recurrence",
                "later production material remaining inside baseline",
            ],
            "metric": ("endpoint extension", 9.0, "s"),
            "cause_signal": "chemical_batch_trace",
            "root_cause": (
                "UNRESOLVED after normal pad and equipment checks because chemical batch "
                "genealogy is unavailable"
            ),
            "actions": [
                "report the unavailable chemical genealogy source",
                "preserve slurry samples without attributing a batch",
            ],
            "impact_count": 0,
            "guidance_type": "SOP",
            "metadata_quality": "complete",
            "unavailable_sources": ["chemical batch genealogy"],
            "secondary_relevant_asset_ids": [
                "RCA_V2_001",
                "RCA_V2_005",
                "RCA_V2_006",
            ],
            "hard_negative_relevance_overrides": {"RCA_V2_004": 1},
            "observation_source": "FDC",
            "observation_spec": (0.0, 5.0),
            "all_lots_share_detected_equipment": True,
            "detected_step_fdc_excursions": [
                {
                    "parameter_name": "endpoint_extension",
                    "baseline_value": 0.0,
                    "source_value": 9.0,
                    "normal_value": 1.0,
                    "unit": "s",
                },
                {
                    "parameter_name": "removal_rate_drop_index",
                    "baseline_value": 100.0,
                    "source_value": 118.0,
                    "normal_value": 101.0,
                    "unit": "normalized",
                },
            ],
            "normal_fdc_signals": [
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "conditioner_motor_torque",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "pad_life_used_percent",
                    "baseline_value": 50.0,
                    "observed_value": 62.0,
                    "unit": "percent_life",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "slurry_flow",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "slurry_delivery_pressure",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "carrier_pressure",
                },
                {
                    "module": "Cu CMP",
                    "equipment_id": "CMP_CU_23",
                    "parameter_name": "platen_speed",
                },
            ],
            "pre_cmp_normal_metric": ("incoming film thickness tilt", 1.0, "nm"),
            "independent_process_confirmation": {
                "metric_name": "post-CMP residual film delta",
                "source_value": 12.0,
                "normal_value": 1.0,
                "unit": "nm",
                "spec_low": -5.0,
                "spec_high": 5.0,
                "metrology_tool": "MET_CU_MAP_13",
            },
            "detected_step_excursion_summary": (
                "The source Lot has a real endpoint extension and removal-rate dip on "
                "CMP_CU_23; later controls are normal."
            ),
            "normal_process_exclusion_summary": (
                "Pad age, conditioner torque, slurry delivery, carrier pressure, and platen "
                "speed remain normal on the source Lot."
            ),
            "independent_process_confirmation_summary": (
                "Independent post-CMP metrology confirms residual film on the source Lot, "
                "excluding a pure endpoint-sensor artifact."
            ),
            "normal_recurrence_control_summary": (
                "Two later control Lots on CMP_CU_23 have normal removal and endpoint behavior."
            ),
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
            "historical_signature": [
                "intermittent high-ohmic probe readings",
                "excursions repeating with measurement-site sequence",
            ],
            "metric": ("contact resistance p95", 18.0, "ohm"),
            "cause_signal": "probe_contact_repeatability",
            "root_cause": "WAT_CELL_12 probe-card contamination caused false resistance spikes",
            "actions": ["clean and qualify the probe card", "retest retained wafers"],
            "impact_count": 0,
            "guidance_type": "ENGINEERING_NOTE",
            "metadata_quality": "complete",
            "control_lots_share_detected_equipment": True,
            "causal_signal_measured_offset_hours": 20.0,
            "independent_wat_retest": {
                "test_equipment_id": "WAT_CELL_13",
                "measured_value": 5.2,
                "pass_fail": True,
                "fail_mode": "",
            },
            "post_clean_wat_retest": {
                "test_equipment_id": "WAT_CELL_12",
                "test_item": "V2_POST_CLEAN_QUALIFICATION_RETEST",
                "measured_value": 5.1,
                "pass_fail": True,
                "fail_mode": "",
            },
            "equipment_diagnostic_signals": [
                {
                    "parameter_name": "probe_card_contamination_index",
                    "baseline_value": 0.0,
                    "observed_value": 82.0,
                    "unit": "percent",
                    "trend_slope": 0.6,
                    "ooc_flag": True,
                    "severity": "HIGH",
                    "measured_offset_minutes": 35,
                },
                {
                    "parameter_name": "post_clean_probe_contact_repeatability",
                    "baseline_value": 100.0,
                    "observed_value": 101.0,
                    "unit": "normalized",
                    "trend_slope": 0.0,
                    "ooc_flag": False,
                    "severity": "NORMAL",
                    "measured_offset_minutes": 50,
                },
            ],
            "equipment_disposition": {
                "hold_code": "WAT_PROBE_CARD_CONTAMINATION",
                "hold_reason": "Probe-card optical inspection confirmed contamination",
                "hold_comment": (
                    "A same-wafer cross-tool retest on WAT_CELL_13 passed before optical "
                    "inspection found residue on the WAT_CELL_12 probe card."
                ),
                "release_comment": (
                    "The probe card was cleaned and qualified; the retained wafer and two "
                    "later control Lots passed on WAT_CELL_12. The qualification-to-cleaning "
                    "genealogy contains no additional production Lots."
                ),
            },
            "causal_signal_evidence_summary": (
                "WAT_CELL_12 probe-contact repeatability is abnormal on the source Lot; this "
                "localizes the signal to the measurement system but does not alone prove "
                "probe-card contamination."
            ),
            "independent_wat_retest_summary": (
                "The retained source wafer passes on independent WAT_CELL_13, excluding a "
                "reproducible wafer electrical excursion."
            ),
            "equipment_inspection_summary": (
                "Optical inspection finds contamination residue on the WAT_CELL_12 probe card."
            ),
            "post_clean_recovery_summary": (
                "After probe-card cleaning and qualification, the retained wafer and two later "
                "control Lots pass on WAT_CELL_12 without measurement-sequence repetition."
            ),
            "impact_scope_audit_summary": (
                "The qualification-to-cleaning equipment genealogy contains only the source "
                "Lot, so no additional impacted Lots are confirmed."
            ),
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
            "cluster": "parametric_shift",
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
            "cluster": "surface_geometry",
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
            "cluster": "parametric_shift",
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
            "cluster": "surface_geometry",
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
        if item.get("pre_cmp_metric"):
            pre_cmp_name, pre_cmp_value, pre_cmp_unit = item["pre_cmp_metric"]
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_PRE_CMP_PROFILE",
                    "evidence_type": "METROLOGY_PRE_CMP",
                    "summary": (
                        f"Pre-CMP {pre_cmp_name} measured {pre_cmp_value} "
                        f"{pre_cmp_unit}, proving the profile existed before polish."
                    ),
                }
            )
        if item.get("pre_cmp_normal_metric"):
            normal_name, normal_value, normal_unit = item["pre_cmp_normal_metric"]
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_PRE_CMP_NORMAL",
                    "evidence_type": "METROLOGY_PRE_CMP",
                    "summary": (
                        f"Pre-CMP {normal_name} measured {normal_value} {normal_unit} "
                        "inside specification, weakening the upstream-profile hypothesis."
                    ),
                }
            )
        if item.get("independent_remeasure"):
            remeasure = item["independent_remeasure"]
            supporting.extend(
                [
                    {
                        "evidence_id": f"EV_V2_{index:02d}_REFERENCE_CALIBRATION",
                        "evidence_type": "METROLOGY_REFERENCE_CALIBRATION",
                        "summary": (
                            f"{item['detected_equipment']} failed its reference-wafer "
                            "calibration check with a reproducible offset."
                        ),
                    },
                    {
                        "evidence_id": f"EV_V2_{index:02d}_INDEPENDENT_REMEASURE",
                        "evidence_type": "INDEPENDENT_METROLOGY_REMEASURE",
                        "summary": (
                            f"Independent remeasurement on {remeasure['metrology_tool']} "
                            f"returned {remeasure['measured_value']} nm inside specification."
                        ),
                    },
                    {
                        "evidence_id": f"EV_V2_{index:02d}_PROCESS_FDC_NORMAL",
                        "evidence_type": "PROCESS_FDC_NORMAL_EXCLUSION",
                        "summary": "Current-Lot CVD and Cu CMP operational signals remain normal.",
                    },
                    {
                        "evidence_id": f"EV_V2_{index:02d}_WAT_NORMAL",
                        "evidence_type": "WAT_NORMAL_EXCLUSION",
                        "summary": (
                            "Current-Lot electrical checks remain normal, supporting a "
                            "measurement artifact rather than a real process excursion."
                        ),
                    },
                ]
            )
        if item.get("normal_recurrence_control_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_SAME_CHAMBER_CONTROLS",
                    "evidence_type": "DEFECT_RECURRENCE_CHECK",
                    "summary": item["normal_recurrence_control_summary"],
                }
            )
        if item.get("impact_correlation_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_IMPACT_CORRELATION",
                    "evidence_type": "CORRELATED_IMPACT_LOT_EVIDENCE",
                    "summary": item["impact_correlation_summary"],
                }
            )
        if item.get("control_recovery_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_RECOVERY_CONTROLS",
                    "evidence_type": "POST_RECOVERY_CONTROL_EVIDENCE",
                    "summary": item["control_recovery_summary"],
                }
            )
        if item.get("normal_process_exclusion_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_DETECTED_STEP_NORMAL",
                    "evidence_type": "DETECTED_STEP_FDC_NORMAL_EXCLUSION",
                    "summary": item["normal_process_exclusion_summary"],
                }
            )
        if item.get("independent_wat_retest_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_INDEPENDENT_WAT_RETEST",
                    "evidence_type": "INDEPENDENT_WAT_RETEST",
                    "summary": item["independent_wat_retest_summary"],
                }
            )
        if item.get("equipment_inspection_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_EQUIPMENT_INSPECTION",
                    "evidence_type": "EQUIPMENT_INSPECTION_CONFIRMATION",
                    "summary": item["equipment_inspection_summary"],
                }
            )
        if item.get("post_clean_recovery_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_POST_CLEAN_RECOVERY",
                    "evidence_type": "POST_MAINTENANCE_RECOVERY",
                    "summary": item["post_clean_recovery_summary"],
                }
            )
        if item.get("impact_scope_audit_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_IMPACT_SCOPE_AUDIT",
                    "evidence_type": "EQUIPMENT_GENEALOGY_SCOPE_AUDIT",
                    "summary": item["impact_scope_audit_summary"],
                }
            )
        if item.get("normal_electrical_exclusion_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_ELECTRICAL_CONTROLS_NORMAL",
                    "evidence_type": "WAT_NORMAL_EXCLUSION",
                    "summary": item["normal_electrical_exclusion_summary"],
                }
            )
        if item.get("independent_metrology_confirmation_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_INDEPENDENT_METROLOGY",
                    "evidence_type": "INDEPENDENT_METROLOGY_CONFIRMATION",
                    "summary": item["independent_metrology_confirmation_summary"],
                }
            )
        if item.get("detected_step_excursion_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_DETECTED_STEP_EXCURSION",
                    "evidence_type": "DETECTED_STEP_FDC_EXCURSION",
                    "summary": item["detected_step_excursion_summary"],
                }
            )
        if item.get("independent_process_confirmation_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_INDEPENDENT_PROCESS_CONFIRMATION",
                    "evidence_type": "INDEPENDENT_PROCESS_CONFIRMATION",
                    "summary": item["independent_process_confirmation_summary"],
                }
            )
        if item["status"] == "supported" or item.get("causal_signal_evidence_summary"):
            supporting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_CAUSAL_SIGNAL",
                    "evidence_type": "FDC_OR_OPERATIONAL_CONFIRMATION",
                    "summary": item.get(
                        "causal_signal_evidence_summary",
                        "Current-Lot operational evidence supports the causal attribution.",
                    ),
                }
            )
        contradicting = []
        if item.get("conflicting"):
            contradicting.append(
                {
                    "evidence_id": f"EV_V2_{index:02d}_CONTRADICTION",
                    "evidence_type": "FDC_NORMAL_EXCLUSION",
                    "summary": item.get(
                        "contradicting_evidence_summary",
                        "A second independent trace remains normal and blocks confirmation.",
                    ),
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
                "secondary_relevant_asset_ids": list(item.get("secondary_relevant_asset_ids", [])),
                "excluded_candidate_asset_ids": list(item.get("excluded_candidate_asset_ids", [])),
                "hard_negative_relevance_overrides": dict(
                    item.get("hard_negative_relevance_overrides", {})
                ),
                "pre_cmp_metric": (
                    list(item["pre_cmp_metric"]) if item.get("pre_cmp_metric") else []
                ),
                "pre_cmp_normal_metric": (
                    list(item["pre_cmp_normal_metric"]) if item.get("pre_cmp_normal_metric") else []
                ),
                "independent_remeasure": dict(item.get("independent_remeasure", {})),
                "normal_fdc_signals": [
                    dict(signal) for signal in item.get("normal_fdc_signals", [])
                ],
                "causal_signal_observed_value": item.get("causal_signal_observed_value"),
                "wat_expected_fail": bool(item.get("wat_expected_fail", True)),
                "wat_impact_lots_fail": bool(item.get("wat_impact_lots_fail", False)),
                "impact_metric_values": list(item.get("impact_metric_values", [])),
                "observation_spec": list(item.get("observation_spec", [])),
                "source_defect_count": item.get("source_defect_count"),
                "impact_defect_counts": list(item.get("impact_defect_counts", [])),
                "independent_wat_retest": dict(item.get("independent_wat_retest", {})),
                "post_clean_wat_retest": dict(item.get("post_clean_wat_retest", {})),
                "equipment_diagnostic_signals": [
                    dict(signal) for signal in item.get("equipment_diagnostic_signals", [])
                ],
                "equipment_disposition": dict(item.get("equipment_disposition", {})),
                "causal_signal_measured_offset_hours": float(
                    item.get("causal_signal_measured_offset_hours", 10.0)
                ),
                "normal_wat_signals": [
                    dict(signal) for signal in item.get("normal_wat_signals", [])
                ],
                "wafer_qty": int(item.get("wafer_qty", 1)),
                "odd_slot_metric_values": list(item.get("odd_slot_metric_values", [])),
                "all_lots_share_detected_equipment": bool(
                    item.get("all_lots_share_detected_equipment", False)
                ),
                "independent_metrology_confirmation": dict(
                    item.get("independent_metrology_confirmation", {})
                ),
                "observation_source": str(item.get("observation_source", "METROLOGY")),
                "detected_step_fdc_excursions": [
                    dict(signal) for signal in item.get("detected_step_fdc_excursions", [])
                ],
                "independent_process_confirmation": dict(
                    item.get("independent_process_confirmation", {})
                ),
                "control_lots_share_detected_equipment": bool(
                    item.get("control_lots_share_detected_equipment", False)
                ),
                "control_lots_share_causal_equipment": bool(
                    item.get("control_lots_share_causal_equipment", False)
                ),
                "document_observation_summary": list(item["historical_signature"]),
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
                        "symptom_cluster": item["cluster"],
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
            document_summary = family.get("document_observation_summary")
            if not isinstance(document_summary, list) or len(document_summary) < 2:
                raise EvaluationV2DataError(
                    f"{family_id} requires independent document observation wording"
                )
            if len(family.get("knowledge_asset_links", [])) != 2:
                raise EvaluationV2DataError(f"{family_id} requires RCA and guidance assets")
    asset_owner = {
        link["asset_id"]: family
        for family in families
        for link in family.get("knowledge_asset_links", [])
    }
    for family in families:
        for asset_id in family.get("secondary_relevant_asset_ids", []):
            owner = asset_owner.get(asset_id)
            if owner is None:
                raise EvaluationV2DataError(
                    f"{family['incident_family_id']} references unknown secondary asset"
                )
            if family["partition"] == "calibration" and owner["partition"] == "test":
                raise EvaluationV2DataError(
                    f"{family['incident_family_id']} calibration secondary asset leaks test data"
                )


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
                    "partition": family["partition"],
                    "document_observation_summary": list(family["document_observation_summary"]),
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
    symptoms = "; ".join(payload["document_observation_summary"])
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
        "partition": payload["partition"],
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
    target_ids = {
        *(link["asset_id"] for link in family.get("knowledge_asset_links", [])),
        *family.get("secondary_relevant_asset_ids", []),
        *family.get("excluded_candidate_asset_ids", []),
    }
    cluster = family["observation_record"]["known_defect_attributes"]["symptom_cluster"]
    same_cluster_and_module = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and cluster in asset["tags"]
        and asset["observed_module"] == family["observation_record"]["detected_module"]
    ]
    same_cluster = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and cluster in asset["tags"]
        and asset not in same_cluster_and_module
    ]
    same_observed_module = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and asset["observed_module"] == family["observation_record"]["detected_module"]
        and asset not in same_cluster_and_module
        and asset not in same_cluster
    ]
    remainder = [
        asset
        for asset in assets
        if asset["document_type"] == document_type
        and asset["asset_id"] not in target_ids
        and asset not in same_cluster_and_module
        and asset not in same_cluster
        and asset not in same_observed_module
    ]
    ordered = same_cluster_and_module + same_cluster + same_observed_module + remainder
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
        secondary_relevant = (
            list(family.get("secondary_relevant_asset_ids", []))
            if spec["target_document_type"] == "RCA_CASE" and not spec["no_answer"]
            else []
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
                "secondary_relevant_asset_ids": secondary_relevant,
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
        judgments.extend({"asset_id": value, "relevance": 2} for value in secondary_relevant)
        overrides = (
            family.get("hard_negative_relevance_overrides", {})
            if spec["target_document_type"] == "RCA_CASE"
            else {}
        )
        judgments.extend(
            {"asset_id": value, "relevance": int(overrides.get(value, 0))}
            for value in hard_negatives
        )
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
                if judgment["relevance"] == 3
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
        secondary_candidates = list(family.get("secondary_relevant_asset_ids", []))
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
                "knowledge_candidate_asset_ids": [
                    rca_target,
                    *secondary_candidates,
                    *negatives,
                ],
                "secondary_relevant_asset_ids": secondary_candidates,
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
        causal_signal_offset_hours = float(
            family.get("causal_signal_measured_offset_hours", 10.0)
        )
        control_lot_ids = [
            f"LOT_V2_{index:02d}_CTRL_01",
            f"LOT_V2_{index:02d}_CTRL_02",
        ]
        impact_metric_by_lot = dict(
            zip(
                family["impact_lot_truth"],
                family.get("impact_metric_values", []),
                strict=False,
            )
        )
        impact_defect_by_lot = dict(
            zip(
                family["impact_lot_truth"],
                family.get("impact_defect_counts", []),
                strict=False,
            )
        )
        lot_ids = [
            observation["source_lot_id"],
            *family["impact_lot_truth"],
            *control_lot_ids,
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
                    "wafer_qty": str(family.get("wafer_qty", 1)),
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
                if (
                    family.get("all_lots_share_detected_equipment")
                    and operation == observation["detected_operation"]
                ):
                    equipment_id = observation["detected_equipment_id"]
                if (
                    family.get("control_lots_share_detected_equipment")
                    and lot_id in control_lot_ids
                    and operation == observation["detected_operation"]
                ):
                    equipment_id = observation["detected_equipment_id"]
                if (
                    family.get("control_lots_share_causal_equipment")
                    and lot_id in control_lot_ids
                    and operation == causal["causal_operation"]
                    and causal["causal_equipment_id"] != "UNRESOLVED"
                ):
                    equipment_id = causal["causal_equipment_id"]
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
                control_uses_causal_equipment = lot_id in control_lot_ids and bool(
                    (
                        family.get("control_lots_share_detected_equipment")
                        and causal["causal_operation"] == observation["detected_operation"]
                        and causal["causal_equipment_id"] == observation["detected_equipment_id"]
                    )
                    or family.get("control_lots_share_causal_equipment")
                )
                fdc_uses_causal_equipment = is_exposed or control_uses_causal_equipment
                configured_value = family.get("causal_signal_observed_value")
                observed_value = 101.0
                if is_exposed and configured_value is not None:
                    observed_value = float(configured_value)
                elif is_exposed and family["expected_status"] == "supported":
                    observed_value = 118.0
                tables["fdc_feature"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": causal["causal_operation"],
                        "equipment_id": (
                            causal["causal_equipment_id"]
                            if fdc_uses_causal_equipment
                            else f"EQ_V2_{causal['causal_operation']}"
                        ),
                        "chamber_id": (
                            f"{causal['causal_equipment_id']}_CH01"
                            if fdc_uses_causal_equipment
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
                        "measured_at": (
                            lot_start + timedelta(hours=causal_signal_offset_hours)
                        ).isoformat(),
                    }
                )
            for excursion in family.get("detected_step_fdc_excursions", []):
                excursion_is_ooc = lot_id == observation["source_lot_id"]
                excursion_value = (
                    excursion["source_value"]
                    if excursion_is_ooc
                    else excursion["normal_value"]
                )
                excursion_baseline = float(excursion["baseline_value"])
                tables["fdc_feature"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": observation["detected_operation"],
                        "equipment_id": observation["detected_equipment_id"],
                        "chamber_id": f"{observation['detected_equipment_id']}_CH01",
                        "recipe_id": f"RCP_V2_{observation['detected_operation']}",
                        "recipe_version": "V2.1",
                        "parameter_name": excursion["parameter_name"],
                        "baseline_value": str(excursion["baseline_value"]),
                        "observed_value": str(excursion_value),
                        "delta_percent": f"{float(excursion_value) - excursion_baseline:.1f}",
                        "unit": excursion["unit"],
                        "trend_slope": "0.2" if excursion_is_ooc else "0.0",
                        "ooc_flag": str(excursion_is_ooc).lower(),
                        "severity": "HIGH" if excursion_is_ooc else "NORMAL",
                        "measured_at": (lot_start + timedelta(hours=10)).isoformat(),
                    }
                )
            if lot_id == observation["source_lot_id"]:
                for signal in family.get("normal_fdc_signals", []):
                    signal_operation = _OPERATION_BY_MODULE[signal["module"]]
                    signal_equipment = signal.get(
                        "equipment_id", f"EQ_V2_{signal_operation}"
                    )
                    signal_baseline = float(signal.get("baseline_value", 100.0))
                    signal_observed = float(signal.get("observed_value", 101.0))
                    tables["fdc_feature"].append(
                        {
                            "lot_id": lot_id,
                            "wafer_id": wafer_id,
                            "operation_no": signal_operation,
                            "equipment_id": signal_equipment,
                            "chamber_id": f"{signal_equipment}_CH01",
                            "recipe_id": f"RCP_V2_{signal_operation}",
                            "recipe_version": "V2.1",
                            "parameter_name": signal["parameter_name"],
                            "baseline_value": str(signal_baseline),
                            "observed_value": str(signal_observed),
                            "delta_percent": f"{signal_observed - signal_baseline:.1f}",
                            "unit": signal.get("unit", "normalized"),
                            "trend_slope": "0.0",
                            "ooc_flag": "false",
                            "severity": "NORMAL",
                            "measured_at": (lot_start + timedelta(hours=10)).isoformat(),
                        }
                    )
            metric = observation["known_measurements"][0]
            measurement_value = (
                metric["observed"]
                if lot_id == observation["source_lot_id"]
                else impact_metric_by_lot.get(lot_id, 0.0)
            )
            measurement_fail = bool(
                lot_id == observation["source_lot_id"] or lot_id in impact_metric_by_lot
            )
            observation_spec = family.get("observation_spec", [])
            measurement_spec_low = (
                str(observation_spec[0]) if observation_spec else "-10.0"
            )
            measurement_spec_high = (
                str(observation_spec[1]) if observation_spec else "10.0"
            )
            if (
                observation["detected_module"] != "WAT"
                and family.get("observation_source") != "FDC"
            ):
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": observation["detected_operation"],
                        "measurement_stage": "POST_PROCESS",
                        "metric_name": metric["metric_name"],
                        "measured_value": str(measurement_value),
                        "unit": metric["unit"],
                        "spec_low": measurement_spec_low,
                        "spec_high": measurement_spec_high,
                        "pass_fail": "false" if measurement_fail else "true",
                        "metrology_tool": observation["detected_equipment_id"],
                        "measured_at": (lot_start + timedelta(hours=17)).isoformat(),
                    }
                )
            if lot_id == observation["source_lot_id"] and family.get(
                "independent_remeasure"
            ):
                remeasure = family["independent_remeasure"]
                remeasure_tool = remeasure["metrology_tool"]
                equipment_catalog[remeasure_tool] = (
                    observation["detected_module"],
                    _EQUIPMENT_TYPE[observation["detected_module"]],
                )
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": observation["detected_operation"],
                        "measurement_stage": "INDEPENDENT_REMEASURE",
                        "metric_name": metric["metric_name"],
                        "measured_value": str(remeasure["measured_value"]),
                        "unit": metric["unit"],
                        "spec_low": str(remeasure["spec_low"]),
                        "spec_high": str(remeasure["spec_high"]),
                        "pass_fail": "true",
                        "metrology_tool": remeasure_tool,
                        "measured_at": (lot_start + timedelta(hours=18)).isoformat(),
                    }
                )
            independent_confirmation = family.get("independent_metrology_confirmation", {})
            if observation["detected_module"] != "WAT" and independent_confirmation:
                confirmation_tool = independent_confirmation["metrology_tool"]
                equipment_catalog[confirmation_tool] = (
                    observation["detected_module"],
                    _EQUIPMENT_TYPE[observation["detected_module"]],
                )
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": observation["detected_operation"],
                        "measurement_stage": "INDEPENDENT_CONFIRMATION",
                        "metric_name": metric["metric_name"],
                        "measured_value": str(measurement_value),
                        "unit": metric["unit"],
                        "spec_low": measurement_spec_low,
                        "spec_high": measurement_spec_high,
                        "pass_fail": "false" if measurement_fail else "true",
                        "metrology_tool": confirmation_tool,
                        "measured_at": (lot_start + timedelta(hours=18, minutes=30)).isoformat(),
                    }
                )
            process_confirmation = family.get("independent_process_confirmation", {})
            if process_confirmation:
                process_confirmation_tool = process_confirmation["metrology_tool"]
                equipment_catalog[process_confirmation_tool] = (
                    "Metrology",
                    _EQUIPMENT_TYPE["Metrology"],
                )
                process_confirmation_failed = lot_id == observation["source_lot_id"]
                process_confirmation_value = (
                    process_confirmation["source_value"]
                    if process_confirmation_failed
                    else process_confirmation["normal_value"]
                )
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": observation["detected_operation"],
                        "measurement_stage": "POST_CMP_CONFIRMATION",
                        "metric_name": process_confirmation["metric_name"],
                        "measured_value": str(process_confirmation_value),
                        "unit": process_confirmation["unit"],
                        "spec_low": str(process_confirmation["spec_low"]),
                        "spec_high": str(process_confirmation["spec_high"]),
                        "pass_fail": "false" if process_confirmation_failed else "true",
                        "metrology_tool": process_confirmation_tool,
                        "measured_at": (lot_start + timedelta(hours=18, minutes=30)).isoformat(),
                    }
                )
            if family.get("pre_cmp_metric"):
                pre_cmp_name, pre_cmp_value, pre_cmp_unit = family["pre_cmp_metric"]
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": causal["causal_operation"],
                        "measurement_stage": "PRE_CMP",
                        "metric_name": str(pre_cmp_name),
                        "measured_value": str(pre_cmp_value if is_exposed else 2.0),
                        "unit": str(pre_cmp_unit),
                        "spec_low": "-10.0",
                        "spec_high": "10.0",
                        "pass_fail": "false" if is_exposed else "true",
                        "metrology_tool": "MET_CU_MAP_01",
                        "measured_at": (lot_start + timedelta(hours=11)).isoformat(),
                    }
                )
            if family.get("pre_cmp_normal_metric"):
                normal_name, normal_value, normal_unit = family["pre_cmp_normal_metric"]
                tables["metrology_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "operation_no": _OPERATION_BY_MODULE["Electroplating"],
                        "measurement_stage": "PRE_CMP",
                        "metric_name": str(normal_name),
                        "measured_value": str(normal_value if is_exposed else 1.0),
                        "unit": str(normal_unit),
                        "spec_low": "-10.0",
                        "spec_high": "10.0",
                        "pass_fail": "true",
                        "metrology_tool": "MET_CU_MAP_01",
                        "measured_at": (lot_start + timedelta(hours=11)).isoformat(),
                    }
                )
            source_defect_count = family.get("source_defect_count")
            defect_count = (
                int(source_defect_count if source_defect_count is not None else 18)
                if lot_id == observation["source_lot_id"]
                else int(impact_defect_by_lot.get(lot_id, 2))
            )
            tables["defect_summary"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "inspection_operation_no": observation["detected_operation"],
                    "defect_type": observation["symptom_types"][0],
                    "defect_count": str(defect_count),
                    "defect_density": "0.8" if defect_count > 2 else "0.05",
                    "pattern_type": observation["known_defect_attributes"]["pattern"],
                    "location_region": "MIXED",
                    "inspected_at": (lot_start + timedelta(hours=18)).isoformat(),
                }
            )
            if lot_id == observation["source_lot_id"]:
                for diagnostic in family.get("equipment_diagnostic_signals", []):
                    baseline = float(diagnostic["baseline_value"])
                    observed = float(diagnostic["observed_value"])
                    tables["fdc_feature"].append(
                        {
                            "lot_id": lot_id,
                            "wafer_id": wafer_id,
                            "operation_no": observation["detected_operation"],
                            "equipment_id": observation["detected_equipment_id"],
                            "chamber_id": f"{observation['detected_equipment_id']}_CH01",
                            "recipe_id": f"RCP_V2_{observation['detected_operation']}",
                            "recipe_version": "V2.1",
                            "parameter_name": diagnostic["parameter_name"],
                            "baseline_value": str(diagnostic["baseline_value"]),
                            "observed_value": str(diagnostic["observed_value"]),
                            "delta_percent": f"{observed - baseline:.1f}",
                            "unit": diagnostic["unit"],
                            "trend_slope": str(diagnostic["trend_slope"]),
                            "ooc_flag": str(bool(diagnostic["ooc_flag"])).lower(),
                            "severity": diagnostic["severity"],
                            "measured_at": (
                                lot_start
                                + timedelta(
                                    hours=20,
                                    minutes=int(diagnostic["measured_offset_minutes"]),
                                )
                            ).isoformat(),
                        }
                    )
            wat_fail = bool(
                (
                    lot_id == observation["source_lot_id"]
                    and family.get("wat_expected_fail", True)
                )
                or (
                    lot_id in family["impact_lot_truth"]
                    and family.get("wat_impact_lots_fail", False)
                )
            )
            wat_is_observation = observation["detected_module"] == "WAT"
            uses_detected_wat_tool = wat_is_observation and bool(
                lot_id == observation["source_lot_id"]
                or family.get("all_lots_share_detected_equipment")
                or (
                    lot_id in control_lot_ids
                    and family.get("control_lots_share_detected_equipment")
                )
            )
            primary_wat_tool = (
                observation["detected_equipment_id"]
                if uses_detected_wat_tool
                else f"EQ_V2_{_OPERATION_BY_MODULE['WAT']}"
            )
            tables["wat_result"].append(
                {
                    "lot_id": lot_id,
                    "wafer_id": wafer_id,
                    "test_equipment_id": primary_wat_tool,
                    "test_item": (
                        "V2_PRIMARY_ELECTRICAL" if wat_is_observation else "V2_PARAMETRIC_CHECK"
                    ),
                    "parameter_name": (
                        metric["metric_name"] if wat_is_observation else "yield_proxy"
                    ),
                    "measured_value": (
                        str(measurement_value)
                        if wat_is_observation
                        else ("0.82" if wat_fail else "0.98")
                    ),
                    "spec_low": measurement_spec_low if wat_is_observation else "0.90",
                    "spec_high": measurement_spec_high if wat_is_observation else "1.00",
                    "pass_fail": "false" if wat_fail else "true",
                    "fail_mode": "SYNTHETIC_SIGNAL" if wat_fail else "",
                    "tested_at": (lot_start + timedelta(hours=20)).isoformat(),
                }
            )
            for signal in family.get("normal_wat_signals", []):
                tables["wat_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "test_equipment_id": primary_wat_tool,
                        "test_item": signal["test_item"],
                        "parameter_name": signal["parameter_name"],
                        "measured_value": str(signal["measured_value"]),
                        "spec_low": str(signal["spec_low"]),
                        "spec_high": str(signal["spec_high"]),
                        "pass_fail": "true",
                        "fail_mode": "",
                        "tested_at": (lot_start + timedelta(hours=20, minutes=10)).isoformat(),
                    }
                )
            independent_retest = family.get("independent_wat_retest", {})
            if wat_is_observation and measurement_fail and independent_retest:
                retest_tool = independent_retest["test_equipment_id"]
                retest_pass = bool(independent_retest.get("pass_fail", False))
                retest_value = independent_retest.get("measured_value", measurement_value)
                equipment_catalog[retest_tool] = (
                    "WAT",
                    _EQUIPMENT_TYPE["WAT"],
                )
                tables["wat_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "test_equipment_id": retest_tool,
                        "test_item": "V2_INDEPENDENT_RETEST",
                        "parameter_name": metric["metric_name"],
                        "measured_value": str(retest_value),
                        "spec_low": measurement_spec_low,
                        "spec_high": measurement_spec_high,
                        "pass_fail": str(retest_pass).lower(),
                        "fail_mode": independent_retest.get(
                            "fail_mode",
                            "" if retest_pass else "SYNTHETIC_SIGNAL_REPRODUCED",
                        ),
                        "tested_at": (lot_start + timedelta(hours=20, minutes=30)).isoformat(),
                    }
                )
            post_clean_retest = family.get("post_clean_wat_retest", {})
            if wat_is_observation and lot_id == observation["source_lot_id"] and post_clean_retest:
                post_clean_tool = post_clean_retest["test_equipment_id"]
                post_clean_pass = bool(post_clean_retest.get("pass_fail", True))
                equipment_catalog[post_clean_tool] = (
                    "WAT",
                    _EQUIPMENT_TYPE["WAT"],
                )
                tables["wat_result"].append(
                    {
                        "lot_id": lot_id,
                        "wafer_id": wafer_id,
                        "test_equipment_id": post_clean_tool,
                        "test_item": post_clean_retest.get(
                            "test_item", "V2_POST_MAINTENANCE_RETEST"
                        ),
                        "parameter_name": metric["metric_name"],
                        "measured_value": str(post_clean_retest["measured_value"]),
                        "spec_low": measurement_spec_low,
                        "spec_high": measurement_spec_high,
                        "pass_fail": str(post_clean_pass).lower(),
                        "fail_mode": post_clean_retest.get(
                            "fail_mode", "" if post_clean_pass else "SYNTHETIC_SIGNAL"
                        ),
                        "tested_at": (lot_start + timedelta(hours=21)).isoformat(),
                    }
                )
            wafer_qty = int(family.get("wafer_qty", 1))
            odd_slot_values = family.get("odd_slot_metric_values", [])
            if wafer_qty > 1 and odd_slot_values:
                base_process_rows = [
                    dict(row)
                    for row in tables["process_history"]
                    if row["lot_id"] == lot_id and row["wafer_id"] == wafer_id
                ]
                base_recipe_rows = [
                    dict(row)
                    for row in tables["recipe_history"]
                    if row["lot_id"] == lot_id and row["wafer_id"] == wafer_id
                ]
                base_fdc_rows = [
                    dict(row)
                    for row in tables["fdc_feature"]
                    if row["lot_id"] == lot_id and row["wafer_id"] == wafer_id
                ]
                confirmation_tool = independent_confirmation.get("metrology_tool", "")
                for wafer_number in range(2, wafer_qty + 1):
                    slot_wafer_id = f"{lot_id}_W{wafer_number:02d}"
                    tables["wafer_master"].append(
                        {
                            "wafer_id": slot_wafer_id,
                            "lot_id": lot_id,
                            "wafer_no": str(wafer_number),
                            "slot": str(wafer_number),
                            "status": "COMPLETE",
                        }
                    )
                    for base_row in base_process_rows:
                        process_row = dict(base_row)
                        process_row["wafer_id"] = slot_wafer_id
                        tables["process_history"].append(process_row)
                    for base_row in base_recipe_rows:
                        recipe_row = dict(base_row)
                        recipe_row["wafer_id"] = slot_wafer_id
                        tables["recipe_history"].append(recipe_row)
                    for base_row in base_fdc_rows:
                        fdc_row = dict(base_row)
                        fdc_row["wafer_id"] = slot_wafer_id
                        tables["fdc_feature"].append(fdc_row)

                    slot_failed = bool(is_exposed and wafer_number % 2 == 1)
                    slot_value = (
                        odd_slot_values[(wafer_number - 1) // 2] if slot_failed else 0.0
                    )
                    for stage, metrology_tool in (
                        ("POST_PROCESS", observation["detected_equipment_id"]),
                        ("INDEPENDENT_CONFIRMATION", confirmation_tool),
                    ):
                        tables["metrology_result"].append(
                            {
                                "lot_id": lot_id,
                                "wafer_id": slot_wafer_id,
                                "operation_no": observation["detected_operation"],
                                "measurement_stage": stage,
                                "metric_name": metric["metric_name"],
                                "measured_value": str(slot_value),
                                "unit": metric["unit"],
                                "spec_low": measurement_spec_low,
                                "spec_high": measurement_spec_high,
                                "pass_fail": "false" if slot_failed else "true",
                                "metrology_tool": metrology_tool,
                                "measured_at": (
                                    lot_start + timedelta(hours=18, minutes=wafer_number)
                                ).isoformat(),
                            }
                        )
                    tables["defect_summary"].append(
                        {
                            "lot_id": lot_id,
                            "wafer_id": slot_wafer_id,
                            "inspection_operation_no": observation["detected_operation"],
                            "defect_type": observation["symptom_types"][0],
                            "defect_count": "18" if slot_failed else "2",
                            "defect_density": "0.8" if slot_failed else "0.05",
                            "pattern_type": observation["known_defect_attributes"]["pattern"],
                            "location_region": "MIXED",
                            "inspected_at": (
                                lot_start + timedelta(hours=18, minutes=wafer_number)
                            ).isoformat(),
                        }
                    )
                    tables["wat_result"].append(
                        {
                            "lot_id": lot_id,
                            "wafer_id": slot_wafer_id,
                            "test_equipment_id": primary_wat_tool,
                            "test_item": "V2_PARAMETRIC_CHECK",
                            "parameter_name": "yield_proxy",
                            "measured_value": "0.82" if slot_failed else "0.98",
                            "spec_low": "0.90",
                            "spec_high": "1.00",
                            "pass_fail": "false" if slot_failed else "true",
                            "fail_mode": "SYNTHETIC_SIGNAL" if slot_failed else "",
                            "tested_at": (
                                lot_start + timedelta(hours=20, minutes=wafer_number)
                            ).isoformat(),
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
        equipment_disposition = family.get("equipment_disposition", {})
        if equipment_disposition:
            tables["hold_history"].append(
                {
                    "hold_id": f"HOLD_{family['incident_family_id']}_EQUIPMENT",
                    "lot_id": source_lot,
                    "wafer_id": "",
                    "hold_type": "EQUIPMENT",
                    "hold_code": equipment_disposition["hold_code"],
                    "hold_reason": equipment_disposition["hold_reason"],
                    "hold_comment": equipment_disposition["hold_comment"],
                    "created_by": "SYNTHETIC_AUTO",
                    "created_at": (family_start + timedelta(hours=20, minutes=40)).isoformat(),
                    "released_by": "SYNTHETIC_AUTO",
                    "released_at": (family_start + timedelta(hours=21, minutes=15)).isoformat(),
                    "release_comment": equipment_disposition["release_comment"],
                }
            )
        configured_value = family.get("causal_signal_observed_value")
        if causal["causal_equipment_id"] != "UNRESOLVED" and (
            family["expected_status"] == "supported"
            or (configured_value is not None and float(configured_value) >= 115.0)
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
                    "triggered_at": (
                        family_start + timedelta(hours=causal_signal_offset_hours)
                    ).isoformat(),
                    "description": (
                        "Synthetic candidate signal; independent evidence is required."
                        if family["expected_status"] == "inconclusive"
                        else "Synthetic current-Lot operational confirmation."
                    ),
                }
            )
        for diagnostic_index, diagnostic in enumerate(
            family.get("equipment_diagnostic_signals", []), start=1
        ):
            if not diagnostic["ooc_flag"]:
                continue
            tables["ooc_event"].append(
                {
                    "feature_id": (
                        f"OOC_{family['incident_family_id']}_DIAGNOSTIC_{diagnostic_index:02d}"
                    ),
                    "equipment_id": observation["detected_equipment_id"],
                    "chamber_id": f"{observation['detected_equipment_id']}_CH01",
                    "operation_no": observation["detected_operation"],
                    "parameter_name": diagnostic["parameter_name"],
                    "alarm_type": "SYNTHETIC_V2_EQUIPMENT_DIAGNOSTIC",
                    "severity": diagnostic["severity"],
                    "triggered_at": (
                        family_start
                        + timedelta(
                            hours=20,
                            minutes=int(diagnostic["measured_offset_minutes"]),
                        )
                    ).isoformat(),
                    "description": (
                        "Synthetic equipment inspection confirmation after independent retest."
                    ),
                }
            )
        for excursion_index, excursion in enumerate(
            family.get("detected_step_fdc_excursions", []), start=1
        ):
            tables["ooc_event"].append(
                {
                    "feature_id": (
                        f"OOC_{family['incident_family_id']}_DETECTED_{excursion_index:02d}"
                    ),
                    "equipment_id": observation["detected_equipment_id"],
                    "chamber_id": f"{observation['detected_equipment_id']}_CH01",
                    "operation_no": observation["detected_operation"],
                    "parameter_name": excursion["parameter_name"],
                    "alarm_type": "SYNTHETIC_V2_OBSERVED_OOC",
                    "severity": "HIGH",
                    "triggered_at": (family_start + timedelta(hours=10)).isoformat(),
                    "description": (
                        "Synthetic detected-step excursion; this is not causal attribution."
                    ),
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
        "test_equipment_id",
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
        "document_observation_summary",
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
    reused_symptom_phrases = 0
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
        judged_ids = {item["asset_id"] for item in judgments}
        if not judged_ids.issubset(pool_ids):
            errors.append(f"query {query_id} has qrels outside its governed candidate pool")
        family = family_by_id[query["incident_family_id"]]
        cluster = family["observation_record"]["known_defect_attributes"]["symptom_cluster"]
        plausible_negatives = [
            asset_id
            for asset_id in query["hard_negative_asset_ids"]
            if cluster in assets_by_id[asset_id]["tags"]
        ]
        if (query["no_answer"] or query["question_kind"] == "historical_match") and len(
            plausible_negatives
        ) < 3:
            errors.append(f"query {query_id} requires three symptom-plausible hard negatives")
        relevant = [item for item in judgments if item["relevance"] >= 2]
        primary = [item for item in judgments if item["relevance"] == 3]
        secondary_ids = {item["asset_id"] for item in judgments if item["relevance"] == 2}
        if secondary_ids != set(query["secondary_relevant_asset_ids"]):
            errors.append(f"query {query_id} secondary relevance declarations disagree")
        if secondary_ids & set(query["hard_negative_asset_ids"]):
            errors.append(f"query {query_id} treats a relevant asset as a hard negative")
        if query["no_answer"]:
            if relevant:
                errors.append(f"no-answer query {query_id} has a relevant qrel")
            if not pool:
                errors.append(f"no-answer query {query_id} has an empty in-scope pool")
        elif len(primary) != 1:
            errors.append(f"answerable query {query_id} requires one primary target")
        else:
            target = assets_by_id.get(primary[0]["asset_id"])
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
                normalized_content = _normalize(target["content"])
                for symptom in family["observation_record"]["symptom_types"]:
                    normalized_symptom = _normalize(symptom)
                    if normalized_symptom and normalized_symptom in normalized_content:
                        reused_symptom_phrases += 1
                        errors.append(
                            f"query {query_id} and target reuse complete symptom phrase: {symptom}"
                        )
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
        family = family_by_id[scenario["incident_family_id"]]
        route_order = {
            item["operation_no"]: int(item["sequence_no"]) for item in family["process_route"]
        }
        detected_operation = scenario["observation_scope"]["detected_operation"]
        causal_operation = family["causal_record"]["causal_operation"]
        if route_order[causal_operation] > route_order[detected_operation]:
            errors.append(f"scenario {scenario['scenario_id']} places cause after first detection")

        histories = built["seed_tables"]["process_history"]
        exposed_lots = {scenario["source_lot_id"], *scenario["expected_impact_lots"]}
        for relation in family["resource_relations"]:
            column = {
                "equipment": "equipment_id",
                "chamber": "chamber_id",
                "recipe": "recipe_id",
            }.get(relation["resource_type"])
            if column is None:
                errors.append(
                    f"scenario {scenario['scenario_id']} uses an unsupported resource type"
                )
                continue
            related_lots = {
                row["lot_id"] for row in histories if row[column] == relation["resource_id"]
            }
            if not exposed_lots.issubset(related_lots):
                errors.append(
                    f"scenario {scenario['scenario_id']} resource truth is absent from seed data"
                )

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
        "complete_symptom_phrase_reuse": reused_symptom_phrases,
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


def human_review_packet_markdown(built: dict[str, Any]) -> str:
    """Render qrel and RCA truth side by side without changing review decisions."""

    assets = {item["asset_id"]: item for item in built["corpus"]["documents"]}
    families = {item["incident_family_id"]: item for item in built["catalog"]["incident_families"]}
    qrel_review = {
        (item["query_id"], item["asset_id"]): item for item in built["qrel_review"]["reviews"]
    }
    scenario_review = {item["scenario_id"]: item for item in built["scenario_review"]["reviews"]}
    lines = [
        "# Evaluation V2 Human Review Packet",
        "",
        "> Synthetic benchmark only. This packet helps a human reviewer compare labels; it "
        "does not approve them.",
        "",
        "## Review instructions",
        "",
        "For each Retrieval group, confirm that relevance 3 is the best answer and relevance "
        "0 candidates are plausible but wrong. For No-answer groups, confirm that every "
        "candidate is related enough to be tempting but none answers the request. For each RCA "
        "scenario, independently review causal truth, operational Evidence, and impact scope. "
        "Record decisions only in the two versioned review JSON files.",
        "",
        "## Retrieval qrel groups",
        "",
    ]
    for query in built["ground_truth"]["queries"]:
        query_id = query["query_id"]
        lines.extend(
            [
                f"### {query_id}",
                "",
                f"- Partition: `{query['partition']}`",
                f"- Requested type: `{query['requested_document_type']}`",
                f"- Expected No-answer: `{str(query['no_answer']).lower()}`",
                f"- Query: {query['text']}",
                "",
                "| Relevance | Decision | Asset | Causal/asset Module | Title |",
                "|---:|---|---|---|---|",
            ]
        )
        for judgment in built["ground_truth"]["qrels"][query_id]:
            asset = assets[judgment["asset_id"]]
            review = qrel_review[(query_id, judgment["asset_id"])]
            lines.append(
                "| "
                f"{judgment['relevance']} | {review['decision']} | "
                f"`{asset['asset_id']}` | {_markdown_cell(asset['module'])} | "
                f"{_markdown_cell(asset['title'])} |"
            )
        lines.extend(["", "Candidate excerpts:", ""])
        for judgment in built["ground_truth"]["qrels"][query_id]:
            asset = assets[judgment["asset_id"]]
            excerpt = " ".join(asset["content"].split())[:360]
            lines.append(f"- `{asset['asset_id']}` (rel={judgment['relevance']}): {excerpt}")
        lines.append("")

    lines.extend(["## RCA scenario groups", ""])
    for scenario in built["rca_scenarios"]["scenarios"]:
        family = families[scenario["incident_family_id"]]
        review = scenario_review[scenario["scenario_id"]]
        evidence_by_id = {
            item["evidence_id"]: item["summary"]
            for key in (
                "supporting_evidence",
                "contradicting_evidence",
                "neutral_evidence",
            )
            for item in family[key]
        }
        lines.extend(
            [
                f"### {scenario['scenario_id']}",
                "",
                f"- Overall review decision: `{review['decision']}`",
                f"- Root-cause review: `{review['root_cause_review']}`",
                f"- Evidence-chain review: `{review['evidence_chain_review']}`",
                f"- Impact-scope review: `{review['impact_scope_review']}`",
                f"- Category / lane: `{scenario['scenario_category']}` / "
                f"`{scenario['expected_discovery_lane']}`",
                f"- Expected status: `{scenario['expected_status']}`",
                f"- Observed at: `{scenario['observation_scope']['detected_module']}` / "
                f"`{scenario['observation_scope']['detected_operation_name']}`",
                f"- Expected causal Module: `{scenario['expected_causal_module']}`",
                f"- Expected root cause: {scenario['expected_root_cause']}",
                f"- Expected impact Lots: {', '.join(scenario['expected_impact_lots']) or 'None'}",
                f"- Unavailable sources: "
                f"{', '.join(scenario['unavailable_data_sources']) or 'None'}",
                "- Evidence under review:",
            ]
        )
        for evidence_id in (
            scenario["required_evidence_ids"] + scenario["contradicting_evidence_ids"]
        ):
            lines.append(f"  - `{evidence_id}`: {evidence_by_id[evidence_id]}")
        lines.extend(
            [
                f"- Hard-negative hypotheses: {', '.join(scenario['hard_negative_asset_ids'])}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
