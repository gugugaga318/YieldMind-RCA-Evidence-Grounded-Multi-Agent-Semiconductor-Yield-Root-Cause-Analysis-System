from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"


class KnowledgeAPIIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.knowledge_store = load_builtin_knowledge_store(
            CORPUS,
            additional_case_ids={"CASE_CMP_SLURRY_DEGRADATION"},
        )
        self.client = TestClient(
            create_app(
                workflow=build_csv_workflow(SEED_DIR),
                knowledge_store=self.knowledge_store,
            )
        )

    def tearDown(self) -> None:
        self.client.close()

    def ingest(self, unique_term: str = "apiuniqueneedle") -> dict[str, object]:
        response = self.client.post(
            "/knowledge/ingestions",
            data={
                "document_type": "SOP",
                "title": "API governed SOP",
                "module": "Cu CMP",
                "operation": "Cu CMP polish",
                "tags": "scratch, inspection",
            },
            files={
                "file": (
                    "scratch.md",
                    f"# Step 1\nInspect carrier and record {unique_term}.".encode(),
                    "text/markdown",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return dict(response.json()["candidate"])

    def test_lookup_runs_only_knowledge_agent_and_returns_no_rca_conclusion(self) -> None:
        response = self.client.post(
            "/knowledge/lookups",
            json={
                "query": "Cu CMP radial scratch retaining ring",
                "question_kind": "historical_match",
                "module": "Cu CMP",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["intent"], "knowledge_lookup")
        self.assertEqual(payload["plan"]["action"], "retrieve_historical_case")
        self.assertEqual([item["agent"] for item in payload["agent_trace"]], ["knowledge"])
        self.assertIsNone(payload["root_cause_conclusion"])
        self.assertNotIn("hypotheses", payload)
        self.assertNotIn("impact_lots", payload)
        self.assertNotIn("report", payload)

    def test_staged_document_is_invisible_until_two_different_approvals(self) -> None:
        candidate = self.ingest()
        candidate_id = str(candidate["candidate_id"])

        before = self.client.post(
            "/knowledge/lookups",
            json={
                "query": "apiuniqueneedle",
                "question_kind": "procedure_guidance",
            },
        ).json()
        self.assertEqual(before["status"], "no_match")

        first = self.client.post(
            f"/knowledge/ingestions/{candidate_id}/approvals",
            json={
                "engineer_id": "YE001",
                "engineer_role": "yield_engineer",
                "decision": "approve",
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["candidate"]["status"], "pending_approval")

        second = self.client.post(
            f"/knowledge/ingestions/{candidate_id}/approvals",
            json={
                "engineer_id": "PE001",
                "engineer_role": "process_engineer",
                "decision": "approve",
            },
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["candidate"]["status"], "published")

        after = self.client.post(
            "/knowledge/lookups",
            json={
                "query": "apiuniqueneedle",
                "question_kind": "procedure_guidance",
            },
        ).json()
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["hits"][0]["document"]["title"], "API governed SOP")

    def test_duplicate_engineer_and_question_type_mismatch_return_stable_errors(self) -> None:
        candidate = self.ingest("apierrorneedle")
        candidate_id = str(candidate["candidate_id"])
        decision = {
            "engineer_id": "YE001",
            "engineer_role": "yield_engineer",
            "decision": "approve",
        }
        self.assertEqual(
            self.client.post(
                f"/knowledge/ingestions/{candidate_id}/approvals", json=decision
            ).status_code,
            200,
        )
        duplicate = self.client.post(
            f"/knowledge/ingestions/{candidate_id}/approvals", json=decision
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(
            duplicate.json()["detail"]["error_code"], "ENGINEER_ALREADY_DECIDED"
        )

        mismatch = self.client.post(
            "/knowledge/lookups",
            json={
                "query": "scratch",
                "question_kind": "procedure_guidance",
                "document_type": "ENGINEERING_NOTE",
            },
        )
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(
            mismatch.json()["detail"]["error_code"],
            "QUESTION_DOCUMENT_TYPE_MISMATCH",
        )

    def test_openapi_uses_typed_knowledge_contracts(self) -> None:
        schema = self.client.get("/openapi.json").json()
        lookup = schema["paths"]["/knowledge/lookups"]["post"]
        self.assertTrue(
            lookup["responses"]["200"]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("KnowledgeLookupResponse")
        )
        ingestion = schema["paths"]["/knowledge/ingestions"]["post"]
        self.assertTrue(
            ingestion["responses"]["201"]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("KnowledgeIngestionResponse")
        )

    def test_approved_rca_memory_is_chunked_into_the_same_active_index(self) -> None:
        created = self.client.post(
            "/rca/jobs",
            json={
                "user_query": (
                    "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
                )
            },
        ).json()
        candidate_id = created["memory_candidate_id"]
        for engineer_id, role in (
            ("YE_MEMORY", "yield_engineer"),
            ("PE_MEMORY", "process_engineer"),
        ):
            response = self.client.post(
                f"/memory/candidates/{candidate_id}/approvals",
                json={
                    "engineer_id": engineer_id,
                    "engineer_role": role,
                    "decision": "approve",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)

        lookup = self.client.post(
            "/knowledge/lookups",
            json={
                "query": "CMP_CU03_CH02 slurry delivery degradation",
                "question_kind": "historical_match",
                "top_k": 20,
            },
        ).json()
        self.assertTrue(
            any(
                item["document"]["evaluation_asset_id"].startswith("RCA_MEMORY_")
                for item in lookup["hits"]
            )
        )


if __name__ == "__main__":
    unittest.main()
