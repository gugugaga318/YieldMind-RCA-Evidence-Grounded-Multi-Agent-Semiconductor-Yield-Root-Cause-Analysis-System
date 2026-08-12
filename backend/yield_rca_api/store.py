"""Persistence adapters for RCA API job state and durable queue metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Protocol

from yield_rca_core.models import RCAState, TaskStatus


class DuplicateJobError(ValueError):
    """Raised when a job identifier is reserved more than once."""


class IdempotencyConflictError(ValueError):
    """Raised when one idempotency key is reused for a different request."""

    error_code = "idempotency_conflict"


class InvalidJobTransitionError(ValueError):
    """Raised when persisted queue state attempts an illegal transition."""


class JobLeaseLostError(RuntimeError):
    """Raised when a Worker tries to commit without owning the active lease."""


class JobNotCancellableError(ValueError):
    """Raised when cancellation is requested after a non-cancelled terminal result."""

    error_code = "job_not_cancellable"


ACTIVE_JOB_STATUSES = frozenset(
    {
        TaskStatus.QUEUED.value,
        TaskStatus.RUNNING.value,
        TaskStatus.RETRY_WAIT.value,
        TaskStatus.CANCEL_REQUESTED.value,
    }
)
TERMINAL_JOB_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }
)
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.QUEUED.value: frozenset(
        {TaskStatus.RUNNING.value, TaskStatus.CANCELLED.value}
    ),
    TaskStatus.RUNNING.value: frozenset(
        {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.RETRY_WAIT.value,
            TaskStatus.CANCEL_REQUESTED.value,
        }
    ),
    TaskStatus.RETRY_WAIT.value: frozenset(
        {TaskStatus.RUNNING.value, TaskStatus.CANCELLED.value}
    ),
    TaskStatus.CANCEL_REQUESTED.value: frozenset({TaskStatus.CANCELLED.value}),
    # Read compatibility for pre-Batch-23 snapshots.
    TaskStatus.PENDING.value: frozenset(
        {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.CANCELLED.value}
    ),
    TaskStatus.SKIPPED.value: frozenset(),
    TaskStatus.COMPLETED.value: frozenset(),
    TaskStatus.FAILED.value: frozenset(),
    TaskStatus.CANCELLED.value: frozenset(),
}
_FORBIDDEN_QUEUE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "chainofthought",
        "dashscopeapikey",
        "hiddenreasoning",
    }
)


def _validate_no_sensitive_fields(value: Any, field_name: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = "".join(
                character for character in str(key).lower() if character.isalnum()
            )
            if normalized_key in _FORBIDDEN_QUEUE_KEYS:
                raise ValueError(f"{field_name} must not persist sensitive field: {key}")
            _validate_no_sensitive_fields(item, field_name)
    elif isinstance(value, list | tuple):
        for item in value:
            _validate_no_sensitive_fields(item, field_name)


def validate_job_transition(current_status: str, next_status: str) -> None:
    """Enforce the Python-owned queue lifecycle, including terminal immutability."""

    if current_status == next_status:
        return
    allowed = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
    if next_status not in allowed:
        raise InvalidJobTransitionError(
            f"illegal RCA job transition: {current_status} -> {next_status}"
        )


@dataclass(frozen=True)
class RCAJobQueueRecord:
    """Durable queue envelope; secrets and hidden model reasoning are never fields."""

    state: RCAState
    request: dict[str, Any]
    request_hash: str
    idempotency_key: str | None
    runtime_config: dict[str, Any]
    priority: int = 0
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    cancel_requested_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if len(self.request_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.request_hash
        ):
            raise ValueError("request_hash must be a SHA-256 hexadecimal digest")
        if self.idempotency_key is not None and not 1 <= len(self.idempotency_key) <= 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.version < 1:
            raise ValueError("version must be at least 1")
        for field_name in ("request", "runtime_config", "error", "checkpoint"):
            _validate_no_sensitive_fields(getattr(self, field_name), field_name)


@dataclass(frozen=True)
class RCAJobAttemptRecord:
    job_id: str
    attempt_number: int
    worker_id: str
    status: str
    started_at: str
    completed_at: str | None = None
    error: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None


@dataclass(frozen=True)
class RCAJobEventRecord:
    job_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


def _legacy_record(state: RCAState) -> RCAJobQueueRecord:
    """Wrap legacy create/save callers without inventing an idempotency identity."""

    import hashlib

    request = {
        "investigation_mode": state.job.investigation_mode,
        "user_query": state.job.user_query,
        "lot_id": state.job.source_lot_id,
    }
    request_hash = hashlib.sha256(
        json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return RCAJobQueueRecord(
        state=state,
        request=request,
        request_hash=request_hash,
        idempotency_key=None,
        runtime_config=dict(state.execution_metadata),
    )


class RCAJobStore(Protocol):
    def enqueue(self, record: RCAJobQueueRecord) -> RCAJobQueueRecord: ...

    def get_record(self, job_id: str) -> RCAJobQueueRecord | None: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None: ...

    def heartbeat(
        self,
        *,
        worker_id: str,
        job_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> str | None: ...

    def complete(
        self,
        *,
        worker_id: str,
        job_id: str,
        state: RCAState,
        checkpoint: dict[str, Any] | None = None,
    ) -> RCAJobQueueRecord: ...

    def fail_attempt(
        self,
        *,
        worker_id: str,
        job_id: str,
        error: dict[str, Any],
        retryable: bool,
        retry_after_seconds: float,
        state: RCAState | None = None,
        checkpoint: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord: ...

    def request_cancel(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None: ...

    def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
        retry_after_seconds: float = 0,
    ) -> int: ...

    def record_worker_heartbeat(
        self,
        worker_id: str,
        *,
        active_lease_count: int,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None: ...

    def list_attempts(self, job_id: str) -> list[RCAJobAttemptRecord]: ...

    def list_events(self, job_id: str) -> list[RCAJobEventRecord]: ...

    def create(self, state: RCAState) -> None: ...

    def save(self, state: RCAState) -> None: ...

    def get(self, job_id: str) -> RCAState | None: ...

    def check_ready(self) -> None: ...


class InMemoryRCAJobStore:
    """Thread-safe queue adapter used by tests and local non-PostgreSQL runs."""

    def __init__(self) -> None:
        self._records: dict[str, RCAJobQueueRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._attempts: dict[str, list[RCAJobAttemptRecord]] = {}
        self._events: dict[str, list[RCAJobEventRecord]] = {}
        self._worker_heartbeats: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _as_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @classmethod
    def _owns_active_lease(
        cls,
        record: RCAJobQueueRecord,
        worker_id: str,
        now: datetime,
    ) -> bool:
        expires_at = cls._as_datetime(record.lease_expires_at)
        return (
            record.lease_owner == worker_id
            and expires_at is not None
            and expires_at > now
        )

    def _append_event(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> RCAJobEventRecord:
        events = self._events.setdefault(job_id, [])
        event = RCAJobEventRecord(
            job_id=job_id,
            sequence=len(events) + 1,
            event_type=event_type,
            payload=dict(payload),
            created_at=now.isoformat(),
        )
        events.append(event)
        return event

    def _replace_attempt(
        self,
        job_id: str,
        attempt_number: int,
        **updates: Any,
    ) -> None:
        attempts = self._attempts.get(job_id, [])
        for index, attempt in enumerate(attempts):
            if attempt.attempt_number == attempt_number:
                attempts[index] = replace(attempt, **updates)
                return

    @staticmethod
    def _cancelled_state(state: RCAState) -> RCAState:
        return replace(
            state,
            job=replace(state.job, status=TaskStatus.CANCELLED.value),
        )

    def enqueue(self, record: RCAJobQueueRecord) -> RCAJobQueueRecord:
        job_id = record.state.job.job_id
        with self._lock:
            if record.idempotency_key is not None:
                existing_id = self._idempotency_index.get(record.idempotency_key)
                if existing_id is not None:
                    existing = self._records[existing_id]
                    if existing.request_hash != record.request_hash:
                        raise IdempotencyConflictError(
                            "Idempotency-Key is already bound to a different RCA request"
                        )
                    return existing
            if job_id in self._records:
                raise DuplicateJobError(f"job already exists: {job_id}")
            self._records[job_id] = record
            if record.idempotency_key is not None:
                self._idempotency_index[record.idempotency_key] = job_id
            now = self._now(None)
            self._append_event(
                job_id,
                "job_queued",
                {"status": TaskStatus.QUEUED.value},
                now,
            )
            return record

    def get_record(self, job_id: str) -> RCAJobQueueRecord | None:
        with self._lock:
            return self._records.get(job_id)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = self._now(now)
        with self._lock:
            candidates = [
                record
                for record in self._records.values()
                if record.state.job.status
                in {TaskStatus.QUEUED.value, TaskStatus.RETRY_WAIT.value}
                and (
                    record.next_attempt_at is None
                    or self._as_datetime(record.next_attempt_at) <= claimed_at
                )
            ]
            if not candidates:
                return None
            record = sorted(
                candidates,
                key=lambda item: (-item.priority, item.state.job.created_at),
            )[0]
            job_id = record.state.job.job_id
            running_state = replace(
                record.state,
                job=replace(record.state.job, status=TaskStatus.RUNNING.value),
            )
            attempt_number = record.attempt_count + 1
            claimed = replace(
                record,
                state=running_state,
                attempt_count=attempt_number,
                next_attempt_at=None,
                lease_owner=worker_id,
                lease_expires_at=(claimed_at + timedelta(seconds=lease_seconds)).isoformat(),
                heartbeat_at=claimed_at.isoformat(),
                started_at=record.started_at or claimed_at.isoformat(),
                error=None,
                version=record.version + 1,
            )
            self._records[job_id] = claimed
            self._attempts.setdefault(job_id, []).append(
                RCAJobAttemptRecord(
                    job_id=job_id,
                    attempt_number=attempt_number,
                    worker_id=worker_id,
                    status=TaskStatus.RUNNING.value,
                    started_at=claimed_at.isoformat(),
                )
            )
            self._append_event(
                job_id,
                "job_started",
                {"status": "running", "attempt_number": attempt_number},
                claimed_at,
            )
            return claimed

    def heartbeat(
        self,
        *,
        worker_id: str,
        job_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> str | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        heartbeat_at = self._now(now)
        with self._lock:
            record = self._records.get(job_id)
            current_expiry = (
                self._as_datetime(record.lease_expires_at)
                if record is not None
                else None
            )
            if (
                record is None
                or record.lease_owner != worker_id
                or record.state.job.status
                not in {TaskStatus.RUNNING.value, TaskStatus.CANCEL_REQUESTED.value}
                or current_expiry is None
                or current_expiry <= heartbeat_at
            ):
                return None
            expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
            self._records[job_id] = replace(
                record,
                heartbeat_at=heartbeat_at.isoformat(),
                lease_expires_at=expires_at.isoformat(),
                version=record.version + 1,
            )
            return expires_at.isoformat()

    def complete(
        self,
        *,
        worker_id: str,
        job_id: str,
        state: RCAState,
        checkpoint: dict[str, Any] | None = None,
    ) -> RCAJobQueueRecord:
        completed_at = self._now(None)
        with self._lock:
            record = self._records.get(job_id)
            if record is None or not self._owns_active_lease(
                record, worker_id, completed_at
            ):
                raise JobLeaseLostError(f"Worker {worker_id} does not own Job {job_id}")
            if record.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                final_status = TaskStatus.CANCELLED.value
                final_state = self._cancelled_state(record.state)
                event_type = "job_cancelled"
            elif record.state.job.status == TaskStatus.RUNNING.value:
                final_status = TaskStatus.COMPLETED.value
                final_state = replace(
                    state,
                    job=replace(state.job, status=TaskStatus.COMPLETED.value),
                )
                event_type = "job_completed"
            else:
                raise JobLeaseLostError(f"Job {job_id} no longer has a committable lease")
            final_record = replace(
                record,
                state=final_state,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                completed_at=completed_at.isoformat(),
                checkpoint=checkpoint,
                error=None,
                version=record.version + 1,
            )
            self._records[job_id] = final_record
            self._replace_attempt(
                job_id,
                record.attempt_count,
                status=final_status,
                completed_at=completed_at.isoformat(),
                checkpoint=checkpoint,
            )
            self._append_event(
                job_id,
                event_type,
                {"status": final_status, "attempt_number": record.attempt_count},
                completed_at,
            )
            return final_record

    def fail_attempt(
        self,
        *,
        worker_id: str,
        job_id: str,
        error: dict[str, Any],
        retryable: bool,
        retry_after_seconds: float,
        state: RCAState | None = None,
        checkpoint: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord:
        failed_at = self._now(now)
        with self._lock:
            record = self._records.get(job_id)
            if record is None or not self._owns_active_lease(
                record, worker_id, failed_at
            ):
                raise JobLeaseLostError(f"Worker {worker_id} does not own Job {job_id}")
            if record.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                next_status = TaskStatus.CANCELLED.value
            elif retryable and record.attempt_count < record.max_attempts:
                next_status = TaskStatus.RETRY_WAIT.value
            else:
                next_status = TaskStatus.FAILED.value
            base_state = state or record.state
            next_state = replace(
                base_state,
                job=replace(base_state.job, status=next_status),
            )
            next_attempt_at = (
                (failed_at + timedelta(seconds=max(0, retry_after_seconds))).isoformat()
                if next_status == TaskStatus.RETRY_WAIT.value
                else None
            )
            completed_at = (
                failed_at.isoformat() if next_status in TERMINAL_JOB_STATUSES else None
            )
            updated = replace(
                record,
                state=next_state,
                next_attempt_at=next_attempt_at,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                completed_at=completed_at,
                error=dict(error),
                checkpoint=checkpoint,
                version=record.version + 1,
            )
            self._records[job_id] = updated
            self._replace_attempt(
                job_id,
                record.attempt_count,
                status=(
                    "failed" if next_status == TaskStatus.RETRY_WAIT.value else next_status
                ),
                completed_at=failed_at.isoformat(),
                error=dict(error),
                checkpoint=checkpoint,
            )
            event_type = {
                TaskStatus.RETRY_WAIT.value: "job_retry_scheduled",
                TaskStatus.CANCELLED.value: "job_cancelled",
                TaskStatus.FAILED.value: "job_failed",
            }[next_status]
            self._append_event(
                job_id,
                event_type,
                {
                    "status": next_status,
                    "attempt_number": record.attempt_count,
                    "retryable": retryable,
                    "next_attempt_at": next_attempt_at,
                    "error": dict(error),
                },
                failed_at,
            )
            return updated

    def request_cancel(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None:
        requested_at = self._now(now)
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return None
            current_status = record.state.job.status
            if current_status == TaskStatus.CANCELLED.value:
                return record
            if current_status in {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}:
                raise JobNotCancellableError(
                    f"RCA Job {job_id} is already {current_status}"
                )
            immediate = current_status in {
                TaskStatus.QUEUED.value,
                TaskStatus.RETRY_WAIT.value,
            }
            next_status = (
                TaskStatus.CANCELLED.value
                if immediate
                else TaskStatus.CANCEL_REQUESTED.value
            )
            validate_job_transition(current_status, next_status)
            next_state = replace(
                record.state,
                job=replace(record.state.job, status=next_status),
            )
            updated = replace(
                record,
                state=next_state,
                cancel_requested_at=requested_at.isoformat(),
                completed_at=requested_at.isoformat() if immediate else record.completed_at,
                next_attempt_at=None if immediate else record.next_attempt_at,
                version=record.version + 1,
            )
            self._records[job_id] = updated
            self._append_event(
                job_id,
                "job_cancelled" if immediate else "job_cancel_requested",
                {"status": next_status},
                requested_at,
            )
            return updated

    def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
        retry_after_seconds: float = 0,
    ) -> int:
        recovered_at = self._now(now)
        with self._lock:
            stale_job_ids = [
                job_id
                for job_id, record in self._records.items()
                if record.state.job.status
                in {TaskStatus.RUNNING.value, TaskStatus.CANCEL_REQUESTED.value}
                and record.lease_expires_at is not None
                and self._as_datetime(record.lease_expires_at) <= recovered_at
            ]
            for job_id in stale_job_ids:
                record = self._records[job_id]
                if record.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                    next_status = TaskStatus.CANCELLED.value
                elif record.attempt_count < record.max_attempts:
                    next_status = TaskStatus.RETRY_WAIT.value
                else:
                    next_status = TaskStatus.FAILED.value
                error = {
                    "error_code": "WORKER_LEASE_EXPIRED",
                    "message": "Worker lease expired before the attempt committed.",
                    "retryable": next_status == TaskStatus.RETRY_WAIT.value,
                }
                state = replace(
                    record.state,
                    job=replace(record.state.job, status=next_status),
                )
                updated = replace(
                    record,
                    state=state,
                    next_attempt_at=(
                        (recovered_at + timedelta(seconds=retry_after_seconds)).isoformat()
                        if next_status == TaskStatus.RETRY_WAIT.value
                        else None
                    ),
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    completed_at=(
                        recovered_at.isoformat()
                        if next_status in TERMINAL_JOB_STATUSES
                        else None
                    ),
                    error=error,
                    version=record.version + 1,
                )
                self._records[job_id] = updated
                self._replace_attempt(
                    job_id,
                    record.attempt_count,
                    status="abandoned",
                    completed_at=recovered_at.isoformat(),
                    error=error,
                )
                self._append_event(
                    job_id,
                    "job_cancelled"
                    if next_status == TaskStatus.CANCELLED.value
                    else "job_lease_recovered",
                    {"status": next_status, "attempt_number": record.attempt_count},
                    recovered_at,
                )
            return len(stale_job_ids)

    def record_worker_heartbeat(
        self,
        worker_id: str,
        *,
        active_lease_count: int,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if active_lease_count < 0:
            raise ValueError("active_lease_count must be non-negative")
        heartbeat_at = self._now(now)
        with self._lock:
            started_at = self._worker_heartbeats.get(worker_id, {}).get(
                "started_at", heartbeat_at.isoformat()
            )
            self._worker_heartbeats[worker_id] = {
                "started_at": started_at,
                "last_seen_at": heartbeat_at.isoformat(),
                "active_lease_count": active_lease_count,
                "metadata": dict(metadata or {}),
            }

    def list_attempts(self, job_id: str) -> list[RCAJobAttemptRecord]:
        with self._lock:
            return list(self._attempts.get(job_id, []))

    def list_events(self, job_id: str) -> list[RCAJobEventRecord]:
        with self._lock:
            return list(self._events.get(job_id, []))

    def create(self, state: RCAState) -> None:
        self.enqueue(_legacy_record(state))

    def save(self, state: RCAState) -> None:
        with self._lock:
            current = self._records.get(state.job.job_id)
            if current is None:
                self._records[state.job.job_id] = _legacy_record(state)
                return
            validate_job_transition(current.state.job.status, state.job.status)
            completed_at = current.completed_at
            if state.job.status in TERMINAL_JOB_STATUSES and completed_at is None:
                completed_at = datetime.now(UTC).isoformat()
            self._records[state.job.job_id] = replace(
                current,
                state=state,
                completed_at=completed_at,
                version=current.version + 1,
            )

    def get(self, job_id: str) -> RCAState | None:
        record = self.get_record(job_id)
        return record.state if record is not None else None

    def check_ready(self) -> None:
        return None


class PostgresRCAJobStore:
    """Durable PostgreSQL queue and RCA State storage shared by API processes."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @staticmethod
    def _insert(cursor: Any, record: RCAJobQueueRecord) -> None:
        cursor.execute(
            """
            INSERT INTO rca_job_state (
                job_id, status, state, request, request_hash, idempotency_key,
                runtime_config, priority, attempt_count, max_attempts,
                next_attempt_at, lease_owner, lease_expires_at, heartbeat_at,
                cancel_requested_at, started_at, completed_at, error,
                checkpoint, version, created_at, updated_at
            ) VALUES (
                %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                %s, %s, now()
            )
            """,
            (
                record.state.job.job_id,
                record.state.job.status,
                json.dumps(record.state.to_dict()),
                json.dumps(record.request),
                record.request_hash,
                record.idempotency_key,
                json.dumps(record.runtime_config),
                record.priority,
                record.attempt_count,
                record.max_attempts,
                record.next_attempt_at,
                record.lease_owner,
                record.lease_expires_at,
                record.heartbeat_at,
                record.cancel_requested_at,
                record.started_at,
                record.completed_at,
                json.dumps(record.error) if record.error is not None else None,
                json.dumps(record.checkpoint) if record.checkpoint is not None else None,
                record.version,
                record.state.job.created_at,
            ),
        )

    def enqueue(self, record: RCAJobQueueRecord) -> RCAJobQueueRecord:
        import psycopg

        try:
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
                with connection.cursor() as cursor:
                    self._insert(cursor, record)
                    self._append_event(
                        cursor,
                        job_id=record.state.job.job_id,
                        event_type="job_queued",
                        payload={"status": record.state.job.status},
                    )
            return record
        except psycopg.errors.UniqueViolation as exc:
            if record.idempotency_key is None:
                raise DuplicateJobError(
                    f"job already exists: {record.state.job.job_id}"
                ) from exc
            existing = self._get_by_idempotency_key(record.idempotency_key)
            if existing is None:
                raise DuplicateJobError(
                    f"job already exists: {record.state.job.job_id}"
                ) from exc
            if existing.request_hash != record.request_hash:
                raise IdempotencyConflictError(
                    "Idempotency-Key is already bound to a different RCA request"
                ) from exc
            return existing

    def _get_by_idempotency_key(self, key: str) -> RCAJobQueueRecord | None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM rca_job_state WHERE idempotency_key = %s",
                    (key,),
                )
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
        return self._record_from_row(columns, row) if row is not None else None

    def create(self, state: RCAState) -> None:
        self.enqueue(_legacy_record(state))

    def save(self, state: RCAState) -> None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, version FROM rca_job_state WHERE job_id = %s FOR UPDATE",
                    (state.job.job_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    self._insert(cursor, _legacy_record(state))
                    return
                validate_job_transition(str(row[0]), state.job.status)
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET status = %s,
                        state = %s::jsonb,
                        started_at = CASE
                            WHEN %s = 'running' THEN COALESCE(started_at, now())
                            ELSE started_at
                        END,
                        completed_at = CASE
                            WHEN %s IN ('completed', 'failed', 'cancelled')
                            THEN COALESCE(completed_at, now())
                            ELSE completed_at
                        END,
                        version = version + 1,
                        updated_at = now()
                    WHERE job_id = %s
                    """,
                    (
                        state.job.status,
                        json.dumps(state.to_dict()),
                        state.job.status,
                        state.job.status,
                        state.job.job_id,
                    ),
                )

    @staticmethod
    def _record_from_row(columns: list[str], row: Any) -> RCAJobQueueRecord:
        values = dict(zip(columns, row, strict=True))

        def json_value(name: str, default: Any) -> Any:
            value = values.get(name, default)
            return json.loads(value) if isinstance(value, str) else value

        def iso_value(name: str) -> str | None:
            value = values.get(name)
            return value.isoformat() if isinstance(value, datetime) else value

        return RCAJobQueueRecord(
            state=RCAState.from_dict(dict(json_value("state", {}))),
            request=dict(json_value("request", {})),
            request_hash=str(values.get("request_hash") or "0" * 64),
            idempotency_key=values.get("idempotency_key"),
            runtime_config=dict(json_value("runtime_config", {})),
            priority=int(values.get("priority", 0)),
            attempt_count=int(values.get("attempt_count", 0)),
            max_attempts=int(values.get("max_attempts", 3)),
            next_attempt_at=iso_value("next_attempt_at"),
            lease_owner=values.get("lease_owner"),
            lease_expires_at=iso_value("lease_expires_at"),
            heartbeat_at=iso_value("heartbeat_at"),
            cancel_requested_at=iso_value("cancel_requested_at"),
            started_at=iso_value("started_at"),
            completed_at=iso_value("completed_at"),
            error=json_value("error", None),
            checkpoint=json_value("checkpoint", None),
            version=int(values.get("version", 1)),
        )

    def get_record(self, job_id: str) -> RCAJobQueueRecord | None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM rca_job_state WHERE job_id = %s", (job_id,))
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
        return self._record_from_row(columns, row) if row is not None else None

    @staticmethod
    def _append_event(
        cursor: Any,
        *,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO rca_job_event (job_id, sequence, event_type, payload)
            SELECT %s, COALESCE(MAX(sequence), 0) + 1, %s, %s::jsonb
            FROM rca_job_event
            WHERE job_id = %s
            """,
            (job_id, event_type, json.dumps(payload), job_id),
        )

    @classmethod
    def _locked_record(cls, cursor: Any, job_id: str) -> RCAJobQueueRecord | None:
        cursor.execute(
            "SELECT * FROM rca_job_state WHERE job_id = %s FOR UPDATE",
            (job_id,),
        )
        row = cursor.fetchone()
        columns = [item.name for item in cursor.description or ()]
        return cls._record_from_row(columns, row) if row is not None else None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None:
        import psycopg

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or datetime.now(UTC)
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id
                    FROM rca_job_state
                    WHERE status IN ('queued', 'retry_wait')
                      AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                    ORDER BY priority DESC, created_at, job_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (claimed_at,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                job_id = str(row[0])
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET status = 'running',
                        state = jsonb_set(state, '{job,status}', '"running"'::jsonb, true),
                        attempt_count = attempt_count + 1,
                        next_attempt_at = NULL,
                        lease_owner = %s,
                        lease_expires_at = %s,
                        heartbeat_at = %s,
                        started_at = COALESCE(started_at, %s),
                        error = NULL,
                        version = version + 1,
                        updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        worker_id,
                        expires_at,
                        claimed_at,
                        claimed_at,
                        claimed_at,
                        job_id,
                    ),
                )
                claimed_row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
                claimed = self._record_from_row(columns, claimed_row)
                cursor.execute(
                    """
                    INSERT INTO rca_job_attempt (
                        job_id, attempt_number, worker_id, status, started_at
                    ) VALUES (%s, %s, %s, 'running', %s)
                    """,
                    (job_id, claimed.attempt_count, worker_id, claimed_at),
                )
                self._append_event(
                    cursor,
                    job_id=job_id,
                    event_type="job_started",
                    payload={
                        "status": TaskStatus.RUNNING.value,
                        "attempt_number": claimed.attempt_count,
                    },
                )
        return claimed

    def heartbeat(
        self,
        *,
        worker_id: str,
        job_id: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> str | None:
        import psycopg

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        heartbeat_at = now or datetime.now(UTC)
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        expires_at = heartbeat_at + timedelta(seconds=lease_seconds)
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET heartbeat_at = %s,
                        lease_expires_at = %s,
                        version = version + 1,
                        updated_at = %s
                    WHERE job_id = %s
                      AND lease_owner = %s
                      AND status IN ('running', 'cancel_requested')
                      AND lease_expires_at > %s
                    RETURNING lease_expires_at
                    """,
                    (
                        heartbeat_at,
                        expires_at,
                        heartbeat_at,
                        job_id,
                        worker_id,
                        heartbeat_at,
                    ),
                )
                row = cursor.fetchone()
        return row[0].isoformat() if row is not None else None

    def complete(
        self,
        *,
        worker_id: str,
        job_id: str,
        state: RCAState,
        checkpoint: dict[str, Any] | None = None,
    ) -> RCAJobQueueRecord:
        import psycopg

        completed_at = datetime.now(UTC)
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                current = self._locked_record(cursor, job_id)
                if (
                    current is None
                    or current.lease_owner != worker_id
                    or current.lease_expires_at is None
                    or datetime.fromisoformat(current.lease_expires_at) <= completed_at
                ):
                    raise JobLeaseLostError(
                        f"Worker {worker_id} does not own Job {job_id}"
                    )
                if current.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                    final_status = TaskStatus.CANCELLED.value
                    final_state = replace(
                        current.state,
                        job=replace(
                            current.state.job,
                            status=TaskStatus.CANCELLED.value,
                        ),
                    )
                    event_type = "job_cancelled"
                elif current.state.job.status == TaskStatus.RUNNING.value:
                    final_status = TaskStatus.COMPLETED.value
                    final_state = replace(
                        state,
                        job=replace(state.job, status=TaskStatus.COMPLETED.value),
                    )
                    event_type = "job_completed"
                else:
                    raise JobLeaseLostError(
                        f"Job {job_id} no longer has a committable lease"
                    )
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET status = %s,
                        state = %s::jsonb,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        completed_at = %s,
                        error = NULL,
                        checkpoint = %s::jsonb,
                        version = version + 1,
                        updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        final_status,
                        json.dumps(final_state.to_dict()),
                        completed_at,
                        json.dumps(checkpoint) if checkpoint is not None else None,
                        completed_at,
                        job_id,
                    ),
                )
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
                completed = self._record_from_row(columns, row)
                cursor.execute(
                    """
                    UPDATE rca_job_attempt
                    SET status = %s,
                        completed_at = %s,
                        checkpoint = %s::jsonb
                    WHERE job_id = %s AND attempt_number = %s
                    """,
                    (
                        final_status,
                        completed_at,
                        json.dumps(checkpoint) if checkpoint is not None else None,
                        job_id,
                        current.attempt_count,
                    ),
                )
                self._append_event(
                    cursor,
                    job_id=job_id,
                    event_type=event_type,
                    payload={
                        "status": final_status,
                        "attempt_number": current.attempt_count,
                    },
                )
        return completed

    def fail_attempt(
        self,
        *,
        worker_id: str,
        job_id: str,
        error: dict[str, Any],
        retryable: bool,
        retry_after_seconds: float,
        state: RCAState | None = None,
        checkpoint: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord:
        import psycopg

        failed_at = now or datetime.now(UTC)
        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(tzinfo=UTC)
        _validate_no_sensitive_fields(error, "error")
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                current = self._locked_record(cursor, job_id)
                if (
                    current is None
                    or current.lease_owner != worker_id
                    or current.lease_expires_at is None
                    or datetime.fromisoformat(current.lease_expires_at) <= failed_at
                ):
                    raise JobLeaseLostError(
                        f"Worker {worker_id} does not own Job {job_id}"
                    )
                if current.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                    next_status = TaskStatus.CANCELLED.value
                elif retryable and current.attempt_count < current.max_attempts:
                    next_status = TaskStatus.RETRY_WAIT.value
                else:
                    next_status = TaskStatus.FAILED.value
                base_state = state or current.state
                next_state = replace(
                    base_state,
                    job=replace(base_state.job, status=next_status),
                )
                next_attempt_at = (
                    failed_at + timedelta(seconds=max(0, retry_after_seconds))
                    if next_status == TaskStatus.RETRY_WAIT.value
                    else None
                )
                completed_at = (
                    failed_at if next_status in TERMINAL_JOB_STATUSES else None
                )
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET status = %s,
                        state = %s::jsonb,
                        next_attempt_at = %s,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        completed_at = %s,
                        error = %s::jsonb,
                        checkpoint = %s::jsonb,
                        version = version + 1,
                        updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        next_status,
                        json.dumps(next_state.to_dict()),
                        next_attempt_at,
                        completed_at,
                        json.dumps(error),
                        json.dumps(checkpoint) if checkpoint is not None else None,
                        failed_at,
                        job_id,
                    ),
                )
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
                updated = self._record_from_row(columns, row)
                attempt_status = (
                    "failed" if next_status == TaskStatus.RETRY_WAIT.value else next_status
                )
                cursor.execute(
                    """
                    UPDATE rca_job_attempt
                    SET status = %s,
                        completed_at = %s,
                        error = %s::jsonb,
                        checkpoint = %s::jsonb
                    WHERE job_id = %s AND attempt_number = %s
                    """,
                    (
                        attempt_status,
                        failed_at,
                        json.dumps(error),
                        json.dumps(checkpoint) if checkpoint is not None else None,
                        job_id,
                        current.attempt_count,
                    ),
                )
                event_type = {
                    TaskStatus.RETRY_WAIT.value: "job_retry_scheduled",
                    TaskStatus.CANCELLED.value: "job_cancelled",
                    TaskStatus.FAILED.value: "job_failed",
                }[next_status]
                self._append_event(
                    cursor,
                    job_id=job_id,
                    event_type=event_type,
                    payload={
                        "status": next_status,
                        "attempt_number": current.attempt_count,
                        "retryable": retryable,
                        "next_attempt_at": (
                            next_attempt_at.isoformat()
                            if next_attempt_at is not None
                            else None
                        ),
                        "error": error,
                    },
                )
        return updated

    def request_cancel(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> RCAJobQueueRecord | None:
        import psycopg

        requested_at = now or datetime.now(UTC)
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=UTC)
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                current = self._locked_record(cursor, job_id)
                if current is None:
                    return None
                current_status = current.state.job.status
                if current_status == TaskStatus.CANCELLED.value:
                    return current
                if current_status in {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}:
                    raise JobNotCancellableError(
                        f"RCA Job {job_id} is already {current_status}"
                    )
                if current_status == TaskStatus.CANCEL_REQUESTED.value:
                    return current
                immediate = current_status in {
                    TaskStatus.QUEUED.value,
                    TaskStatus.RETRY_WAIT.value,
                }
                next_status = (
                    TaskStatus.CANCELLED.value
                    if immediate
                    else TaskStatus.CANCEL_REQUESTED.value
                )
                validate_job_transition(current_status, next_status)
                next_state = replace(
                    current.state,
                    job=replace(current.state.job, status=next_status),
                )
                cursor.execute(
                    """
                    UPDATE rca_job_state
                    SET status = %s,
                        state = %s::jsonb,
                        cancel_requested_at = %s,
                        completed_at = CASE WHEN %s THEN %s ELSE completed_at END,
                        next_attempt_at = CASE WHEN %s THEN NULL ELSE next_attempt_at END,
                        version = version + 1,
                        updated_at = %s
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        next_status,
                        json.dumps(next_state.to_dict()),
                        requested_at,
                        immediate,
                        requested_at,
                        immediate,
                        requested_at,
                        job_id,
                    ),
                )
                row = cursor.fetchone()
                columns = [item.name for item in cursor.description or ()]
                updated = self._record_from_row(columns, row)
                self._append_event(
                    cursor,
                    job_id=job_id,
                    event_type=(
                        "job_cancelled" if immediate else "job_cancel_requested"
                    ),
                    payload={"status": next_status},
                )
        return updated

    def recover_stale_leases(
        self,
        *,
        now: datetime | None = None,
        retry_after_seconds: float = 0,
    ) -> int:
        import psycopg

        recovered_at = now or datetime.now(UTC)
        if recovered_at.tzinfo is None:
            recovered_at = recovered_at.replace(tzinfo=UTC)
        recovered = 0
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id
                    FROM rca_job_state
                    WHERE status IN ('running', 'cancel_requested')
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= %s
                    ORDER BY lease_expires_at, job_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 100
                    """,
                    (recovered_at,),
                )
                job_ids = [str(row[0]) for row in cursor.fetchall()]
                for job_id in job_ids:
                    current = self._locked_record(cursor, job_id)
                    assert current is not None
                    if current.state.job.status == TaskStatus.CANCEL_REQUESTED.value:
                        next_status = TaskStatus.CANCELLED.value
                    elif current.attempt_count < current.max_attempts:
                        next_status = TaskStatus.RETRY_WAIT.value
                    else:
                        next_status = TaskStatus.FAILED.value
                    next_attempt_at = (
                        recovered_at + timedelta(seconds=max(0, retry_after_seconds))
                        if next_status == TaskStatus.RETRY_WAIT.value
                        else None
                    )
                    error = {
                        "error_code": "WORKER_LEASE_EXPIRED",
                        "message": "Worker lease expired before the attempt committed.",
                        "retryable": next_status == TaskStatus.RETRY_WAIT.value,
                    }
                    next_state = replace(
                        current.state,
                        job=replace(current.state.job, status=next_status),
                    )
                    cursor.execute(
                        """
                        UPDATE rca_job_state
                        SET status = %s,
                            state = %s::jsonb,
                            next_attempt_at = %s,
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            heartbeat_at = NULL,
                            completed_at = %s,
                            error = %s::jsonb,
                            version = version + 1,
                            updated_at = %s
                        WHERE job_id = %s
                        """,
                        (
                            next_status,
                            json.dumps(next_state.to_dict()),
                            next_attempt_at,
                            recovered_at
                            if next_status in TERMINAL_JOB_STATUSES
                            else None,
                            json.dumps(error),
                            recovered_at,
                            job_id,
                        ),
                    )
                    cursor.execute(
                        """
                        UPDATE rca_job_attempt
                        SET status = 'abandoned', completed_at = %s, error = %s::jsonb
                        WHERE job_id = %s AND attempt_number = %s
                        """,
                        (
                            recovered_at,
                            json.dumps(error),
                            job_id,
                            current.attempt_count,
                        ),
                    )
                    self._append_event(
                        cursor,
                        job_id=job_id,
                        event_type=(
                            "job_cancelled"
                            if next_status == TaskStatus.CANCELLED.value
                            else "job_lease_recovered"
                        ),
                        payload={
                            "status": next_status,
                            "attempt_number": current.attempt_count,
                        },
                    )
                    recovered += 1
        return recovered

    def record_worker_heartbeat(
        self,
        worker_id: str,
        *,
        active_lease_count: int,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        import psycopg

        if active_lease_count < 0:
            raise ValueError("active_lease_count must be non-negative")
        heartbeat_at = now or datetime.now(UTC)
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        _validate_no_sensitive_fields(metadata or {}, "worker metadata")
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO rca_worker_heartbeat (
                        worker_id, started_at, last_seen_at,
                        active_lease_count, metadata
                    ) VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (worker_id) DO UPDATE
                    SET last_seen_at = EXCLUDED.last_seen_at,
                        active_lease_count = EXCLUDED.active_lease_count,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        worker_id,
                        heartbeat_at,
                        heartbeat_at,
                        active_lease_count,
                        json.dumps(metadata or {}),
                    ),
                )

    def list_attempts(self, job_id: str) -> list[RCAJobAttemptRecord]:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id, attempt_number, worker_id, status, started_at,
                           completed_at, error, checkpoint
                    FROM rca_job_attempt
                    WHERE job_id = %s
                    ORDER BY attempt_number
                    """,
                    (job_id,),
                )
                rows = cursor.fetchall()
        return [
            RCAJobAttemptRecord(
                job_id=str(row[0]),
                attempt_number=int(row[1]),
                worker_id=str(row[2]),
                status=str(row[3]),
                started_at=row[4].isoformat(),
                completed_at=row[5].isoformat() if row[5] else None,
                error=dict(row[6]) if row[6] is not None else None,
                checkpoint=dict(row[7]) if row[7] is not None else None,
            )
            for row in rows
        ]

    def list_events(self, job_id: str) -> list[RCAJobEventRecord]:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT job_id, sequence, event_type, payload, created_at
                    FROM rca_job_event
                    WHERE job_id = %s
                    ORDER BY sequence
                    """,
                    (job_id,),
                )
                rows = cursor.fetchall()
        return [
            RCAJobEventRecord(
                job_id=str(row[0]),
                sequence=int(row[1]),
                event_type=str(row[2]),
                payload=dict(row[3]),
                created_at=row[4].isoformat(),
            )
            for row in rows
        ]

    def get(self, job_id: str) -> RCAState | None:
        record = self.get_record(job_id)
        return record.state if record is not None else None

    def check_ready(self) -> None:
        import psycopg

        required_tables = (
            "rca_job_state",
            "rca_job_attempt",
            "rca_job_event",
            "rca_worker_heartbeat",
            "audit_event",
            "memory_candidate",
            "schema_migrations",
        )
        with psycopg.connect(self.database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT " + ", ".join("to_regclass(%s)" for _ in required_tables),
                    tuple(f"public.{name}" for name in required_tables),
                )
                row = cursor.fetchone()
                if row is None or any(value is None for value in row):
                    missing = [
                        name
                        for name, value in zip(required_tables, row or (), strict=False)
                        if value is None
                    ]
                    raise RuntimeError(
                        "runtime database is missing required tables: "
                        + ", ".join(missing or required_tables)
                    )
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM schema_migrations "
                    "WHERE version = '011_async_job_queue')"
                )
                migration_row = cursor.fetchone()
                if migration_row is None or not bool(migration_row[0]):
                    raise RuntimeError(
                        "runtime database migration 011_async_job_queue is not applied"
                    )
