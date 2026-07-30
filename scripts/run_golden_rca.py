"""Run the golden Yield RCA case through the pure Python workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "golden_rca_run"
DEFAULT_QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."

sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.workflow import build_csv_workflow, build_postgres_workflow  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the golden semiconductor Yield RCA workflow end to end."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--job-id", default="GOLDEN_RCA_001")
    parser.add_argument("--plan-id", default="PLAN_GOLDEN_RCA_001")
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--database-url")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--no-print-report",
        action="store_true",
        help="Write the Markdown report without printing its body to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database_url:
        workflow = build_postgres_workflow(args.database_url)
        data_source = "PostgreSQL"
    else:
        seed_dir = args.seed_dir.resolve()
        if not seed_dir.is_dir():
            raise FileNotFoundError(f"golden seed directory does not exist: {seed_dir}")
        workflow = build_csv_workflow(seed_dir)
        data_source = str(seed_dir)

    state = workflow.run(
        args.query,
        job_id=args.job_id,
        plan_id=args.plan_id,
    )
    if state.report is None or not state.hypotheses:
        raise RuntimeError("workflow completed without RCA hypothesis or Report")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "rca_state.json"
    report_path = output_dir / "rca_report.md"
    state_path.write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(state.report.markdown, encoding="utf-8")

    hypothesis = state.hypotheses[-1]
    print("Golden RCA workflow completed")
    print(f"Data source: {data_source}")
    print(f"Job status: {state.job.status}")
    print(f"Completed tasks: {', '.join(state.completed_task_ids)}")
    print(f"Root cause: {hypothesis.root_cause}")
    print(f"Confidence: {hypothesis.confidence:.1%}")
    print(f"RCAState: {state_path}")
    print(f"Markdown report: {report_path}")
    if not args.no_print_report:
        print("\n" + state.report.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
