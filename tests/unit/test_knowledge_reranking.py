from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.hybrid_retrieval import (  # noqa: E402
    BM25DocumentChunkRetriever,
    PythonBM25CandidateSource,
)
from yield_rca_core.knowledge_models import (  # noqa: E402
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.reranking import (  # noqa: E402
    PlattScoreCalibrator,
    RerankedKnowledgeRetriever,
    ScoreCalibrationArtifact,
    fit_platt_score_calibration,
)

CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"


class FixtureReranker:
    model_name = "fixture-reranker"
    model_revision = "fixture-v1"
    device = "cpu"

    def score_logits(self, query: str, documents: Sequence[str]) -> tuple[float, ...]:
        del query
        return tuple(8.0 if "random scratch burst" in item else -2.0 for item in documents)


def lookup_plan() -> KnowledgeLookupPlan:
    kind = KnowledgeQuestionKind.HISTORICAL_MATCH
    return KnowledgeLookupPlan(
        intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
        question_kind=kind.value,
        query="Cu CMP scratch",
        allowed_document_types=(kind.document_type,),
        reason="unit test",
        module="Cu CMP",
        equipment_type="CMP",
        top_k=3,
    )


class KnowledgeRerankingTest(unittest.TestCase):
    def setUp(self) -> None:
        store = load_builtin_knowledge_store(CORPUS)
        self.base = BM25DocumentChunkRetriever(PythonBM25CandidateSource(store))

    def test_reranker_changes_order_and_keeps_stage_scores_separate(self) -> None:
        result = RerankedKnowledgeRetriever(
            self.base,
            FixtureReranker(),
        ).retrieve(lookup_plan(), lookup_id="KLOOK_RERANK")

        self.assertEqual(result[0].document.evaluation_asset_id, "RCA_SYN_004")
        self.assertIn("lexical", result[0].score_components)
        self.assertIn("reranker", result[0].score_components)
        self.assertIsNone(result[0].calibrated_relevance)
        self.assertEqual(result[0].source_confidence, 0.8)
        self.assertTrue(result[0].retrieval_strategy.endswith("+cross_encoder"))

    def test_matching_calibration_artifact_populates_calibrated_relevance(self) -> None:
        artifact = ScoreCalibrationArtifact(
            schema_version="1.0",
            calibrator="platt_logistic",
            model_name="fixture-reranker",
            model_revision="fixture-v1",
            slope=0.5,
            intercept=-0.2,
            calibration_query_ids=("Q1",),
            training_pair_count=4,
        )
        calibrator = PlattScoreCalibrator(
            artifact,
            model_name="fixture-reranker",
            model_revision="fixture-v1",
        )
        result = RerankedKnowledgeRetriever(
            self.base,
            FixtureReranker(),
            calibrator=calibrator,
        ).retrieve(lookup_plan(), lookup_id="KLOOK_CALIBRATED")

        self.assertIsNotNone(result[0].calibrated_relevance)
        self.assertNotEqual(
            result[0].calibrated_relevance,
            result[0].score_components["reranker"],
        )

    def test_artifact_loader_rejects_model_mismatch(self) -> None:
        payload = {
            "schema_version": "1.0",
            "calibrator": "platt_logistic",
            "model_name": "fixture-reranker",
            "model_revision": "fixture-v1",
            "slope": 1.0,
            "intercept": 0.0,
            "calibration_query_ids": ["Q1"],
            "training_pair_count": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            artifact = ScoreCalibrationArtifact.load(path)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            PlattScoreCalibrator(
                artifact,
                model_name="other-model",
                model_revision="fixture-v1",
            )

    def test_platt_fit_uses_independent_labeled_pairs(self) -> None:
        artifact = fit_platt_score_calibration(
            (-4.0, -2.0, 2.0, 4.0),
            (0, 0, 1, 1),
            model_name="fixture-reranker",
            model_revision="fixture-v1",
            calibration_query_ids=("Q_CAL_1", "Q_CAL_2"),
        )

        self.assertGreater(artifact.slope, 0)
        self.assertEqual(artifact.training_pair_count, 4)
        calibrator = PlattScoreCalibrator(
            artifact,
            model_name="fixture-reranker",
            model_revision="fixture-v1",
        )
        self.assertLess(calibrator.calibrate_logit(-2), 0.5)
        self.assertGreater(calibrator.calibrate_logit(2), 0.5)


if __name__ == "__main__":
    unittest.main()
