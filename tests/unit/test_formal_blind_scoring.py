from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from score_formal_blind_rca import (  # noqa: E402
    _brier_score,
    _root_cause_component_matches,
    build_parser,
    score_formal_blind,
)
from yield_rca_core.models import (  # noqa: E402
    Evidence,
    Hypothesis,
    RCAJob,
    RCAState,
    TaskStatus,
)


def test_structured_root_cause_accepts_semantic_aliases_not_exact_sentence() -> None:
    matches = _root_cause_component_matches(
        (
            "Backside pressure CV excursion in chamber EQ_D509B8_CH01 during "
            "operation 4000 caused non-uniform copper fill."
        ),
        {
            "equipment": ["EQ_D509B8"],
            "chamber": ["EQ_D509B8_CH01"],
            "operation": ["operation 4000", "OP4000"],
            "mechanism": ["non-uniform copper fill", "copper fill nonuniformity"],
            "abnormal_parameters": ["backside pressure CV"],
        },
    )

    assert matches is not None
    assert all(matches.values())


def test_structured_root_cause_reports_the_missing_mechanism_component() -> None:
    matches = _root_cause_component_matches(
        "EQ_D509B8_CH01 operation 4000 backside pressure CV excursion.",
        {
            "equipment": ["EQ_D509B8"],
            "chamber": ["EQ_D509B8_CH01"],
            "operation": ["operation 4000"],
            "mechanism": ["center seam void"],
            "abnormal_parameters": ["backside pressure CV"],
        },
    )

    assert matches is not None
    assert matches["equipment"] is True
    assert matches["mechanism"] is False


def test_brier_score_penalizes_overconfident_wrong_confirmation() -> None:
    score = _brier_score(
        [
            {"confirmation_probability": 0.9, "expected_supported": False},
            {"confirmation_probability": 0.8, "expected_supported": True},
        ]
    )

    assert score == 0.425


def test_scoring_paths_are_explicit_and_have_no_v1_answer_default() -> None:
    parser = build_parser()

    assert parser.get_default("run_dir") is None
    assert parser.get_default("ground_truth") is None
    assert parser.get_default("output_dir") is None


def test_two_layer_score_joins_frozen_run_and_structured_truth() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        workspace = Path(temporary)
        public_dir = workspace / "public"
        run_dir = workspace / "run"
        state_dir = run_dir / "states"
        output_dir = workspace / "score"
        public_dir.mkdir()
        state_dir.mkdir(parents=True)
        public_file = public_dir / "cases.json"
        public_file.write_text("{}\n", encoding="utf-8")

        evidence = Evidence(
            evidence_id="EV_TEST",
            source_type="system",
            source_id="TEST",
            summary="Scoring fixture Evidence.",
        )
        state = RCAState(
            job=RCAJob(
                job_id="JOB_TEST",
                user_query="Investigate fixture.",
                status=TaskStatus.COMPLETED.value,
            ),
            impact_lots=["LOT_IMPACT"],
            evidence=[evidence],
            hypotheses=[
                Hypothesis(
                    hypothesis_id="HYP_TEST",
                    root_cause=(
                        "EQ_01 chamber CH_01 operation 4000 backside pressure CV "
                        "caused center seam void"
                    ),
                    confidence=0.8,
                    evidence_ids=[evidence.evidence_id],
                    status="supported",
                )
            ],
            execution_metadata={"orchestration_mode": "llm_react"},
        )
        (state_dir / "FORMAL_TEST.json").write_text(
            json.dumps(state.to_dict()), encoding="utf-8"
        )
        public_hash = hashlib.sha256(public_file.read_bytes()).hexdigest().upper()
        (run_dir / "run_manifest.json").write_text(
            json.dumps(
                {
                    "dataset_id": "formal-score-test",
                    "evaluation_role": "development_regression",
                    "input_boundary": {
                        "mode": "public_only",
                        "public_dir": str(public_dir),
                        "allowed_files": [
                            {
                                "path": "cases.json",
                                "sha256": public_hash,
                                "bytes": public_file.stat().st_size,
                            }
                        ],
                        "ground_truth_loaded": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "run_results.json").write_text(
            json.dumps(
                {
                    "dataset_id": "formal-score-test",
                    "failed_case_count": 0,
                    "strict_qwen_acceptance_evaluated": True,
                    "strict_qwen_rejected_case_count": 0,
                    "execution_layer": {"strict_qwen_acceptance_rate": 1.0},
                    "results": [
                        {
                            "case_id": "FORMAL_TEST",
                            "state_file": "states/FORMAL_TEST.json",
                            "error": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        truth_path = workspace / "ground_truth.json"
        truth_path.write_text(
            json.dumps(
                {
                    "dataset_id": "formal-score-test",
                    "evaluation_role": "development_regression",
                    "cases": [
                        {
                            "case_id": "FORMAL_TEST",
                            "expected_status": "supported",
                            "expected_root_cause": {
                                "equipment": ["EQ_01"],
                                "chamber": ["CH_01"],
                                "operation": ["operation 4000"],
                                "mechanism": ["center seam void"],
                                "abnormal_parameters": ["backside pressure CV"],
                            },
                            "expected_impact_lots": ["LOT_IMPACT"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        score = score_formal_blind(
            run_dir=run_dir,
            ground_truth_path=truth_path,
            output_dir=output_dir,
        )

        assert score["execution_layer"]["passed"] is True
        assert score["rca_quality_layer"]["passed"] is True
        assert score["metrics"]["root_cause_structured_accuracy"] == 1.0
        assert score["metrics"]["impact_lot_f1"] == 1.0
        assert score["metrics"]["brier_score"] == 0.04
