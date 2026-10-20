"""Evaluator-only reference builder; never import this in an execution worker.

Inputs are authorized GT inventory + public process data, never model outputs.
Outputs are exclusive-create and committed by hash in the public freeze manifest.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from benchmark_observer import sha

# Curated from GT mechanism descriptions, not historical model answers.
EQUIVALENTS = [
    ["HMDS prime adhesion excursion", "HMDS vaporizer and prime time instability"],
    [
        "electroplating organic replenishment fill excursion",
        "plating additive filtration fill nonuniformity",
    ],
    [
        "CVD showerhead precursor flow radial deposition excursion",
        "showerhead pressure radial film nonuniformity",
    ],
    [
        "oxidation furnace zone thermal slot band excursion",
        "furnace ramp oxide growth nonuniformity",
    ],
    ["implant beam scan dose nonuniformity", "beam scan linearity current ripple dose excursion"],
    ["exposure chuck vacuum focus excursion", "focus servo leveling crescent excursion"],
    ["wet clean metal contamination junction leakage", "rinse bath contamination leakage path"],
    [
        "CMP platen thermal load removal nonuniformity",
        "platen temperature endpoint torque removal band",
    ],
    ["ash oxygen endpoint polymer residue", "strip ash endpoint overrun contact residue"],
    [
        "anneal recipe ramp soak activation nonuniformity",
        "anneal ramp thermal recipe sheet resistance excursion",
    ],
    [
        "etch MFC pressure incomplete bottom clearing",
        "source mass flow response etch residue excursion",
    ],
    ["CVD RF pressure film stress cracking", "CVD self bias instability stressed film crack"],
    ["spin dry speed vacuum watermark ring", "wet clean drying instability watermark"],
    [
        "developer recipe puddle nozzle scum bridge",
        "develop temperature pressure resist scum bridging",
    ],
]


def build(inventory, public, source_gt):
    sources = {c["case_id"]: c for c in source_gt["cases"]}
    rows = [r for r in inventory["cases"] if r["packet"] == "formal_v1_candidate_r2"]
    with (public / "fab_data/process_history.csv").open(encoding="utf-8-sig") as f:
        process = list(csv.DictReader(f))
    cases = []
    for row in rows:
        number = int(row["case_id"].split("_")[1])
        supported = row["expected_status"] == "supported"
        source = sources[row["case_id"]]
        if source["expected_causal_lanes"] != row["expected_lanes"]:
            raise ValueError("Source GT and inventory Lane constraints differ")
        roots = []
        for lane in row["expected_lanes"]:
            lots = set([row["source_lot_id"], *row["expected_impact_lots"]])
            observations = [
                p
                for p in process
                if p["lot_id"] in lots
                and p["operation_no"] == lane["operation"]
                and (not lane["equipment_id"] or p["equipment_id"] == lane["equipment_id"])
                and (not lane["chamber_id"] or p["chamber_id"] == lane["chamber_id"])
            ]
            if not observations:
                raise ValueError("GT Lane cannot be joined to public exposure")
            roots.append(
                {
                    "mechanism_id": row["family_id"] if supported else None,
                    "equivalent_labels": (
                        [row["expected_root_cause"], *EQUIVALENTS[number - 1]] if supported else []
                    ),
                    "entity": {"equipment": lane["equipment_id"], "chamber": lane["chamber_id"]},
                    "operation": lane["operation"],
                    "proof_granularity": source["proof_granularity"],
                    "recipe": {
                        "allowed_ids": sorted({p["recipe_id"] for p in observations}),
                        "allowed_versions": sorted({p["recipe_version"] for p in observations}),
                        "allowed_pairs": [
                            {"recipe": rid, "version": version}
                            for rid, version in sorted(
                                {(p["recipe_id"], p["recipe_version"]) for p in observations}
                            )
                        ],
                        "requirement": "required_identity"
                        if lane["scope_kind"] == "recipe"
                        else "scope_component_not_mechanism_identity",
                    },
                    "scope": {
                        "kind": lane["scope_kind"],
                        "tolerance": (
                            "same mechanism may have narrower recipe scope; score scope separately"
                        ),
                        "rule": row["scope_rule"],
                        "impact_lots": row["expected_impact_lots"],
                    },
                    "time_window": {
                        "start": min(p["started_at"] for p in observations),
                        "end": max(p["ended_at"] for p in observations),
                        "tolerance_seconds": 0,
                        "interpretation": (
                            "observed positive exposure envelope; not inferred onset/recovery"
                        ),
                        "rule": (
                            "intersection for consistency; excess interval reported as overclaim"
                        ),
                    },
                    "parameter_names": lane["parameter_names"],
                    "constraints_source": (
                        "existing GT Lane + public process rows for source/GT impact Lots"
                    ),
                }
            )
        exposure = (
            "development_calibration"
            if number == 12
            else "high_exposure"
            if number in {2, 9, 20, 23}
            else "normal_development"
        )
        cases.append(
            {
                "case_id": row["case_id"],
                "case_revision": "formal_v1_candidate_r2",
                "ground_truth_class": "partial_source_with_frozen_rubric",
                "exposure_class": exposure,
                "expected_outcome": row["expected_status"],
                "proof_granularity": source["proof_granularity"],
                "scenario_category": row["scenario_category"],
                "scoring_ready": True,
                "source_review_status": row["review_status"],
                "rubric_review_status": "frozen_for_user_review",
                "roots": roots if supported else [],
                "unresolved_alternatives": [] if supported else roots,
                "expected_root_description": row["expected_root_cause"],
                "terminal_rule": (
                    "supported authoritative published identity"
                    if supported
                    else "no published root and authority confirmation_status exactly "
                    + row["expected_status"]
                ),
                "unknown_alias_policy": "indeterminate; model-blinded review, no mapping mutation",
                "quality_primary_eligible": number != 12,
            }
        )
    assert len(cases) == 24
    return {
        "scorer_version": "formal-r2-scorer/1.1",
        "benchmark_version": "development-rca/1.1",
        "source_gt_hash": sha(source_gt),
        "source": "Phase0 inventory + immutable formal r2 public data; no model result input",
        "cases": cases,
        "policy": {
            "negation": "unassessable, never substring match",
            "gt_review": (
                "Pending source review retained; rubric requires user approval before Pilot"
            ),
            "terminal_classes_separate": True,
            "calibration_excluded": ["FORMAL_012"],
            "history_used_as_truth": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-gt", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        json.loads(args.inventory.read_text(encoding="utf-8")),
        args.public,
        json.loads(args.source_gt.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("rubric hash", sha(result), "cases", len(result["cases"]))


if __name__ == "__main__":
    main()
