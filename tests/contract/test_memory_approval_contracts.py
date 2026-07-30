from __future__ import annotations

import inspect
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.memory import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryApprovalConflictError,
    MemoryApprovalService,
    MemoryApprovalValidationError,
    MemoryCandidateNotEligibleError,
    PostgresMemoryStore,
)
from yield_rca_core.memory_models import MemoryCandidate, MemoryCandidateStatus  # noqa: E402
from yield_rca_core.models import RCAState  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "multi_case"


class MemoryApprovalContractTest(unittest.TestCase):
    supported_state: ClassVar[RCAState]
    inconclusive_state: ClassVar[RCAState]

    @classmethod
    def setUpClass(cls) -> None:
        workflow = build_csv_workflow(SEED_DIR)
        cls.supported_state = workflow.run(
            "Analyze abnormal Lot LOT_A_015 and identify impact Lots.",
            job_id="MEMORY_SUPPORTED_JOB",
            plan_id="MEMORY_SUPPORTED_PLAN",
            lot_id="LOT_A_015",
        )
        cls.inconclusive_state = workflow.run(
            "Analyze abnormal Lot LOT_A_038 and identify impact Lots.",
            job_id="MEMORY_INCONCLUSIVE_JOB",
            plan_id="MEMORY_INCONCLUSIVE_PLAN",
            lot_id="LOT_A_038",
        )

    def setUp(self) -> None:
        self.store = InMemoryMemoryStore()
        self.service = MemoryApprovalService(self.store)

    def test_candidate_serializes_and_requires_two_distinct_engineers(self) -> None:
        candidate = self.service.create_from_state(self.supported_state)
        restored = MemoryCandidate.from_dict(candidate.to_dict())
        self.assertEqual(restored, candidate)
        self.assertEqual(candidate.status, MemoryCandidateStatus.PENDING_APPROVAL.value)
        self.assertTrue(candidate.requires_process_engineer_approval)

        first = self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE001",
            engineer_role="yield_engineer",
            decision="approve",
        )
        self.assertEqual(first.status, MemoryCandidateStatus.PENDING_APPROVAL.value)
        self.assertEqual(first.approval_count, 1)

        published = self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="PE001",
            engineer_role="process_engineer",
            decision="approve",
            comment="DOE and qualification review accepted.",
        )
        self.assertEqual(published.status, MemoryCandidateStatus.PUBLISHED.value)
        self.assertEqual(published.approval_count, 2)
        self.assertTrue(published.has_process_engineer_approval)
        self.assertIn(published.published_case_id, self.store.published_cases)
        self.assertEqual(
            [event.action for event in self.store.audit_events],
            [
                "MEMORY_APPROVAL_RECORDED",
                "MEMORY_APPROVAL_RECORDED",
                "MEMORY_CANDIDATE_PUBLISHED",
            ],
        )
        case = self.store.published_cases[published.published_case_id]
        self.assertEqual(case["validation_status"], "CONFIRMED")
        self.assertEqual(case["approval_count"], 2)

    def test_same_engineer_cannot_decide_twice(self) -> None:
        candidate = self.service.create_from_state(self.supported_state)
        self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE001",
            engineer_role="yield_engineer",
            decision="approve",
        )
        with self.assertRaises(MemoryApprovalConflictError):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                engineer_id="ye001",
                engineer_role="process_engineer",
                decision="approve",
            )

    def test_second_approval_must_be_process_engineer_for_recipe_candidate(self) -> None:
        candidate = self.service.create_from_state(self.supported_state)
        self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE001",
            engineer_role="yield_engineer",
            decision="approve",
        )
        with self.assertRaises(MemoryApprovalValidationError):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                engineer_id="EE001",
                engineer_role="equipment_engineer",
                decision="approve",
            )
        self.assertEqual(self.service.get(candidate.candidate_id).approval_count, 1)
        self.assertEqual(len(self.store.audit_events), 1)

    def test_separate_service_instances_apply_concurrent_decisions_atomically(self) -> None:
        candidate = self.service.create_from_state(self.supported_state)
        first_service = MemoryApprovalService(self.store)
        second_service = MemoryApprovalService(self.store)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    first_service.decide,
                    candidate_id=candidate.candidate_id,
                    engineer_id="YE_CONCURRENT",
                    engineer_role="yield_engineer",
                    decision="approve",
                    correlation_id="CORR_CONCURRENT_YE",
                ),
                executor.submit(
                    second_service.decide,
                    candidate_id=candidate.candidate_id,
                    engineer_id="PE_CONCURRENT",
                    engineer_role="process_engineer",
                    decision="approve",
                    correlation_id="CORR_CONCURRENT_PE",
                ),
            ]
            for future in futures:
                future.result()

        stored = self.service.get(candidate.candidate_id)
        self.assertEqual(stored.status, MemoryCandidateStatus.PUBLISHED.value)
        self.assertEqual(stored.approval_count, 2)
        self.assertEqual(
            {event.correlation_id for event in self.store.audit_events},
            {"CORR_CONCURRENT_YE", "CORR_CONCURRENT_PE"},
        )

    def test_rejection_is_terminal_and_does_not_publish(self) -> None:
        candidate = self.service.create_from_state(self.supported_state)
        rejected = self.service.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="QE001",
            engineer_role="quality_engineer",
            decision="reject",
            comment="Evidence needs additional verification.",
        )
        self.assertEqual(rejected.status, MemoryCandidateStatus.REJECTED.value)
        self.assertFalse(self.store.published_cases)
        with self.assertRaises(MemoryApprovalConflictError):
            self.service.decide(
                candidate_id=candidate.candidate_id,
                engineer_id="PE001",
                engineer_role="process_engineer",
                decision="approve",
            )

    def test_inconclusive_rca_cannot_create_publishable_candidate(self) -> None:
        with self.assertRaises(MemoryCandidateNotEligibleError):
            self.service.create_from_state(self.inconclusive_state)

    def test_postgres_decision_reloads_locked_state_and_writes_audit_in_transaction(
        self,
    ) -> None:
        source = inspect.getsource(PostgresMemoryStore.commit_decision)
        lock_position = source.index("SELECT * FROM memory_candidate")
        reload_position = source.index("_candidate_from_database")
        decision_position = source.index("_apply_decision")
        approval_position = source.index("INSERT INTO memory_approval")
        audit_position = source.index("INSERT INTO audit_event")

        self.assertLess(lock_position, reload_position)
        self.assertLess(reload_position, decision_position)
        self.assertLess(decision_position, approval_position)
        self.assertLess(approval_position, audit_position)
        self.assertNotIn("self.get(", source)


if __name__ == "__main__":
    unittest.main()
