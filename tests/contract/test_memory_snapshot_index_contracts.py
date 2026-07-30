from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.memory import InMemoryMemoryStore, MemoryApprovalService  # noqa: E402
from yield_rca_core.memory_models import KnowledgeIndexStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


class MemorySnapshotIndexContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = build_csv_workflow(SEED_DIR).run(
            "Analyze abnormal Lot LOT_A_015 and identify impact Lots.",
            job_id="MEMORY_SNAPSHOT_JOB",
            lot_id="LOT_A_015",
        )

    def setUp(self) -> None:
        self.store = InMemoryMemoryStore()
        self.service = MemoryApprovalService(self.store)

    def test_candidate_keeps_a_minimal_self_contained_evidence_snapshot(self) -> None:
        candidate = self.service.create_from_state(self.state)

        self.assertEqual(candidate.index_status, KnowledgeIndexStatus.NOT_REQUESTED.value)
        self.assertEqual(candidate.reasoning_engine, "hypothesis_v1")
        self.assertEqual(
            {item["evidence_id"] for item in candidate.evidence_snapshot},
            set(candidate.evidence_ids),
        )
        self.assertTrue(candidate.evidence_snapshot)
        for item in candidate.evidence_snapshot:
            self.assertTrue(item["observation"])
            self.assertTrue(item["entities"])
            self.assertTrue(item["source"]["source_tool"])
            self.assertNotIn("metadata", item)
        self.assertEqual(
            candidate.knowledge_provenance["hypothesis_id"],
            self.state.hypotheses[0].hypothesis_id,
        )

    def test_publication_and_index_visibility_follow_two_engineer_approval(self) -> None:
        candidate = self.service.create_from_state(self.state)
        first = self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE017",
            engineer_role="yield_engineer",
            decision="approve",
        )
        self.assertEqual(first.index_status, KnowledgeIndexStatus.NOT_REQUESTED.value)
        self.assertFalse(self.store.published_cases)

        published = self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="PE017",
            engineer_role="process_engineer",
            decision="approve",
        )
        self.assertEqual(published.index_status, KnowledgeIndexStatus.COMPLETED.value)
        self.assertEqual(published.index_attempts, 1)
        assert published.published_case_id is not None
        document = next(iter(self.store.published_documents.values()))
        content = json.loads(document["content"])
        self.assertEqual(content["reasoning_engine"], "hypothesis_v1")
        self.assertEqual(
            {item["evidence_id"] for item in content["evidence_snapshot"]},
            set(published.evidence_ids),
        )

    def test_batch_17_migration_contains_snapshot_and_index_contracts(self) -> None:
        migration = (
            ROOT / "db" / "migrations" / "006_memory_snapshot_index_update.up.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("evidence_snapshot", migration)
        self.assertIn("knowledge_provenance", migration)
        self.assertIn("knowledge_index_update", migration)
        self.assertIn("index_status", migration)


if __name__ == "__main__":
    unittest.main()
