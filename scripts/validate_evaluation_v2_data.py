"""Validate committed Evaluation V2 data without running quality evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from yield_rca_core.evaluation_v2_data import (  # noqa: E402
    TemplateSurfaceQueryProvider,
    build_evaluation_v2_dataset,
    data_quality_markdown,
    load_incident_catalog,
    validate_evaluation_v2_dataset,
)

EVALUATION_DIR = ROOT / "data" / "evaluation"
REPORT_DIR = ROOT / "outputs" / "evaluation_v2_data_quality"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    catalog = load_incident_catalog(EVALUATION_DIR / "incident_families_v2.json")
    built = build_evaluation_v2_dataset(catalog, TemplateSurfaceQueryProvider())
    # Validate the versioned review decisions, not freshly generated PENDING records.
    built["qrel_review"] = _load(EVALUATION_DIR / "retrieval_qrel_review_v2.json")
    built["scenario_review"] = _load(EVALUATION_DIR / "rca_scenario_review_v2.json")
    report = validate_evaluation_v2_dataset(built)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "results.json").write_text(
        json.dumps(
            {
                "structural_pass": report.structural_pass,
                "human_review_complete": report.human_review_complete,
                "metrics": report.metrics,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (REPORT_DIR / "report.md").write_text(data_quality_markdown(report), encoding="utf-8")
    print(
        "Evaluation V2 committed-data validation: "
        f"structural={'PASS' if report.structural_pass else 'FAIL'}; "
        f"human_review={'COMPLETE' if report.human_review_complete else 'PENDING'}"
    )
    return 0 if report.structural_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
