"""Compare fixed and controlled-ReAct RCA results for the Scratch/Cu CMP scenario."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import RCAState  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402


def summary(state: RCAState) -> dict[str, Any]:
    hypothesis = state.hypotheses[0] if state.hypotheses else None
    return {
        "root_cause": hypothesis.root_cause if hypothesis else None,
        "hypothesis_status": hypothesis.status if hypothesis else None,
        "evidence_count": len(state.evidence),
        "tool_call_count": state.execution_metadata.get("tool_call_count", 0),
        "action_history": [record.action.kind for record in state.action_history],
        "conclusion_level": state.conclusion_level,
        "stop_reason": state.stop_reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "controlled_react_shadow.json",
    )
    args = parser.parse_args()
    query = "Investigate LOT_A_001 scratch in Cu CMP and identify root cause."
    seed_dir = ROOT / "data" / "seeds" / "golden_case"
    fixed = build_csv_workflow(seed_dir).run(query, job_id="JOB_SHADOW_FIXED", lot_id="LOT_A_001")
    controlled = build_csv_workflow(seed_dir, orchestration_mode="controlled_react").run(
        query,
        job_id="JOB_SHADOW_CONTROLLED",
        lot_id="LOT_A_001",
    )
    fixed_summary = summary(fixed)
    controlled_summary = summary(controlled)
    result = {
        "pass": (
            fixed_summary["root_cause"] == controlled_summary["root_cause"]
            and controlled_summary["conclusion_level"] == "supported"
            and "validate_shared_defect_pattern" in controlled_summary["action_history"]
        ),
        "fixed": fixed_summary,
        "controlled_react": controlled_summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
