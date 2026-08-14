"""Score a completed formal blind RCA run in a separate Ground Truth step.

The companion runner never accepts a Ground Truth path.  This script is the
only formal-blind command that may read the private answer file, and it
requires an explicit command-line acknowledgement before doing so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.models import RCAState, TaskStatus  # noqa: E402

ROOT_CAUSE_COMPONENTS = (
    "equipment",
    "chamber",
    "operation",
    "mechanism",
    "abnormal_parameters",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _validate_public_snapshot(manifest: dict[str, Any]) -> list[str]:
    boundary = manifest.get("input_boundary")
    if not isinstance(boundary, dict) or boundary.get("mode") != "public_only":
        return ["run manifest does not declare a public_only input boundary"]
    if boundary.get("ground_truth_loaded") is not False:
        return ["run manifest does not prove Ground Truth was excluded"]
    public_dir = Path(str(boundary.get("public_dir", "")))
    entries = boundary.get("allowed_files")
    if not public_dir.is_dir() or not isinstance(entries, list):
        return ["public snapshot is incomplete"]
    errors: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("invalid public snapshot entry")
            continue
        relative = Path(str(entry.get("path", "")))
        candidate = (public_dir / relative).resolve()
        if public_dir.resolve() not in candidate.parents or not candidate.is_file():
            errors.append(f"public snapshot file missing: {relative}")
            continue
        if _sha256(candidate) != entry.get("sha256"):
            errors.append(f"public snapshot hash changed: {relative}")
    return errors


def _state_path(run_dir: Path, relative: str) -> Path:
    path = (run_dir / relative).resolve()
    if run_dir.resolve() not in path.parents:
        raise ValueError(f"state file escapes run directory: {relative}")
    return path


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _f1(precision: float, recall: float) -> float:
    return (
        round(2 * precision * recall / (precision + recall), 6)
        if precision + recall
        else 0.0
    )


def _normalized(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _root_cause_component_matches(
    actual_root_cause: str | None,
    expected_root_cause: object,
) -> dict[str, bool] | None:
    """Score semantic fields without requiring one exact generated sentence."""

    if not isinstance(expected_root_cause, dict):
        return None
    if set(expected_root_cause) != set(ROOT_CAUSE_COMPONENTS):
        raise ValueError(
            "structured expected_root_cause must contain exactly "
            f"{list(ROOT_CAUSE_COMPONENTS)}"
        )
    normalized_actual = _normalized(actual_root_cause or "")
    matches: dict[str, bool] = {}
    for component in ROOT_CAUSE_COMPONENTS:
        aliases = expected_root_cause[component]
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError(
                f"expected_root_cause.{component} must be an array of non-empty aliases"
            )
        normalized_aliases = [_normalized(alias) for alias in aliases]
        matches[component] = not aliases or any(
            alias in normalized_actual for alias in normalized_aliases
        )
    return matches


def _brier_score(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return round(
        sum(
            (float(row["confirmation_probability"]) - float(row["expected_supported"]))
            ** 2
            for row in rows
        )
        / len(rows),
        6,
    )


def score_formal_blind(
    *,
    run_dir: Path,
    ground_truth_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare a finished public-only run with private Ground Truth."""

    run_dir = run_dir.resolve()
    manifest = _load_object(run_dir / "run_manifest.json")
    execution = _load_object(run_dir / "run_results.json")
    truth = _load_object(ground_truth_path.resolve())
    evaluation_role = str(
        manifest.get("evaluation_role", "development_regression")
    )
    if manifest.get("dataset_id") != truth.get("dataset_id"):
        raise ValueError("run and Ground Truth dataset_id values differ")
    if execution.get("dataset_id") != truth.get("dataset_id"):
        raise ValueError("execution and Ground Truth dataset_id values differ")
    truth_role = str(truth.get("evaluation_role", evaluation_role))
    if truth_role != evaluation_role:
        raise ValueError("run and Ground Truth evaluation_role values differ")
    if evaluation_role == "sealed_blind":
        governance = manifest.get("governance")
        required_governance = {
            "dataset_generation_independent": True,
            "ground_truth_custodian": "external_agent",
            "sealed_before_execution": True,
            "development_agent_ground_truth_access": False,
            "ground_truth_sha256_commitment": _sha256(ground_truth_path.resolve()),
        }
        if not isinstance(governance, dict) or governance != required_governance:
            raise ValueError("sealed-blind run governance declaration is invalid")
        if not manifest.get("code_commit") or manifest.get("code_worktree_clean") is not True:
            raise ValueError("sealed-blind run did not use a clean committed code snapshot")

    snapshot_errors = _validate_public_snapshot(manifest)
    truth_by_id = {str(item["case_id"]): item for item in truth.get("cases", [])}
    results_by_id = {str(item["case_id"]): item for item in execution.get("results", [])}
    if len(truth_by_id) != len(truth.get("cases", [])):
        raise ValueError("Ground Truth case_id values are not unique")
    if set(truth_by_id) != set(results_by_id):
        raise ValueError("run results do not cover exactly the Ground Truth cases")

    rows: list[dict[str, Any]] = []
    impact_tp = impact_fp = impact_fn = 0
    for case_id in sorted(truth_by_id):
        expected = truth_by_id[case_id]
        result = results_by_id[case_id]
        expected_status = str(expected["expected_status"])
        if expected_status not in {"supported", "inconclusive"}:
            raise ValueError(
                "expected_status must be supported or inconclusive"
            )
        expected_impacts = {str(item) for item in expected.get("expected_impact_lots", [])}
        state_error = result.get("error")
        state: RCAState | None = None
        if state_error is None and isinstance(result.get("state_file"), str):
            state = RCAState.from_dict(
                _load_object(_state_path(run_dir, str(result["state_file"])))
            )
        hypothesis = state.hypotheses[-1] if state and state.hypotheses else None
        actual_status = hypothesis.status if hypothesis else None
        actual_root_cause = hypothesis.root_cause if hypothesis else None
        actual_impacts = set(state.impact_lots) if state else set()
        supported = expected_status == "supported"
        actual_supported = actual_status == "supported"
        status_correct = actual_status == expected_status
        structured_matches = _root_cause_component_matches(
            actual_root_cause,
            expected["expected_root_cause"],
        )
        if evaluation_role == "sealed_blind" and structured_matches is None:
            raise ValueError(
                "sealed_blind Ground Truth requires a structured expected_root_cause"
            )
        component_matches = structured_matches if supported else None
        root_cause_correct = (
            (
                all(component_matches.values())
                if component_matches is not None
                else actual_root_cause == expected["expected_root_cause"]
            )
            if supported
            else None
        )
        impact_exact = actual_impacts == expected_impacts
        impact_tp += len(actual_impacts & expected_impacts)
        impact_fp += len(actual_impacts - expected_impacts)
        impact_fn += len(expected_impacts - actual_impacts)
        completed = bool(state and state.job.status == TaskStatus.COMPLETED.value)
        actual_mode = (
            state.execution_metadata.get("orchestration_mode") if state else None
        )
        row_passed = all(
            (
                completed,
                status_correct,
                bool(root_cause_correct) if supported else True,
                impact_exact,
            )
        )
        rows.append(
            {
                "case_id": case_id,
                "expected_status": expected_status,
                "actual_status": actual_status,
                "status_correct": status_correct,
                "expected_root_cause": expected["expected_root_cause"],
                "actual_root_cause": actual_root_cause,
                "root_cause_component_matches": component_matches,
                "root_cause_correct": root_cause_correct,
                "expected_impact_lots": sorted(expected_impacts),
                "actual_impact_lots": sorted(actual_impacts),
                "impact_exact": impact_exact,
                "impact_true_positive": len(actual_impacts & expected_impacts),
                "impact_false_positive": len(actual_impacts - expected_impacts),
                "impact_false_negative": len(expected_impacts - actual_impacts),
                "job_completed": completed,
                "actual_orchestration_mode": actual_mode,
                "fallback_reason": (
                    state.execution_metadata.get("orchestration_fallback_reason")
                    if state
                    else None
                ),
                "run_error": state_error,
                "expected_supported": supported,
                "actual_supported": actual_supported,
                "confirmation_probability": (
                    float(hypothesis.confidence) if hypothesis is not None else 0.0
                ),
                "passed": row_passed,
            }
        )

    supported_rows = [row for row in rows if row["expected_status"] == "supported"]
    abstention_rows = [row for row in rows if row["expected_status"] != "supported"]
    fallback_rows = [row for row in rows if row["fallback_reason"]]
    supported_tp = sum(
        row["expected_supported"] and row["actual_supported"] for row in rows
    )
    supported_fp = sum(
        not row["expected_supported"] and row["actual_supported"] for row in rows
    )
    supported_fn = sum(
        row["expected_supported"] and not row["actual_supported"] for row in rows
    )
    supported_tn = sum(
        not row["expected_supported"] and not row["actual_supported"] for row in rows
    )
    impact_precision = _ratio(impact_tp, impact_tp + impact_fp)
    impact_recall = _ratio(impact_tp, impact_tp + impact_fn)
    component_rows = [
        row
        for row in supported_rows
        if isinstance(row["root_cause_component_matches"], dict)
    ]
    component_metrics = {
        component: _ratio(
            sum(
                bool(row["root_cause_component_matches"][component])
                for row in component_rows
            ),
            len(component_rows),
        )
        for component in ROOT_CAUSE_COMPONENTS
    }
    rca_quality_layer = {
        "case_count": len(rows),
        "case_pass_count": sum(bool(row["passed"]) for row in rows),
        "case_pass_rate": _ratio(sum(bool(row["passed"]) for row in rows), len(rows)),
        "completion_rate": _ratio(
            sum(bool(row["job_completed"]) for row in rows), len(rows)
        ),
        "supported_case_count": len(supported_rows),
        "status_accuracy": _ratio(
            sum(bool(row["status_correct"]) for row in rows), len(rows)
        ),
        "supported_precision": _ratio(supported_tp, supported_tp + supported_fp),
        "supported_recall": _ratio(supported_tp, supported_tp + supported_fn),
        "inconclusive_recall": _ratio(supported_tn, supported_tn + supported_fp),
        "overconfirmation_count": supported_fp,
        "overconfirmation_rate": _ratio(supported_fp, supported_tn + supported_fp),
        "underconfirmation_count": supported_fn,
        "underconfirmation_rate": _ratio(supported_fn, supported_tp + supported_fn),
        "supported_root_cause_correctness": _ratio(
            sum(bool(row["root_cause_correct"]) for row in supported_rows),
            len(supported_rows),
        ),
        "structured_root_cause_case_count": len(component_rows),
        "root_cause_component_accuracy": component_metrics,
        "structured_root_cause_exact_rate": _ratio(
            sum(
                all(row["root_cause_component_matches"].values())
                for row in component_rows
            ),
            len(component_rows),
        ),
        "root_cause_structured_accuracy": _ratio(
            sum(
                all(row["root_cause_component_matches"].values())
                for row in component_rows
            ),
            len(component_rows),
        ),
        "impact_lot_precision": impact_precision,
        "impact_lot_recall": impact_recall,
        "impact_lot_f1": _f1(impact_precision, impact_recall),
        "impact_scope_exact_rate": _ratio(
            sum(bool(row["impact_exact"]) for row in rows), len(rows)
        ),
        "impact_lot_exact_rate": _ratio(
            sum(bool(row["impact_exact"]) for row in rows), len(rows)
        ),
        "abstention_case_count": len(abstention_rows),
        "correct_abstention_rate": _ratio(
            sum(row["actual_status"] == "inconclusive" for row in abstention_rows),
            len(abstention_rows),
        ),
        "abstention_zero_impact_rate": _ratio(
            sum(not row["actual_impact_lots"] for row in abstention_rows),
            len(abstention_rows),
        ),
        "fallback_case_count": len(fallback_rows),
        "brier_score": _brier_score(rows),
        "actual_mode_counts": {
            mode: sum(row["actual_orchestration_mode"] == mode for row in rows)
            for mode in sorted(
                {str(row["actual_orchestration_mode"]) for row in rows}
            )
        },
    }
    execution_layer = dict(execution.get("execution_layer", {}))
    execution_layer_passed = bool(
        execution.get("failed_case_count") == 0
        and (
            not execution.get("strict_qwen_acceptance_evaluated")
            or execution.get("strict_qwen_rejected_case_count") == 0
        )
    )
    rca_quality_passed = all(bool(row["passed"]) for row in rows)
    score = {
        "schema_version": "2.0",
        "dataset_id": truth["dataset_id"],
        "run_kind": "formal_rca_blind_score",
        "evaluation_role": evaluation_role,
        "run_dir": str(run_dir),
        "ground_truth_sha256": _sha256(ground_truth_path.resolve()),
        "public_snapshot_errors": snapshot_errors,
        "execution_layer": {
            "passed": execution_layer_passed,
            "metrics": execution_layer,
            "note": (
                "This layer evaluates runtime integrity and does not score "
                "RCA correctness."
            ),
        },
        "rca_quality_layer": {
            "passed": rca_quality_passed,
            "metrics": rca_quality_layer,
            "note": (
                "This layer is computed only after the sealed run is joined "
                "with Ground Truth."
            ),
        },
        "metrics": rca_quality_layer,
        "results": rows,
        "passed": not snapshot_errors and execution_layer_passed and rca_quality_passed,
        "limitations": (
            "Synthetic benchmark result. It evaluates this frozen public packet, "
            "not production-fab performance."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics = rca_quality_layer
    lines = [
        "# Formal RCA Blind Score",
        "",
        f"- Dataset: `{score['dataset_id']}`",
        f"- Score: **{'PASS' if score['passed'] else 'FAIL'}**",
        f"- Case pass rate: {metrics['case_pass_rate']:.1%}",
        f"- Execution layer: **{'PASS' if execution_layer_passed else 'FAIL'}**",
        f"- RCA quality layer: **{'PASS' if rca_quality_passed else 'FAIL'}**",
        f"- Status accuracy: {metrics['status_accuracy']:.1%}",
        "- Supported precision / recall: "
        f"{metrics['supported_precision']:.1%} / {metrics['supported_recall']:.1%}",
        f"- Inconclusive recall: {metrics['inconclusive_recall']:.1%}",
        f"- Supported root-cause correctness: {metrics['supported_root_cause_correctness']:.1%}",
        "- Impact precision / recall: "
        f"{metrics['impact_lot_precision']:.1%} / {metrics['impact_lot_recall']:.1%}",
        f"- Impact F1 / exact: {metrics['impact_lot_f1']:.1%} / "
        f"{metrics['impact_scope_exact_rate']:.1%}",
        f"- Brier score: {metrics['brier_score']:.4f}",
        f"- Over/under-confirmation: {metrics['overconfirmation_rate']:.1%} / "
        f"{metrics['underconfirmation_rate']:.1%}",
        f"- Correct abstention: {metrics['correct_abstention_rate']:.1%}",
        f"- Abstention with zero confirmed impacts: {metrics['abstention_zero_impact_rate']:.1%}",
        f"- Public snapshot errors: {len(snapshot_errors)}",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return score


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    parser.add_argument("--confirm-ground-truth-access", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.confirm_ground_truth_access:
        parser = build_parser()
        parser.error(
            "--confirm-ground-truth-access is required after the public-only run finishes"
        )
    score = score_formal_blind(
        run_dir=args.run_dir,
        ground_truth_path=args.ground_truth,
        output_dir=args.output_dir,
    )
    metrics = score["metrics"]
    print(
        "Formal blind score: "
        f"{'PASS' if score['passed'] else 'FAIL'}; "
        f"cases={metrics['case_pass_count']}/{metrics['case_count']}; "
        f"supported_root={metrics['supported_root_cause_correctness']:.1%}; "
        f"impact_precision={metrics['impact_lot_precision']:.1%}; "
        f"impact_recall={metrics['impact_lot_recall']:.1%}; "
        f"abstention={metrics['correct_abstention_rate']:.1%}"
    )
    print(f"Results: {args.output_dir.resolve() / 'results.json'}")
    return 0 if score["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
