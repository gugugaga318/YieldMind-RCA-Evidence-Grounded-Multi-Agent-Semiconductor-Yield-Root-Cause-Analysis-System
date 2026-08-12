from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.retrieval_evaluation import RetrievalGroundTruth  # noqa: E402
from yield_rca_core.synthetic_knowledge import (  # noqa: E402
    TemplateSyntheticKnowledgeTextProvider,
    build_synthetic_knowledge_corpus,
    canonical_file_sha256,
    load_canonical_facts,
    write_synthetic_knowledge_corpus,
)

CORPUS_DIR = ROOT / "data" / "knowledge" / "synthetic_v1"
CANONICAL = CORPUS_DIR / "canonical_facts.json"
GROUND_TRUTH = ROOT / "data" / "evaluation" / "retrieval_ground_truth.json"


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class RecordingTemplateProvider(TemplateSyntheticKnowledgeTextProvider):  # type: ignore[misc,unused-ignore]
    def __init__(self) -> None:
        self.query_inputs: list[dict[str, Any]] = []

    def generate_queries(self, query_inputs: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
        self.query_inputs = [dict(item) for item in query_inputs]
        return super().generate_queries(query_inputs)


class SyntheticKnowledgeCorpusContractTest(unittest.TestCase):
    def test_corpus_has_professional_scale_and_independent_document_types(self) -> None:
        manifest = json.loads((CORPUS_DIR / "generation_manifest.json").read_text(encoding="utf-8"))
        documents = csv_rows(CORPUS_DIR / "knowledge_document.csv")
        cases = csv_rows(CORPUS_DIR / "rca_case.csv")

        self.assertEqual(manifest["counts"]["confirmed_rca_cases"], 36)
        self.assertEqual(manifest["counts"]["confirmed_sops"], 12)
        self.assertEqual(manifest["counts"]["confirmed_engineering_notes"], 12)
        self.assertEqual(manifest["counts"]["answerable_queries"], 96)
        self.assertEqual(manifest["counts"]["no_answer_queries"], 18)
        self.assertEqual(manifest["counts"]["total_queries"], 114)
        self.assertEqual(sum(row["validation_status"] == "CONFIRMED" for row in cases), 36)
        independent = [row for row in documents if not row["case_id"]]
        self.assertEqual(
            sum(
                row["document_type"] == "SOP" and row["validation_status"] == "CONFIRMED"
                for row in independent
            ),
            12,
        )
        self.assertEqual(
            sum(
                row["document_type"] == "ENGINEERING_NOTE"
                and row["validation_status"] == "CONFIRMED"
                for row in independent
            ),
            12,
        )

    def test_ground_truth_is_independent_and_valid_against_governed_assets(self) -> None:
        manifest = json.loads((CORPUS_DIR / "generation_manifest.json").read_text(encoding="utf-8"))
        statuses = {item["asset_id"]: item["validation_status"] for item in manifest["assets"]}
        ground_truth = RetrievalGroundTruth.load(GROUND_TRUTH)

        ground_truth.validate_asset_catalog(statuses)
        self.assertEqual(ground_truth.corpus_version, manifest["corpus_version"])
        self.assertEqual(ground_truth.relevance_threshold, 2)
        self.assertEqual(len(ground_truth.queries), 114)
        self.assertEqual(sum(item.no_answer for item in ground_truth.queries), 18)
        self.assertNotEqual(GROUND_TRUTH.name, "scenarios.json")

    def test_query_generation_projection_excludes_answers_and_identifiers(self) -> None:
        canonical = load_canonical_facts(CANONICAL)
        provider = RecordingTemplateProvider()

        build_synthetic_knowledge_corpus(
            canonical,
            provider,
            canonical_sha256=canonical_file_sha256(CANONICAL),
        )

        forbidden_keys = {
            "asset_id",
            "case_id",
            "document_id",
            "title_seed",
            "root_cause",
            "corrective_actions",
            "solution",
            "procedure_steps",
            "interpretation",
            "content",
            "qrels",
        }
        self.assertEqual(len(provider.query_inputs), 96)
        self.assertTrue(all(forbidden_keys.isdisjoint(item) for item in provider.query_inputs))

    def test_queries_do_not_copy_case_ids_or_complete_root_causes(self) -> None:
        canonical = load_canonical_facts(CANONICAL)
        payload = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
        all_query_text = "\n".join(item["text"].casefold() for item in payload["queries"])

        for case in canonical["rca_cases"]:
            self.assertNotIn(case["case_id"].casefold(), all_query_text)
            self.assertNotIn(case["document_id"].casefold(), all_query_text)
            self.assertNotIn(case["root_cause"].casefold(), all_query_text)

    def test_hard_negatives_are_confirmed_same_module_cases_with_other_causes(self) -> None:
        canonical = load_canonical_facts(CANONICAL)
        by_id = {item["case_id"]: item for item in canonical["rca_cases"]}

        for case in canonical["rca_cases"]:
            hard_negative = by_id[case["hard_negative_asset_id"]]
            self.assertEqual(case["module"], hard_negative["module"])
            self.assertNotEqual(case["root_cause"], hard_negative["root_cause"])

    def test_manifest_hash_matches_canonical_source(self) -> None:
        manifest = json.loads((CORPUS_DIR / "generation_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["canonical_sha256"],
            hashlib.sha256(CANONICAL.read_bytes()).hexdigest(),
        )
        self.assertTrue(manifest["synthetic"])
        self.assertEqual(manifest["publication_policy"], "BUILTIN_SYNTHETIC_SEED")
        self.assertFalse(manifest["generation"]["query_root_cause_exposed"])
        self.assertTrue(manifest["generation"]["qrels_generated_by_python"])

    def test_template_generation_is_byte_stable(self) -> None:
        canonical = load_canonical_facts(CANONICAL)
        built = build_synthetic_knowledge_corpus(
            canonical,
            TemplateSyntheticKnowledgeTextProvider(),
            canonical_sha256=canonical_file_sha256(CANONICAL),
        )
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_dir = Path(first)
            second_dir = Path(second)
            write_synthetic_knowledge_corpus(
                built,
                output_dir=first_dir,
                ground_truth_path=first_dir / "retrieval_ground_truth.json",
            )
            write_synthetic_knowledge_corpus(
                built,
                output_dir=second_dir,
                ground_truth_path=second_dir / "retrieval_ground_truth.json",
            )
            names = {
                "rca_case.csv",
                "knowledge_document.csv",
                "corpus.json",
                "generation_manifest.json",
                "retrieval_ground_truth.json",
            }
            for name in names:
                self.assertEqual((first_dir / name).read_bytes(), (second_dir / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
