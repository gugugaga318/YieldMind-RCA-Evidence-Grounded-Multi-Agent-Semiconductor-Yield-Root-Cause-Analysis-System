"""Run the deterministic Batch 21.2 product-surface evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.autonomous_evaluation import (  # noqa: E402
    evaluate_autonomous_qwen_react,
    render_autonomous_qwen_report,
)
from yield_rca_core.evaluation import EvaluationScenario  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

DEFAULT_AUTONOMOUS_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
DEFAULT_FIXED_CATALOG = ROOT / "data" / "evaluation" / "scenarios.json"
DEFAULT_FIXED_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "autonomous_qwen_react_evaluation"


def load_fixed_scenarios(path: Path) -> list[EvaluationScenario]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvaluationScenario.from_dict(item)
        for item in payload["scenarios"]
    ]


def _real_qwen_status() -> tuple[str, str]:
    key_configured = bool(os.getenv("DASHSCOPE_API_KEY", "").strip())
    opted_in = os.getenv("RUN_REAL_QWEN_TEST") == "1"
    if not key_configured or not opted_in:
        return (
            "SKIPPED",
            "DASHSCOPE_API_KEY and RUN_REAL_QWEN_TEST=1 are not configured.",
        )
    return (
        "SKIPPED",
        (
            "Credentials are configured, but paid calls are isolated from this "
            "deterministic runner; execute tests/integration/test_qwen_optional.py."
        ),
    )


def run_autonomous_evaluation(
    *,
    autonomous_seed_dir: Path,
    fixed_catalog: Path,
    fixed_seed_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    real_qwen_status, real_qwen_reason = _real_qwen_status()
    evaluation = evaluate_autonomous_qwen_react(
        CsvFabRepository(autonomous_seed_dir),
        fixed_repository=CsvFabRepository(fixed_seed_dir),
        fixed_scenarios=load_fixed_scenarios(fixed_catalog),
        real_qwen_status=real_qwen_status,
        real_qwen_reason=real_qwen_reason,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_autonomous_qwen_report(evaluation),
        encoding="utf-8",
    )
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Batch 21.2 product-surface and semantic final evaluation."
    )
    parser.add_argument(
        "--autonomous-seed-dir",
        type=Path,
        default=DEFAULT_AUTONOMOUS_SEED_DIR,
    )
    parser.add_argument(
        "--fixed-catalog",
        type=Path,
        default=DEFAULT_FIXED_CATALOG,
    )
    parser.add_argument(
        "--fixed-seed-dir",
        type=Path,
        default=DEFAULT_FIXED_SEED_DIR,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    evaluation = run_autonomous_evaluation(
        autonomous_seed_dir=args.autonomous_seed_dir,
        fixed_catalog=args.fixed_catalog,
        fixed_seed_dir=args.fixed_seed_dir,
        output_dir=args.output_dir,
    )
    lanes = evaluation["lanes"]
    print(
        "Batch 21.2 deterministic acceptance: "
        f"{'PASS' if evaluation['passed'] else 'FAIL'}; "
        f"autonomous_fake={lanes['autonomous_fake']['status']} "
        f"({lanes['autonomous_fake']['scenario_pass_count']}/"
        f"{lanes['autonomous_fake']['scenario_count']}); "
        f"fixed={lanes['fixed_workflow']['status']} "
        f"({lanes['fixed_workflow']['scenario_pass_count']}/"
        f"{lanes['fixed_workflow']['scenario_count']}); "
        f"real_qwen={lanes['real_qwen_smoke']['status']}"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
