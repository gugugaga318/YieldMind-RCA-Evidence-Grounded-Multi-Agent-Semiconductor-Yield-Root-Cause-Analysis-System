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

DEFAULT_RUN_DIR = ROOT / "outputs" / "formal_blind_v1" / "controlled_react_run"
DEFAULT_GROUND_TRUTH = (
    ROOT / ".blind_evaluation" / "formal_v1_candidate_r2" / "hidden" / "ground_truth.json"
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
    if manifest.get("dataset_id") != truth.get("dataset_id"):
        raise ValueError("run and Ground Truth dataset_id values differ")
    if execution.get("dataset_id") != truth.get("dataset_id"):
        raise ValueError("execution and Ground Truth dataset_id values differ")

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
        status_correct = (
            actual_status == "supported" if supported else actual_status == "inconclusive"
        )
        root_cause_correct = (
            actual_root_cause == expected["expected_root_cause"] if supported else None
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
                "expected_root_cause": expected["expected_root_cause"],
                "actual_root_cause": actual_root_cause,
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
                "passed": row_passed,
            }
        )

    supported_rows = [row for row in rows if row["expected_status"] == "supported"]
    abstention_rows = [row for row in rows if row["expected_status"] != "supported"]
    fallback_rows = [row for row in rows if row["fallback_reason"]]
    metrics = {
        "case_count": len(rows),
        "case_pass_count": sum(bool(row["passed"]) for row in rows),
        "case_pass_rate": _ratio(sum(bool(row["passed"]) for row in rows), len(rows)),
        "completion_rate": _ratio(
            sum(bool(row["job_completed"]) for row in rows), len(rows)
        ),
        "supported_case_count": len(supported_rows),
        "supported_root_cause_correctness": _ratio(
            sum(bool(row["root_cause_correct"]) for row in supported_rows),
            len(supported_rows),
        ),
        "impact_lot_precision": _ratio(impact_tp, impact_tp + impact_fp),
        "impact_lot_recall": _ratio(impact_tp, impact_tp + impact_fn),
        "impact_scope_exact_rate": _ratio(
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
        "actual_mode_counts": {
            mode: sum(row["actual_orchestration_mode"] == mode for row in rows)
            for mode in sorted(
                {str(row["actual_orchestration_mode"]) for row in rows}
            )
        },
    }
    score = {
        "schema_version": "1.0",
        "dataset_id": truth["dataset_id"],
        "run_kind": "formal_rca_blind_score",
        "run_dir": str(run_dir),
        "ground_truth_sha256": _sha256(ground_truth_path.resolve()),
        "public_snapshot_errors": snapshot_errors,
        "metrics": metrics,
        "results": rows,
        "passed": not snapshot_errors and all(bool(row["passed"]) for row in rows),
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
    lines = [
        "# Formal RCA Blind Score",
        "",
        f"- Dataset: `{score['dataset_id']}`",
        f"- Score: **{'PASS' if score['passed'] else 'FAIL'}**",
        f"- Case pass rate: {metrics['case_pass_rate']:.1%}",
        f"- Supported root-cause correctness: {metrics['supported_root_cause_correctness']:.1%}",
        "- Impact precision / recall: "
        f"{metrics['impact_lot_precision']:.1%} / {metrics['impact_lot_recall']:.1%}",
        f"- Correct abstention: {metrics['correct_abstention_rate']:.1%}",
        f"- Abstention with zero confirmed impacts: {metrics['abstention_zero_impact_rate']:.1%}",
        f"- Public snapshot errors: {len(snapshot_errors)}",
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return score


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "formal_blind_v1" / "controlled_react_score",
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
