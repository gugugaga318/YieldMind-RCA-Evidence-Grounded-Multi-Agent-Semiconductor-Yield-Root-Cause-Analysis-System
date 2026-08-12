from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db/migrations/011_async_job_queue.up.sql").read_text(encoding="utf-8")
DOWN_SQL = (ROOT / "db/migrations/011_async_job_queue.down.sql").read_text(
    encoding="utf-8"
)
SEED_SCRIPT = (ROOT / "scripts/seed_database.py").read_text(encoding="utf-8")
STORE = (ROOT / "backend/yield_rca_api/store.py").read_text(encoding="utf-8")


class AsyncJobQueueSchemaContractTest(unittest.TestCase):
    def test_migration_contains_queue_tables_and_worker_ready_columns(self) -> None:
        for table in ("rca_job_attempt", "rca_job_event", "rca_worker_heartbeat"):
            self.assertIn(f"CREATE TABLE {table}", UP_SQL)
            self.assertIn(f"DROP TABLE IF EXISTS {table}", DOWN_SQL)

        for column in (
            "request jsonb",
            "request_hash text",
            "idempotency_key text",
            "runtime_config jsonb",
            "priority integer",
            "attempt_count integer",
            "max_attempts integer",
            "next_attempt_at timestamptz",
            "lease_owner text",
            "lease_expires_at timestamptz",
            "heartbeat_at timestamptz",
            "cancel_requested_at timestamptz",
            "error jsonb",
            "checkpoint jsonb",
            "version integer",
        ):
            self.assertIn(column, UP_SQL)
        self.assertIn("rca_job_state_priority_check", UP_SQL)
        self.assertIn("jsonb_build_object", UP_SQL)

    def test_migration_and_store_use_batch_23_readiness_boundary(self) -> None:
        self.assertIn("011_async_job_queue.up.sql", SEED_SCRIPT)
        self.assertIn("011_async_job_queue.down.sql", SEED_SCRIPT)
        self.assertIn("011_async_job_queue", UP_SQL)
        self.assertIn("011_async_job_queue", DOWN_SQL)
        self.assertIn("011_async_job_queue", STORE)
        self.assertIn("idx_rca_job_state_idempotency_key", UP_SQL)
        self.assertIn("UNIQUE (job_id, attempt_number)", UP_SQL)
        self.assertIn("UNIQUE (job_id, sequence)", UP_SQL)

    def test_queue_schema_does_not_name_secret_or_hidden_reasoning_fields(self) -> None:
        contract = UP_SQL.lower()
        self.assertNotIn("api_key", contract)
        self.assertNotIn("authorization", contract)
        self.assertNotIn("chain_of_thought", contract)
        self.assertNotIn("hidden_reasoning", contract)


if __name__ == "__main__":
    unittest.main()
