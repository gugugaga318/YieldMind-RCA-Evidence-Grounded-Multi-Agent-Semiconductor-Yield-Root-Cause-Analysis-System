"""Leased PostgreSQL Queue Worker for bounded RCA Workflow attempts."""

from __future__ import annotations

import logging
import os
import platform
import socket
from dataclasses import dataclass, replace
from threading import Event, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from yield_rca_core.llm_gateway import LLMCallError, LLMOutputValidationError
from yield_rca_core.models import RCAState, TaskStatus
from yield_rca_core.supervisor import SupervisorExecutionError
from yield_rca_core.workflow import PurePythonRCAWorkflow
from yield_rca_core.workflow_events import capture_workflow_events

from yield_rca_api.audit import AuditEvent, AuditSink
from yield_rca_api.memory import MemoryApprovalService, MemoryCandidateNotEligibleError
from yield_rca_api.observability import RCAMetrics
from yield_rca_api.store import JobLeaseLostError, RCAJobQueueRecord, RCAJobStore

LOGGER = logging.getLogger("yield_rca_worker")


def _select_orchestration_mode(
    workflow: PurePythonRCAWorkflow,
    *,
    investigation_mode: str,
    lot_id: str | None,
    user_query: str,
) -> tuple[str, str | None]:
    if workflow.orchestration_mode == "llm_react":
        return "llm_react", None
    if workflow.orchestration_mode != "controlled_react":
        return "fixed", None
    if investigation_mode != "lot" or not lot_id:
        return "fixed", "controlled_react_requires_lot_investigation"
    if not any(term in user_query.lower() for term in ("scratch", "缺陷", "划伤", "刮伤")):
        return "fixed", "controlled_react_requires_explicit_defect_clue"
    return "controlled_react", None


