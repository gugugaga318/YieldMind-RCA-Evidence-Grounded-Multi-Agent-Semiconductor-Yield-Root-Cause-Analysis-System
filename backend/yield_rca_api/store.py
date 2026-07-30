"""Persistence adapters for RCA API job state."""

from __future__ import annotations

import json
from threading import RLock
from typing import Protocol

from yield_rca_core.models import RCAState


class DuplicateJobError(ValueError):
    """Raised when a job identifier is reserved more than once."""


class RCAJobStore(Protocol):
    def create(self, state: RCAState) -> None: ...

    def save(self, state: RCAState) -> None: ...

    def get(self, job_id: str) -> RCAState | None: ...

    def check_ready(self) -> None: ...


class InMemoryRCAJobStore:
    """Thread-safe process-local store for serialized workflow results.

    This is deliberately an API adapter concern. Fab evidence remains in the
    configured read-only Repository and Synthetic Fab generation remains an
    offline operation.
    """

    def __init__(self) -> None:
        self._states: dict[str, RCAState] = {}
        self._lock = RLock()

    def create(self, state: RCAState) -> None:
        job_id = state.job.job_id
        with self._lock:
            if job_id in self._states:
                raise DuplicateJobError(f"job already exists: {job_id}")
            self._states[job_id] = state

    def save(self, state: RCAState) -> None:
        with self._lock:
            self._states[state.job.job_id] = state

    def get(self, job_id: str) -> RCAState | None:
        with self._lock:
            return self._states.get(job_id)

    def check_ready(self) -> None:
        return None


class PostgresRCAJobStore:
    """Durable RCA State storage shared by API workers."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def create(self, state: RCAState) -> None:
        import psycopg

        try:
            with psycopg.connect(self.database_url, connect_timeout=10) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO rca_job_state (
                            job_id, status, state, created_at, updated_at
                        ) VALUES (%s, %s, %s::jsonb, %s, now())
                        """,
                        (
                            state.job.job_id,
                            state.job.status,
                            json.dumps(state.to_dict()),
                            state.job.created_at,
                        ),
                    )
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateJobError(f"job already exists: {state.job.job_id}") from exc

    def save(self, state: RCAState) -> None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO rca_job_state (
                        job_id, status, state, created_at, updated_at
                    ) VALUES (%s, %s, %s::jsonb, %s, now())
                    ON CONFLICT (job_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        state = EXCLUDED.state,
                        updated_at = now()
                    """,
                    (
                        state.job.job_id,
                        state.job.status,
                        json.dumps(state.to_dict()),
                        state.job.created_at,
                    ),
                )

    def get(self, job_id: str) -> RCAState | None:
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state FROM rca_job_state WHERE job_id = %s",
                    (job_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return RCAState.from_dict(dict(payload))

    def check_ready(self) -> None:
        import psycopg

        required_tables = (
            "rca_job_state",
            "audit_event",
            "memory_candidate",
            "schema_migrations",
        )
        with psycopg.connect(self.database_url, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT to_regclass(%s), to_regclass(%s),
                           to_regclass(%s), to_regclass(%s)
                    """,
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
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM schema_migrations
                        WHERE version = '005_runtime_resilience'
                    )
                    """
                )
                migration_row = cursor.fetchone()
                if migration_row is None or not bool(migration_row[0]):
                    raise RuntimeError(
                        "runtime database migration 005_runtime_resilience is not applied"
                    )
