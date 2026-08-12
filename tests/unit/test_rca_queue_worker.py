from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.audit import InMemoryAuditSink  # noqa: E402
from yield_rca_api.memory import InMemoryMemoryStore, MemoryApprovalService  # noqa: E402
from yield_rca_api.observability import RCAMetrics  # noqa: E402
from yield_rca_api.store import InMemoryRCAJobStore, RCAJobQueueRecord  # noqa: E402
from yield_rca_api.worker import (  # noqa: E402
    RCAQueueWorker,
    WorkerSettings,
    classify_worker_error,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMOutputValidationError,
    LLMSettings,
)
from yield_rca_core.models import RCAJob, RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


def enqueue(store: InMemoryRCAJobStore, job_id: str) -> None:
    state = RCAState(
        job=RCAJob(job_id=job_id, user_query=QUERY, status=TaskStatus.QUEUED.value)
    )
    store.enqueue(
        RCAJobQueueRecord(
            state=state,
            request={
                "investigation_mode": "product_window",
                "user_query": QUERY,
                "lot_id": None,
            },
            request_hash="a" * 64,
            idempotency_key=None,
            runtime_config={
                "agent_mode": "deterministic",
                "provider": "dashscope",
                "model": "qwen-plus",
                "orchestration_mode": "fixed",
                "dataset": "golden_case",
            },
        )
    )


def worker(
    store: InMemoryRCAJobStore,
    *,
    workflow: object | None = None,
) -> RCAQueueWorker:
    return RCAQueueWorker(
        store=store,
        workflow=workflow or build_csv_workflow(SEED_DIR),  # type: ignore[arg-type]
        audit_sink=InMemoryAuditSink(),
        memory_service=MemoryApprovalService(InMemoryMemoryStore()),
        metrics=RCAMetrics(),
        runtime_dataset="golden_case",
        settings=WorkerSettings(
            lease_seconds=60,
            heartbeat_seconds=10,
            poll_seconds=0.01,
            recovery_seconds=10,
            retry_base_seconds=0.01,
        ),
        worker_id="worker-test",
    )


class FailingWorkflow:
    orchestration_mode = "fixed"
    llm_settings = LLMSettings()

    def __init__(self, error: Exception) -> None:
        self.error = error

    def run(self, *args: object, **kwargs: object) -> RCAState:
        raise self.error


class CancellingWorkflow:
    orchestration_mode = "fixed"

    def __init__(self, store: InMemoryRCAJobStore) -> None:
        self.store = store
        self.delegate = build_csv_workflow(SEED_DIR)
        self.llm_settings = self.delegate.llm_settings

    def run(self, *args: object, **kwargs: object) -> RCAState:
        job_id = str(kwargs["job_id"])
        self.store.request_cancel(job_id)
        return self.delegate.run(*args, **kwargs)


class NeverRunWorkflow:
    orchestration_mode = "fixed"
    llm_settings = LLMSettings()

    def __init__(self) -> None:
        self.called = False

    def run(self, *args: object, **kwargs: object) -> RCAState:
        self.called = True
        raise AssertionError("runtime mismatch must be rejected before Workflow execution")


class RCAQueueWorkerTest(unittest.TestCase):
    def test_worker_completes_claimed_workflow(self) -> None:
        store = InMemoryRCAJobStore()
        enqueue(store, "RCA_WORKER_SUCCESS")

        self.assertTrue(worker(store).run_once())

        record = store.get_record("RCA_WORKER_SUCCESS")
        assert record is not None
        self.assertEqual(record.state.job.status, "completed")
        self.assertTrue(record.state.evidence)
        self.assertEqual(record.state.execution_metadata["queue_attempt_number"], 1)
        self.assertEqual(store.list_attempts("RCA_WORKER_SUCCESS")[0].status, "completed")

    def test_transient_provider_failure_schedules_retry(self) -> None:
        store = InMemoryRCAJobStore()
        enqueue(store, "RCA_WORKER_RETRY")
        error = LLMCallError("provider timeout", status_code=None)

        self.assertTrue(worker(store, workflow=FailingWorkflow(error)).run_once())

        record = store.get_record("RCA_WORKER_RETRY")
        assert record is not None
        self.assertEqual(record.state.job.status, "retry_wait")
        self.assertEqual(record.error["error_code"], "LLM_UNAVAILABLE")

    def test_non_retryable_output_failure_is_terminal(self) -> None:
        store = InMemoryRCAJobStore()
        enqueue(store, "RCA_WORKER_FAILED")

        worker(
            store,
            workflow=FailingWorkflow(LLMOutputValidationError("bad output")),
        ).run_once()

        record = store.get_record("RCA_WORKER_FAILED")
        assert record is not None
        self.assertEqual(record.state.job.status, "failed")
        self.assertEqual(record.error["error_code"], "LLM_OUTPUT_INVALID")

    def test_running_cancellation_discards_completed_workflow_result(self) -> None:
        store = InMemoryRCAJobStore()
        enqueue(store, "RCA_WORKER_CANCEL")

        worker(store, workflow=CancellingWorkflow(store)).run_once()

        state = store.get("RCA_WORKER_CANCEL")
        assert state is not None
        self.assertEqual(state.job.status, "cancelled")
        self.assertFalse(state.evidence)

    def test_runtime_dataset_mismatch_is_terminal_before_workflow_execution(self) -> None:
        store = InMemoryRCAJobStore()
        enqueue(store, "RCA_WORKER_DATASET_MISMATCH")
        workflow = NeverRunWorkflow()
        queue_worker = worker(store, workflow=workflow)
        queue_worker.runtime_dataset = "multi_case"

        queue_worker.run_once()

        record = store.get_record("RCA_WORKER_DATASET_MISMATCH")
        assert record is not None
        self.assertFalse(workflow.called)
        self.assertEqual(record.state.job.status, "failed")
        self.assertEqual(
            record.error["error_code"],
            "WORKER_RUNTIME_CONFIG_MISMATCH",
        )

    def test_error_classification_retries_only_transient_failures(self) -> None:
        for error, expected_code, retryable in (
            (LLMCallError("timeout"), "LLM_UNAVAILABLE", True),
            (LLMCallError("rate", status_code=429), "LLM_RATE_LIMITED", True),
            (LLMCallError("auth", status_code=401), "LLM_AUTH_FAILED", False),
            (
                LLMCallError("billing", provider_code="Arrearage"),
                "LLM_BILLING_ERROR",
                False,
            ),
            (LLMOutputValidationError("bad"), "LLM_OUTPUT_INVALID", False),
        ):
            payload, actual_retryable = classify_worker_error(error)
            self.assertEqual(payload["error_code"], expected_code)
            self.assertEqual(actual_retryable, retryable)


if __name__ == "__main__":
    unittest.main()
