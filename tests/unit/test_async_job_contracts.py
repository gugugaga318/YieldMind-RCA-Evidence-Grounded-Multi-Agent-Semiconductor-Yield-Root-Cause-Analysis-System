from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.store import (  # noqa: E402
    TERMINAL_JOB_STATUSES,
    IdempotencyConflictError,
    InMemoryRCAJobStore,
    InvalidJobTransitionError,
    RCAJobQueueRecord,
    validate_job_transition,
)
from yield_rca_core.models import RCAJob, RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."


def queue_record(
    job_id: str,
    *,
    status: str = TaskStatus.QUEUED.value,
    request_hash: str = "a" * 64,
    idempotency_key: str | None = None,
) -> RCAJobQueueRecord:
    state = RCAState(
        job=RCAJob(job_id=job_id, user_query=QUERY, status=status),
        execution_metadata={"agent_mode": "deterministic"},
    )
    return RCAJobQueueRecord(
        state=state,
        request={
            "investigation_mode": "product_window",
            "user_query": QUERY,
            "lot_id": None,
        },
        request_hash=request_hash,
        idempotency_key=idempotency_key,
        runtime_config={
            "agent_mode": "deterministic",
            "provider": "dashscope",
            "model": "qwen-plus",
            "orchestration_mode": "fixed",
            "dataset": "golden_case",
        },
    )


class AsyncJobContractTest(unittest.TestCase):
    def test_task_status_exposes_async_lifecycle_and_terminal_set(self) -> None:
        self.assertEqual(
            {
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.CANCEL_REQUESTED.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            }
            & {item.value for item in TaskStatus},
            {
                "queued",
                "running",
                "retry_wait",
                "cancel_requested",
                "completed",
                "failed",
                "cancelled",
            },
        )
        self.assertEqual(TERMINAL_JOB_STATUSES, {"completed", "failed", "cancelled"})

    def test_transition_contract_rejects_terminal_and_illegal_shortcuts(self) -> None:
        for current, next_status in (
            ("queued", "running"),
            ("queued", "cancelled"),
            ("running", "retry_wait"),
            ("running", "cancel_requested"),
            ("cancel_requested", "cancelled"),
        ):
            validate_job_transition(current, next_status)

        for current, next_status in (
            ("queued", "completed"),
            ("retry_wait", "completed"),
            ("completed", "running"),
            ("failed", "queued"),
            ("cancelled", "running"),
        ):
            with self.assertRaises(InvalidJobTransitionError):
                validate_job_transition(current, next_status)

    def test_in_memory_queue_enforces_idempotency_and_state_transitions(self) -> None:
        store = InMemoryRCAJobStore()
        original = queue_record("RCA_ONE", idempotency_key="request-one")
        store.enqueue(original)

        repeated = store.enqueue(
            queue_record("RCA_TWO", idempotency_key="request-one")
        )
        self.assertEqual(repeated.state.job.job_id, "RCA_ONE")

        with self.assertRaises(IdempotencyConflictError):
            store.enqueue(
                queue_record(
                    "RCA_THREE",
                    idempotency_key="request-one",
                    request_hash="b" * 64,
                )
            )

        running = replace(
            original.state,
            job=replace(original.state.job, status=TaskStatus.RUNNING.value),
        )
        store.save(running)
        completed = replace(
            running,
            job=replace(running.job, status=TaskStatus.COMPLETED.value),
        )
        store.save(completed)
        with self.assertRaises(InvalidJobTransitionError):
            store.save(running)

    def test_queue_record_rejects_secrets_and_hidden_reasoning(self) -> None:
        original = queue_record("RCA_SAFE")
        for runtime_config in (
            {"DASHSCOPE_API_KEY": "must-not-persist"},
            {"headers": {"Authorization": "Bearer must-not-persist"}},
            {"chain_of_thought": "must-not-persist"},
        ):
            with self.assertRaises(ValueError):
                replace(original, runtime_config=runtime_config)

    def test_default_post_only_enqueues_and_returns_polling_contract(self) -> None:
        workflow = build_csv_workflow(SEED_DIR)
        store = InMemoryRCAJobStore()
        app = create_app(
            workflow=workflow,
            store=store,
            runtime_dataset="golden_case",
        )

        with patch.object(
            type(workflow),
            "run",
            side_effect=AssertionError("default async route must not execute Workflow"),
        ) as run:
            with TestClient(app) as client:
                response = client.post(
                    "/rca/jobs",
                    json={"user_query": QUERY},
                    headers={"Idempotency-Key": "browser-submit-001"},
                )
                payload = response.json()
                state_response = client.get(payload["state_url"])
                report_response = client.get(payload["report_url"])

        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["idempotency_key"], "browser-submit-001")
        self.assertEqual(payload["events_url"], f"/rca/jobs/{payload['job_id']}/events")
        self.assertEqual(payload["cancel_url"], f"/rca/jobs/{payload['job_id']}/cancel")
        run.assert_not_called()
        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["status"], "queued")
        self.assertEqual(state_response.json()["queue"]["attempt_count"], 0)
        self.assertEqual(report_response.status_code, 409)
        self.assertEqual(
            report_response.json()["detail"]["error_code"],
            "job_not_completed",
        )

    def test_post_idempotency_reuses_job_and_rejects_changed_request(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(SEED_DIR),
            store=InMemoryRCAJobStore(),
        )
        headers = {"Idempotency-Key": "same-submit"}
        with TestClient(app) as client:
            first = client.post("/rca/jobs", json={"user_query": QUERY}, headers=headers)
            repeated = client.post("/rca/jobs", json={"user_query": QUERY}, headers=headers)
            conflict = client.post(
                "/rca/jobs",
                json={"user_query": "Analyze a different product window."},
                headers=headers,
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(first.json()["job_id"], repeated.json()["job_id"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["error_code"], "idempotency_conflict")

    def test_inline_adapter_is_explicit_and_preserves_sync_regression_baseline(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(SEED_DIR),
            store=InMemoryRCAJobStore(),
            execute_jobs_inline=True,
        )
        with TestClient(app) as client:
            response = client.post("/rca/jobs", json={"user_query": QUERY})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "completed")


if __name__ == "__main__":
    unittest.main()
