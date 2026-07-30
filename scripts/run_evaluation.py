"""Run the deterministic Step 14 offline evaluation suite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.evaluation import (  # noqa: E402
    EvaluationScenario,
    evaluate_scenarios,
    render_evaluation_report,
)
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

DEFAULT_CATALOG = ROOT / "data" / "evaluation" / "scenarios.json"
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "multi_case"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "evaluation"


def load_scenarios(path: Path) -> list[EvaluationScenario]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return [EvaluationScenario.from_dict(item) for item in payload["scenarios"]]


def run_evaluation(catalog: Path, seed_dir: Path, output_dir: Path) -> dict[str, Any]:
    scenarios = load_scenarios(catalog)
    evaluation = evaluate_scenarios(CsvFabRepository(seed_dir), scenarios)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(evaluation, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_evaluation_report(evaluation),
        encoding="utf-8",
    )
    return evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Step 14 offline RCA evaluation.")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    evaluation = run_evaluation(args.catalog, args.seed_dir, args.output_dir)
    metrics = evaluation["metrics"]
    print(
        "Step 14 evaluation: "
        f"{'PASS' if evaluation['passed'] else 'FAIL'}; "
        f"scenarios={metrics['scenario_count']}; "
        f"pass_rate={metrics['scenario_pass_rate']:.1%}; "
        f"top1={metrics['top1_root_cause_accuracy']:.1%}; "
        f"top3={metrics['top3_recall']:.1%}; "
        f"inconclusive={metrics['inconclusive_handling_rate']:.1%}; "
        f"hallucinated_citations={metrics['hallucinated_citation_rate']:.1%}; "
        f"tool_p95={metrics['tool_latency_ms']['p95']:.3f}ms; "
        f"e2e_p95={metrics['end_to_end_latency_ms']['p95']:.3f}ms"
    )
    print(f"Results: {args.output_dir / 'results.json'}")
    print(f"Report:  {args.output_dir / 'report.md'}")
    return 0 if evaluation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
