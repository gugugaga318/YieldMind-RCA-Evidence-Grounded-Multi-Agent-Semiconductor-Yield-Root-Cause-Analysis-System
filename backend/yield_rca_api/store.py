"""Persistence adapters for RCA API job state and durable queue metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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

    def create(self, state: RCAState) -> None: ...

    def save(self, state: RCAState) -> None: ...

    def get(self, job_id: str) -> RCAState | None: ...

    def check_ready(self) -> None: ...


class InMemoryRCAJobStore:
    """Thread-safe queue adapter used by tests and local non-PostgreSQL runs."""

    def __init__(self) -> None:
        self._records: dict[str, RCAJobQueueRecord] = {}
        self._idempotency_index: dict[str, str] = {}
        self._lock = RLock()

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
            return record

    def get_record(self, job_id: str) -> RCAJobQueueRecord | None:
        with self._lock:
            return self._records.get(job_id)

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