@dataclass(frozen=True)
class WorkerSettings:
    lease_seconds: float = 180.0
    heartbeat_seconds: float = 30.0
    poll_seconds: float = 1.0
    recovery_seconds: float = 30.0
    retry_base_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name in (
            "lease_seconds",
            "heartbeat_seconds",
            "poll_seconds",
            "recovery_seconds",
            "retry_base_seconds",
        ):
            value = float(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")

    @classmethod
    def from_env(cls) -> WorkerSettings:
        return cls(
            lease_seconds=float(os.getenv("YIELD_RCA_WORKER_LEASE_SECONDS", "180")),
            heartbeat_seconds=float(
                os.getenv("YIELD_RCA_WORKER_HEARTBEAT_SECONDS", "30")
            ),
            poll_seconds=float(os.getenv("YIELD_RCA_WORKER_POLL_SECONDS", "1")),
            recovery_seconds=float(
                os.getenv("YIELD_RCA_WORKER_RECOVERY_SECONDS", "30")
            ),
            retry_base_seconds=float(
                os.getenv("YIELD_RCA_WORKER_RETRY_BASE_SECONDS", "5")
            ),
        )


def default_worker_id() -> str:
    configured = os.getenv("YIELD_RCA_WORKER_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"


class WorkerRuntimeConfigurationError(RuntimeError):
    """Raised before execution when the Worker cannot honor a Job snapshot."""


def _find_nested_error(
    error: BaseException,
    error_types: type[BaseException] | tuple[type[BaseException], ...],
) -> BaseException | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, error_types):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def classify_worker_error(error: BaseException) -> tuple[dict[str, Any], bool]:
    """Classify only transient provider/transport failures as retryable."""

    llm_error = _find_nested_error(error, LLMCallError)
    if isinstance(llm_error, LLMCallError):
        provider_code = (llm_error.provider_code or "").casefold()
        if provider_code == "arrearage":
            code, retryable = "LLM_BILLING_ERROR", False
        elif llm_error.status_code in {401, 403}:
            code, retryable = "LLM_AUTH_FAILED", False
        elif llm_error.status_code == 429:
            code, retryable = "LLM_RATE_LIMITED", True
        elif llm_error.status_code is None or (
            llm_error.status_code is not None and llm_error.status_code >= 500
        ):
            code, retryable = "LLM_UNAVAILABLE", True
        else:
            code, retryable = "LLM_PROVIDER_ERROR", False
        return (
            {
                "error_code": code,
                "message": {
                    "LLM_BILLING_ERROR": "The LLM account is not in good standing.",
                    "LLM_AUTH_FAILED": "The LLM provider rejected runtime credentials.",
                    "LLM_RATE_LIMITED": "The LLM provider rate limit was exceeded.",
                    "LLM_UNAVAILABLE": "The LLM provider was unavailable or timed out.",
                    "LLM_PROVIDER_ERROR": "The LLM provider rejected the request.",
                }[code],
                "retryable": retryable,
                "provider_code": llm_error.provider_code,
                "provider_request_id": llm_error.request_id,
            },
            retryable,
        )
    if _find_nested_error(error, LLMOutputValidationError) is not None:
        return (
            {
                "error_code": "LLM_OUTPUT_INVALID",
                "message": "Qwen output violated the structured Agent contract.",
                "retryable": False,
            },
            False,
        )
    if isinstance(error, SupervisorExecutionError) and error.error_code:
        return (
            {
                "error_code": error.error_code,
                "message": "The RCA Supervisor rejected the bounded Workflow attempt.",
                "retryable": False,
            },
            False,
        )
    if isinstance(error, WorkerRuntimeConfigurationError):
        return (
            {
                "error_code": "WORKER_RUNTIME_CONFIG_MISMATCH",
                "message": "Worker runtime does not match the immutable Job configuration.",
                "retryable": False,
            },
            False,
        )
    return (
        {
            "error_code": "WORKFLOW_EXECUTION_FAILED",
            "message": "RCA Workflow execution failed.",
            "retryable": False,
        },
        False,
    )


class RCAQueueWorker:
    """Claim one Job at a time and commit results only while its lease is valid."""

    def __init__(
        self,
        *,
        store: RCAJobStore,
        workflow: PurePythonRCAWorkflow,
        audit_sink: AuditSink,
        memory_service: MemoryApprovalService,
        metrics: RCAMetrics,
        runtime_dataset: str,
        settings: WorkerSettings | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.workflow = workflow
        self.audit_sink = audit_sink
        self.memory_service = memory_service
        self.metrics = metrics
        self.runtime_dataset = runtime_dataset
        self.settings = settings or WorkerSettings.from_env()
        self.worker_id = worker_id or default_worker_id()

    def _record_audit(self, event: AuditEvent) -> None:
        try:
            self.audit_sink.record_event(event)
        except Exception:
            LOGGER.warning("Worker audit write failed", exc_info=True)

    def _record_usage(self, state: RCAState, correlation_id: str) -> None:
        for usage in state.llm_usage:
            try:
                self.audit_sink.record_llm_usage(
                    job_id=state.job.job_id,
                    correlation_id=correlation_id,
                    usage=usage,
                )
            except Exception:
                LOGGER.warning("Worker LLM usage audit write failed", exc_info=True)

    def _heartbeat_loop(self, job_id: str, stop: Event, lease_lost: Event) -> None:
        while not stop.wait(self.settings.heartbeat_seconds):
            try:
                expires_at = self.store.heartbeat(
                    worker_id=self.worker_id,
                    job_id=job_id,
                    lease_seconds=self.settings.lease_seconds,
                )
                if expires_at is None:
                    lease_lost.set()
                    return
                if stop.is_set():
                    return
                self.store.record_worker_heartbeat(
                    self.worker_id,
                    active_lease_count=1,
                    metadata={"runtime": platform.python_version()},
                )
            except Exception:
                LOGGER.warning("Worker lease heartbeat failed", exc_info=True)
                lease_lost.set()
                return

    def _record_idle_heartbeat(self) -> None:
        try:
            self.store.record_worker_heartbeat(
                self.worker_id,
                active_lease_count=0,
                metadata={"runtime": platform.python_version()},
            )
        except Exception:
            LOGGER.warning("Worker idle heartbeat write failed", exc_info=True)

    def _validate_runtime_config(self, record: RCAJobQueueRecord) -> None:
        expected = record.runtime_config
        actual = {
            "agent_mode": self.workflow.llm_settings.agent_mode,
            "provider": self.workflow.llm_settings.provider,
            "model": self.workflow.llm_settings.model,
            "orchestration_mode": self.workflow.orchestration_mode,
            "dataset": self.runtime_dataset,
        }
        mismatches = {
            key: {"expected": expected.get(key), "actual": value}
            for key, value in actual.items()
            if expected.get(key) != value
        }
        if mismatches:
            raise WorkerRuntimeConfigurationError(
                "Worker runtime does not match the immutable Job configuration: "
                + ", ".join(sorted(mismatches))
            )

    def _run_workflow(self, record: RCAJobQueueRecord) -> RCAState:
        self._validate_runtime_config(record)
        request = record.request
        user_query = str(request["user_query"])
        lot_id = request.get("lot_id")
        investigation_mode = str(request.get("investigation_mode", "product_window"))
        selected_mode, fallback_reason = _select_orchestration_mode(
            self.workflow,
            investigation_mode=investigation_mode,
            lot_id=str(lot_id) if lot_id is not None else None,
            user_query=user_query,
        )
        def record_progress(event_type: str, payload: dict[str, Any]) -> None:
            self.store.record_progress_event(
                worker_id=self.worker_id,
                job_id=record.state.job.job_id,
                event_type=event_type,
                payload=payload,
            )

        with capture_workflow_events(record_progress):
            completed = self.workflow.run(
                user_query,
                job_id=record.state.job.job_id,
                plan_id=f"PLAN_{record.state.job.job_id}",
                lot_id=str(lot_id) if lot_id is not None else None,
                orchestration_mode_override=selected_mode,
            )
        execution_metadata = {
            **completed.execution_metadata,
            "orchestration_requested_mode": self.workflow.orchestration_mode,
            "queue_attempt_number": record.attempt_count,
            "queue_worker_id": self.worker_id,
        }
        if fallback_reason and self.workflow.orchestration_mode == "controlled_react":
            execution_metadata["orchestration_fallback_reason"] = fallback_reason
        return replace(completed, execution_metadata=execution_metadata)

    def run_once(self) -> bool:
        self._record_idle_heartbeat()
        record = self.store.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self.settings.lease_seconds,
        )
        if record is None:
            return False
        job_id = record.state.job.job_id
        correlation_id = f"WORKER_{job_id}_{record.attempt_count}"
        stop_heartbeat = Event()
        lease_lost = Event()
        heartbeat = Thread(
            target=self._heartbeat_loop,
            args=(job_id, stop_heartbeat, lease_lost),
            daemon=True,
        )
        heartbeat.start()
        started = monotonic()
        try:
            completed_state = self._run_workflow(record)
            if lease_lost.is_set():
                raise JobLeaseLostError(f"Worker lease was lost for Job {job_id}")
            committed = self.store.complete(
                worker_id=self.worker_id,
                job_id=job_id,
                state=completed_state,
                checkpoint={
                    "stage": "workflow_completed",
                    "evidence_count": len(completed_state.evidence),
                    "finding_count": len(completed_state.findings),
                },
            )
            if committed.state.job.status == TaskStatus.COMPLETED.value:
                memory_candidate = None
                controlled_fast_path = completed_state.execution_metadata.get(
                    "orchestration_mode"
                ) in {"controlled_react", "llm_react"}
                try:
                    if not controlled_fast_path:
                        memory_candidate = self.memory_service.create_from_state(
                            completed_state
                        )
                except MemoryCandidateNotEligibleError:
                    pass
                except Exception:
                    LOGGER.warning(
                        "Completed Job memory candidate creation failed",
                        extra={"job_id": job_id, "outcome": "memory_failed"},
                        exc_info=True,
                    )
                self.metrics.observe_state(completed_state, outcome="success")
                self._record_usage(completed_state, correlation_id)
                self._record_audit(
                    AuditEvent(
                        action="RCA_JOB_COMPLETED",
                        job_id=job_id,
                        correlation_id=correlation_id,
                        actor=self.worker_id,
                        outcome="success",
                        details={
                            "attempt_number": record.attempt_count,
                            "duration_ms": round((monotonic() - started) * 1000, 3),
                            "memory_candidate_id": (
                                memory_candidate.candidate_id
                                if memory_candidate is not None
                                else None
                            ),
                        },
                    )
                )
            else:
                self._record_audit(
                    AuditEvent(
                        action="RCA_JOB_CANCELLED",
                        job_id=job_id,
                        correlation_id=correlation_id,
                        actor=self.worker_id,
                        outcome="cancelled",
                        details={"attempt_number": record.attempt_count},
                    )
                )
        except JobLeaseLostError:
            LOGGER.warning(
                "Worker discarded an uncommitted result after lease loss",
                extra={"job_id": job_id, "outcome": "lease_lost"},
            )
        except Exception as exc:
            error, retryable = classify_worker_error(exc)
            failed_state = exc.state if isinstance(exc, SupervisorExecutionError) else None
            retry_after = self.settings.retry_base_seconds * (2 ** (record.attempt_count - 1))
            try:
                updated = self.store.fail_attempt(
                    worker_id=self.worker_id,
                    job_id=job_id,
                    error=error,
                    retryable=retryable,
                    retry_after_seconds=retry_after,
                    state=failed_state,
                    checkpoint={"stage": "workflow_failed"},
                )
            except JobLeaseLostError:
                LOGGER.warning(
                    "Worker failure was not committed after lease loss",
                    extra={"job_id": job_id, "outcome": "lease_lost"},
                )
            except Exception:
                LOGGER.warning(
                    "Worker could not persist attempt failure; lease recovery will decide it",
                    extra={"job_id": job_id, "outcome": "persistence_failed"},
                    exc_info=True,
                )
            else:
                outcome = updated.state.job.status
                self._record_audit(
                    AuditEvent(
                        action=(
                            "RCA_JOB_RETRY_SCHEDULED"
                            if outcome == TaskStatus.RETRY_WAIT.value
                            else "RCA_JOB_CANCELLED"
                            if outcome == TaskStatus.CANCELLED.value
                            else "RCA_JOB_FAILED"
                        ),
                        job_id=job_id,
                        correlation_id=correlation_id,
                        actor=self.worker_id,
                        outcome=outcome,
                        details={
                            "attempt_number": record.attempt_count,
                            "error_code": error["error_code"],
                            "retryable": retryable,
                        },
                    )
                )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.settings.heartbeat_seconds))
            self._record_idle_heartbeat()
        return True

    def run_forever(self, stop: Event | None = None) -> None:
        stop_event = stop or Event()
        last_recovery = 0.0
        while not stop_event.is_set():
            try:
                now = monotonic()
                if now - last_recovery >= self.settings.recovery_seconds:
                    self.store.recover_stale_leases(
                        retry_after_seconds=self.settings.retry_base_seconds
                    )
                    last_recovery = now
                worked = self.run_once()
            except Exception:
                LOGGER.warning("Worker loop failed and will retry", exc_info=True)
                worked = False
            if not worked:
                stop_event.wait(self.settings.poll_seconds)
