from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evaluation_v2_data import (  # noqa: E402
    TemplateSurfaceQueryProvider,
    build_evaluation_v2_dataset,
    load_incident_catalog,
    validate_evaluation_v2_dataset,
)

EVALUATION_DIR = ROOT / "data" / "evaluation"
KNOWLEDGE_DIR = ROOT / "data" / "knowledge" / "synthetic_v2"


def _load(path: Path):  # type: ignore[no-untyped-def]
    return json.loads(path.read_text(encoding="utf-8"))


class EvaluationV2DataContractTest(unittest.TestCase):
    catalog: ClassVar[dict[str, Any]]
    built: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_incident_catalog(EVALUATION_DIR / "incident_families_v2.json")
        cls.built = build_evaluation_v2_dataset(cls.catalog, TemplateSurfaceQueryProvider())
        cls.built["qrel_review"] = _load(EVALUATION_DIR / "retrieval_qrel_review_v2.json")
        cls.built["scenario_review"] = _load(EVALUATION_DIR / "rca_scenario_review_v2.json")

    def test_committed_outputs_are_byte_stable_against_hidden_catalog(self) -> None:
        self.assertEqual(_load(KNOWLEDGE_DIR / "corpus.json"), self.built["corpus"])
        self.assertEqual(
            _load(EVALUATION_DIR / "retrieval_ground_truth_v2.json"),
            self.built["ground_truth"],
        )
        self.assertEqual(
            _load(EVALUATION_DIR / "retrieval_partitions_v2.json"),
            self.built["partitions"],
        )
        self.assertEqual(
            _load(EVALUATION_DIR / "rca_scenarios_v2.json"),
            self.built["rca_scenarios"],
        )

    def test_structural_and_completed_human_review_gates_pass(self) -> None:
        report = validate_evaluation_v2_dataset(self.built)

        self.assertTrue(report.structural_pass, report.errors)
        self.assertTrue(report.human_review_complete)
        self.assertEqual(report.metrics["retrieval_queries"], 32)
        self.assertEqual(report.metrics["rca_scenarios"], 14)
        self.assertGreaterEqual(report.metrics["candidate_pool_min"], 4)
        self.assertEqual(report.metrics["supported_rca_scenarios"], 11)
        self.assertGreaterEqual(report.metrics["cross_module_supported_ratio"], 0.30)
        self.assertEqual(report.metrics["complete_symptom_phrase_reuse"], 0)
        self.assertEqual(report.metrics["pending_qrel_reviews"], 0)
        self.assertEqual(report.metrics["pending_scenario_reviews"], 0)

    def test_qrels_and_partitions_are_family_isolated_and_review_covered(self) -> None:
        partitions = self.built["partitions"]["partitions"]
        self.assertFalse(
            set(partitions["calibration"]["incident_family_ids"])
            & set(partitions["test"]["incident_family_ids"])
        )
        self.assertFalse(
            set(partitions["calibration"]["primary_target_asset_ids"])
            & set(partitions["test"]["primary_target_asset_ids"])
        )
        expected = {
            (query_id, judgment["asset_id"], judgment["relevance"])
            for query_id, judgments in self.built["ground_truth"]["qrels"].items()
            for judgment in judgments
        }
        reviewed = {
            (item["query_id"], item["asset_id"], item["provisional_relevance"])
            for item in self.built["qrel_review"]["reviews"]
        }
        self.assertEqual(expected, reviewed)
        self.assertTrue(
            all(item["decision"] == "ACCEPTED" for item in self.built["qrel_review"]["reviews"])
        )

    def test_no_answer_queries_have_dense_governed_candidate_pools(self) -> None:
        assets = self.built["corpus"]["documents"]
        queries = self.built["ground_truth"]["queries"]
        no_answers = [item for item in queries if item["no_answer"]]

        self.assertEqual(len(no_answers), 4)
        for query in no_answers:
            pool = [
                item
                for item in assets
                if item["validation_status"] == "CONFIRMED"
                and item["document_type"] == query["requested_document_type"]
            ]
            self.assertGreaterEqual(len(pool), 7)
            self.assertGreaterEqual(len(query["hard_negative_asset_ids"]), 3)
            self.assertFalse(
                any(
                    judgment["relevance"] >= 2
                    for judgment in self.built["ground_truth"]["qrels"][query["query_id"]]
                )
            )

    def test_v1_regression_fixtures_are_unchanged(self) -> None:
        expected = {
            ROOT / "data/knowledge/synthetic_v1/canonical_facts.json": (
                "8bd783bfa05e7278c6c0ffafcb71bec908dd93ed"
            ),
            ROOT / "data/knowledge/synthetic_v1/corpus.json": (
                "c0f358f4f635f8d481ff8caddf7190d7de0de646"
            ),
            ROOT / "data/evaluation/retrieval_ground_truth.json": (
                "84e9814ebe9cfb64e3daff4eb2a9d7ec67538677"
            ),
            ROOT / "data/evaluation/scenarios.json": ("6355b8594ea1a0a2243c07501142c0104afcb0ce"),
        }
        for path, git_blob_hash in expected.items():
            # Match Git's text normalization so the invariant is portable across
            # Windows CRLF and Linux LF checkouts.
            normalized = path.read_bytes().replace(b"\r\n", b"\n")
            header = f"blob {len(normalized)}\0".encode()
            actual = hashlib.sha1(header + normalized).hexdigest()
            self.assertEqual(actual, git_blob_hash, path)


if __name__ == "__main__":
    unittest.main()
