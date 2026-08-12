from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    JobLeaseLostError,
    JobNotCancellableError,
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

    def test_queue_claim_retry_complete_and_attempt_event_audit(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_LEASED"))
        started = datetime.now(UTC)

        first = store.claim_next(
            worker_id="worker-one",
            lease_seconds=60,
            now=started,
        )
        assert first is not None
        self.assertEqual(first.state.job.status, "running")
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(first.lease_owner, "worker-one")
        self.assertIsNotNone(
            store.heartbeat(
                worker_id="worker-one",
                job_id="RCA_LEASED",
                lease_seconds=60,
                now=started + timedelta(seconds=20),
            )
        )

        retry = store.fail_attempt(
            worker_id="worker-one",
            job_id="RCA_LEASED",
            error={"error_code": "LLM_RATE_LIMITED", "retryable": True},
            retryable=True,
            retry_after_seconds=5,
            now=started + timedelta(seconds=21),
        )
        self.assertEqual(retry.state.job.status, "retry_wait")
        self.assertIsNone(
            store.claim_next(
                worker_id="worker-two",
                lease_seconds=60,
                now=started + timedelta(seconds=24),
            )
        )
        second = store.claim_next(
            worker_id="worker-two",
            lease_seconds=60,
            now=started + timedelta(seconds=26),
        )
        assert second is not None
        completed_state = replace(
            second.state,
            job=replace(second.state.job, status=TaskStatus.COMPLETED.value),
        )
        completed = store.complete(
            worker_id="worker-two",
            job_id="RCA_LEASED",
            state=completed_state,
            checkpoint={"stage": "workflow_completed"},
        )
        self.assertEqual(completed.state.job.status, "completed")
        self.assertEqual(completed.attempt_count, 2)
        self.assertEqual(
            [attempt.status for attempt in store.list_attempts("RCA_LEASED")],
            ["failed", "completed"],
        )
        self.assertEqual(
            [event.event_type for event in store.list_events("RCA_LEASED")],
            ["job_queued", "job_started", "job_retry_scheduled", "job_started", "job_completed"],
        )

    def test_lease_expiry_recovery_discards_old_worker_commit(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_STALE"))
        started = datetime.now(UTC)
        claimed = store.claim_next(
            worker_id="dead-worker",
            lease_seconds=10,
            now=started,
        )
        assert claimed is not None

        recovered = store.recover_stale_leases(
            now=started + timedelta(seconds=11),
            retry_after_seconds=2,
        )
        self.assertEqual(recovered, 1)
        self.assertEqual(store.get("RCA_STALE").job.status, "retry_wait")
        with self.assertRaises(JobLeaseLostError):
            store.complete(
                worker_id="dead-worker",
                job_id="RCA_STALE",
                state=replace(
                    claimed.state,
                    job=replace(claimed.state.job, status="completed"),
                ),
            )
        self.assertEqual(store.list_attempts("RCA_STALE")[0].status, "abandoned")

    def test_cancel_is_immediate_before_claim_and_cooperative_while_running(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_CANCEL_QUEUED"))
        cancelled = store.request_cancel("RCA_CANCEL_QUEUED")
        assert cancelled is not None
        self.assertEqual(cancelled.state.job.status, "cancelled")

        store.enqueue(queue_record("RCA_CANCEL_RUNNING"))
        claimed = store.claim_next(
            worker_id="worker",
            lease_seconds=60,
        )
        assert claimed is not None
        requested = store.request_cancel("RCA_CANCEL_RUNNING")
        assert requested is not None
        self.assertEqual(requested.state.job.status, "cancel_requested")
        committed = store.complete(
            worker_id="worker",
            job_id="RCA_CANCEL_RUNNING",
            state=replace(
                claimed.state,
                job=replace(claimed.state.job, status="completed"),
            ),
        )
        self.assertEqual(committed.state.job.status, "cancelled")

        store.enqueue(queue_record("RCA_NOT_CANCELLABLE"))
        active = store.claim_next(worker_id="worker", lease_seconds=60)
        assert active is not None
        store.complete(
            worker_id="worker",
            job_id="RCA_NOT_CANCELLABLE",
            state=replace(
                active.state,
                job=replace(active.state.job, status="completed"),
            ),
        )
        with self.assertRaises(JobNotCancellableError):
            store.request_cancel("RCA_NOT_CANCELLABLE")

    def test_cancel_api_returns_cancelled_and_is_idempotent(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(SEED_DIR),
            store=InMemoryRCAJobStore(),
        )
        with TestClient(app) as client:
            created = client.post("/rca/jobs", json={"user_query": QUERY}).json()
            first = client.post(created["cancel_url"])
            repeated = client.post(created["cancel_url"])
            state = client.get(created["state_url"])

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["status"], "cancelled")
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(state.json()["status"], "cancelled")

    def test_sse_replays_ordered_events_after_cursor_and_closes_at_terminal(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_SSE"))
        claimed = store.claim_next(worker_id="worker-sse", lease_seconds=60)
        assert claimed is not None
        store.record_progress_event(
            worker_id="worker-sse",
            job_id="RCA_SSE",
            event_type="action_started",
            payload={
                "action_id": "ACTION_1",
                "action_kind": "inspect_defect_pattern",
                "agent": "defect_wat",
                "reason": "Inspect the observed scratch.",
            },
        )
        completed_state = replace(
            claimed.state,
            job=replace(claimed.state.job, status=TaskStatus.COMPLETED.value),
        )
        store.complete(
            worker_id="worker-sse",
            job_id="RCA_SSE",
            state=completed_state,
        )
        app = create_app(workflow=build_csv_workflow(SEED_DIR), store=store)

        with TestClient(app) as client:
            response = client.get("/rca/jobs/RCA_SSE/events?after=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        self.assertIn("id: 3\nevent: job_event", response.text)
        self.assertIn('"event_type":"action_started"', response.text)
        self.assertIn("id: 4\nevent: job_event", response.text)
        self.assertNotIn('"event_type":"job_queued"', response.text)
        self.assertEqual(response.headers["x-accel-buffering"], "no")

    def test_sse_rejects_unknown_job_and_invalid_last_event_id(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_SSE_CURSOR"))
        cancelled = store.request_cancel("RCA_SSE_CURSOR")
        assert cancelled is not None
        app = create_app(workflow=build_csv_workflow(SEED_DIR), store=store)

        with TestClient(app) as client:
            unknown = client.get("/rca/jobs/UNKNOWN/events")
            invalid = client.get(
                "/rca/jobs/RCA_SSE_CURSOR/events",
                headers={"Last-Event-ID": "not-an-integer"},
            )

        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.json()["detail"]["error_code"],
            "invalid_event_cursor",
        )

    def test_progress_events_require_active_lease_and_reject_secrets(self) -> None:
        store = InMemoryRCAJobStore()
        store.enqueue(queue_record("RCA_EVENT_SAFE"))
        with self.assertRaises(JobLeaseLostError):
            store.record_progress_event(
                worker_id="unowned-worker",
                job_id="RCA_EVENT_SAFE",
                event_type="agent_started",
                payload={"agent": "mes"},
            )
        claimed = store.claim_next(worker_id="event-worker", lease_seconds=60)
        assert claimed is not None
        with self.assertRaises(ValueError):
            store.record_progress_event(
                worker_id="event-worker",
                job_id="RCA_EVENT_SAFE",
                event_type="agent_started",
                payload={"DASHSCOPE_API_KEY": "must-not-persist"},
            )


if __name__ == "__main__":
    unittest.main()
