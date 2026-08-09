"""Record the completed Evaluation V2 domain review checkpoint."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "data" / "evaluation"
QREL_REVIEW_PATH = EVALUATION_DIR / "retrieval_qrel_review_v2.json"
SCENARIO_REVIEW_PATH = EVALUATION_DIR / "rca_scenario_review_v2.json"
EXPECTED_QREL_REVIEWS = 144
EXPECTED_SCENARIO_REVIEWS = 14


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("reviews"), list):
        raise ValueError(f"invalid review artifact: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def record_completed_review(*, reviewer: str, reviewed_at: str) -> tuple[int, int]:
    qrels = _load(QREL_REVIEW_PATH)
    scenarios = _load(SCENARIO_REVIEW_PATH)
    qrel_reviews = qrels["reviews"]
    scenario_reviews = scenarios["reviews"]
    if len(qrel_reviews) != EXPECTED_QREL_REVIEWS:
        raise ValueError(
            f"expected {EXPECTED_QREL_REVIEWS} qrel reviews, found {len(qrel_reviews)}"
        )
    if len(scenario_reviews) != EXPECTED_SCENARIO_REVIEWS:
        raise ValueError(
            "expected "
            f"{EXPECTED_SCENARIO_REVIEWS} scenario reviews, found {len(scenario_reviews)}"
        )

    for review in qrel_reviews:
        review.update(
            {
                "decision": "ACCEPTED",
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "notes": "Domain-reviewed during the Evaluation V2 case-by-case checkpoint.",
            }
        )
    for review in scenario_reviews:
        review.update(
            {
                "decision": "ACCEPTED",
                "reviewer": reviewer,
                "reviewed_at": reviewed_at,
                "notes": "Root cause, Evidence chain, and impact scope reviewed case by case.",
                "root_cause_review": "ACCEPTED",
                "evidence_chain_review": "ACCEPTED",
                "impact_scope_review": "ACCEPTED",
            }
        )

    _write(QREL_REVIEW_PATH, qrels)
    _write(SCENARIO_REVIEW_PATH, scenarios)
    return len(qrel_reviews), len(scenario_reviews)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record an explicitly completed Evaluation V2 human review."
    )
    parser.add_argument("--reviewer", required=True)
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="Required acknowledgement that every qrel and RCA scenario was reviewed.",
    )
    args = parser.parse_args()
    if not args.confirm_reviewed:
        parser.error("--confirm-reviewed is required")
    reviewer = args.reviewer.strip()
    if not reviewer:
        parser.error("--reviewer must not be blank")
    reviewed_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    qrel_count, scenario_count = record_completed_review(
        reviewer=reviewer,
        reviewed_at=reviewed_at,
    )
    print(
        "Evaluation V2 human review recorded: "
        f"qrels={qrel_count}; scenarios={scenario_count}; reviewer={reviewer}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
