from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.retrieval_evaluation import (  # noqa: E402
    RankedRetrievalAsset,
    RetrievalEvaluationError,
    RetrievalEvaluationQuery,
    RetrievalGroundTruth,
    RetrievalRanking,
    evaluate_retrieval,
)


def ground_truth() -> RetrievalGroundTruth:
    return RetrievalGroundTruth.from_dict(
        {
            "schema_version": "1.0",
            "corpus_version": "unit-fixture-v1",
            "relevance_threshold": 1,
            "queries": [
                {
                    "query_id": "Q_POSITIVE",
                    "text": "cross-language hard-negative query",
                    "language": "zh",
                    "cross_language": True,
                    "question_kind": "historical_match",
                    "no_answer": False,
                    "hard_negative_asset_ids": ["H"],
                },
                {
                    "query_id": "Q_NO_ANSWER",
                    "text": "unsupported topic",
                    "language": "en",
                    "cross_language": False,
                    "question_kind": "procedure_guidance",
                    "no_answer": True,
                    "hard_negative_asset_ids": [],
                },
            ],
            "qrels": {
                "Q_POSITIVE": [
                    {"asset_id": "A", "relevance": 3},
                    {"asset_id": "B", "relevance": 2},
                    {"asset_id": "C", "relevance": 1},
                    {"asset_id": "H", "relevance": 0},
                ],
                "Q_NO_ANSWER": [],
            },
        }
    )


def hit(asset_id: str, status: str = "CONFIRMED") -> RankedRetrievalAsset:
    return RankedRetrievalAsset(asset_id=asset_id, score=0.9, validation_status=status)


class FixtureBackend:
    name = "FixtureRetriever"

    def __init__(self, rankings: dict[str, RetrievalRanking]) -> None:
        self.rankings = rankings

    def rank(self, query: RetrievalEvaluationQuery) -> RetrievalRanking:
        return self.rankings[query.query_id]


class RetrievalEvaluationMetricTest(unittest.TestCase):
    def test_metrics_match_hand_calculated_rankings(self) -> None:
        backend = FixtureBackend(
            {
                "Q_POSITIVE": RetrievalRanking(
                    candidates=(hit("A"), hit("C"), hit("B"), hit("H"), hit("X")),
                    final_hits=(hit("B"), hit("X"), hit("A"), hit("H")),
                ),
                "Q_NO_ANSWER": RetrievalRanking(candidates=(), final_hits=()),
            }
        )

        evaluation = evaluate_retrieval(
            ground_truth(),
            backend,
            asset_statuses={
                "A": "CONFIRMED",
                "B": "CONFIRMED",
                "C": "CONFIRMED",
                "H": "CONFIRMED",
                "X": "CONFIRMED",
            },
        )

        metrics = evaluation["metrics"]
        self.assertAlmostEqual(metrics["recall_at_5"], 2 / 3, places=6)
        self.assertEqual(metrics["candidate_recall_at_20"], 1.0)
        self.assertEqual(metrics["mrr_at_10"], 1.0)
        self.assertAlmostEqual(metrics["ndcg_at_10"], 0.69202, places=5)
        self.assertAlmostEqual(metrics["cross_language_recall_at_5"], 2 / 3, places=6)
        self.assertEqual(metrics["hard_negative_accuracy"], 1.0)
        self.assertEqual(metrics["no_answer_accuracy"], 1.0)
        self.assertEqual(metrics["no_answer_false_positive_rate"], 0.0)
        self.assertTrue(evaluation["passed"])

    def test_hard_negative_ahead_of_positive_fails_slice(self) -> None:
        backend = FixtureBackend(
            {
                "Q_POSITIVE": RetrievalRanking(
                    candidates=(hit("H"), hit("A"), hit("B"), hit("C")),
                    final_hits=(hit("H"), hit("A"), hit("B"), hit("C")),
                ),
                "Q_NO_ANSWER": RetrievalRanking(candidates=(), final_hits=()),
            }
        )

        evaluation = evaluate_retrieval(
            ground_truth(),
            backend,
            asset_statuses={key: "CONFIRMED" for key in ("A", "B", "C", "H")},
        )

        self.assertEqual(evaluation["metrics"]["hard_negative_accuracy"], 0.0)
        positive = evaluation["results"][0]
        self.assertEqual(positive["hard_negatives_ahead"], ["H"])

    def test_unapproved_candidate_fails_release_gate_even_when_not_final(self) -> None:
        backend = FixtureBackend(
            {
                "Q_POSITIVE": RetrievalRanking(
                    candidates=(hit("A"), hit("DRAFT", "DRAFT"), hit("B"), hit("C")),
                    final_hits=(hit("A"), hit("B"), hit("C")),
                ),
                "Q_NO_ANSWER": RetrievalRanking(candidates=(), final_hits=()),
            }
        )

        evaluation = evaluate_retrieval(
            ground_truth(),
            backend,
            asset_statuses={
                "A": "CONFIRMED",
                "B": "CONFIRMED",
                "C": "CONFIRMED",
                "H": "CONFIRMED",
                "DRAFT": "DRAFT",
            },
        )

        self.assertFalse(evaluation["passed"])
        self.assertEqual(evaluation["metrics"]["unapproved_asset_ids"], ["DRAFT"])

    def test_duplicate_logical_asset_id_is_a_contract_error(self) -> None:
        backend = FixtureBackend(
            {
                "Q_POSITIVE": RetrievalRanking(
                    candidates=(hit("A"), hit("A")),
                    final_hits=(hit("A"),),
                ),
                "Q_NO_ANSWER": RetrievalRanking(candidates=(), final_hits=()),
            }
        )

        with self.assertRaisesRegex(RetrievalEvaluationError, "duplicate logical asset"):
            evaluate_retrieval(
                ground_truth(),
                backend,
                asset_statuses={
                    "A": "CONFIRMED",
                    "B": "CONFIRMED",
                    "C": "CONFIRMED",
                    "H": "CONFIRMED",
                },
            )

    def test_no_answer_false_positive_is_not_hidden_by_score_threshold(self) -> None:
        backend = FixtureBackend(
            {
                "Q_POSITIVE": RetrievalRanking(
                    candidates=(hit("A"), hit("B"), hit("C"), hit("H")),
                    final_hits=(hit("A"), hit("B"), hit("C")),
                ),
                "Q_NO_ANSWER": RetrievalRanking(
                    candidates=(RankedRetrievalAsset("X", 0.01),),
                    final_hits=(RankedRetrievalAsset("X", 0.01),),
                ),
            }
        )

        evaluation = evaluate_retrieval(
            ground_truth(),
            backend,
            asset_statuses={key: "CONFIRMED" for key in ("A", "B", "C", "H", "X")},
        )

        self.assertEqual(evaluation["metrics"]["no_answer_accuracy"], 0.0)
        self.assertEqual(evaluation["metrics"]["no_answer_false_positive_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
