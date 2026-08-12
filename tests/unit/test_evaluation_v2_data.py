from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.evaluation_v2_data import (  # noqa: E402
    EvaluationV2DataError,
    QwenSurfaceQueryProvider,
    TemplateSurfaceQueryProvider,
    build_evaluation_v2_dataset,
    default_incident_catalog,
    document_writer_payloads,
    query_writer_payloads,
    validate_incident_catalog,
    write_evaluation_v2_dataset,
)


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


class FakeQwenClient:
    provider = "fake"
    model = "fake-qwen"

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def complete_json(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return SimpleNamespace(
            data={
                "queries": [
                    {
                        "query_key": item["query_key"],
                        "text": f"Synthetic wording for {item['query_key']}",
                    }
                    for item in request.payload["queries"]
                ]
            }
        )


class EvaluationV2DataUnitTest(unittest.TestCase):
    def test_test_queries_may_reuse_calibration_knowledge_without_reverse_leakage(self) -> None:
        catalog = default_incident_catalog()
        test_family = next(
            item
            for item in catalog["incident_families"]
            if item["incident_family_id"] == "IF_V2_008"
        )

        self.assertIn("RCA_V2_002", test_family["secondary_relevant_asset_ids"])
        validate_incident_catalog(catalog)

        calibration_family = next(
            item
            for item in catalog["incident_families"]
            if item["incident_family_id"] == "IF_V2_001"
        )
        calibration_family["secondary_relevant_asset_ids"].append("RCA_V2_008")
        with self.assertRaisesRegex(EvaluationV2DataError, "leaks test data"):
            validate_incident_catalog(catalog)

    def test_writer_payloads_enforce_asymmetric_information_access(self) -> None:
        catalog = default_incident_catalog()
        document_payload = document_writer_payloads(catalog)[0]
        query_payload = query_writer_payloads(catalog)[0]

        self.assertIn("causal_record", document_payload)
        self.assertIn("corrective_actions", document_payload)
        forbidden = {
            "causal_record",
            "causal_module",
            "root_cause",
            "corrective_actions",
            "target_asset_id",
            "asset_id",
            "qrels",
            "content",
            "document_observation_summary",
        }
        self.assertTrue(forbidden.isdisjoint(_all_keys(query_payload)))
        self.assertIn("document_observation_summary", document_payload)
        self.assertEqual(
            set(query_payload),
            {
                "query_key",
                "requested_task",
                "language",
                "metadata_quality",
                "observation",
                "synthetic",
            },
        )

    def test_qwen_wording_is_bounded_and_receives_only_redacted_payloads(self) -> None:
        client = FakeQwenClient()
        provider = QwenSurfaceQueryProvider(client, batch_size=2, max_paid_calls=1)
        payloads = query_writer_payloads(default_incident_catalog())[:2]

        generated = provider.generate(payloads)

        self.assertEqual(len(generated), 2)
        self.assertEqual(provider.paid_call_count, 1)
        self.assertEqual(client.requests[0].prompt_name, "synthetic_v2_query_wording")
        self.assertTrue(
            all(
                "root_cause" not in _all_keys(item)
                for item in client.requests[0].payload["queries"]
            )
        )
        with self.assertRaisesRegex(EvaluationV2DataError, "paid LLM-call cap"):
            provider.generate(payloads)

    def test_regeneration_preserves_existing_human_review_decisions(self) -> None:
        catalog = default_incident_catalog()
        built = build_evaluation_v2_dataset(catalog, TemplateSurfaceQueryProvider())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            knowledge_dir = root / "knowledge"
            evaluation_dir = root / "evaluation"
            seed_dir = root / "seed"
            write_evaluation_v2_dataset(
                built,
                knowledge_dir=knowledge_dir,
                evaluation_dir=evaluation_dir,
                seed_dir=seed_dir,
            )
            review_path = evaluation_dir / "retrieval_qrel_review_v2.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["reviews"][0].update(
                {
                    "decision": "ACCEPTED",
                    "reviewer": "ENGINEER_A",
                    "reviewed_at": "2026-08-10T00:00:00+00:00",
                }
            )
            review_path.write_text(json.dumps(review), encoding="utf-8")

            write_evaluation_v2_dataset(
                built,
                knowledge_dir=knowledge_dir,
                evaluation_dir=evaluation_dir,
                seed_dir=seed_dir,
            )

            preserved = json.loads(review_path.read_text(encoding="utf-8"))["reviews"][0]
            self.assertEqual(preserved["decision"], "ACCEPTED")
            self.assertEqual(preserved["reviewer"], "ENGINEER_A")


if __name__ == "__main__":
    unittest.main()
