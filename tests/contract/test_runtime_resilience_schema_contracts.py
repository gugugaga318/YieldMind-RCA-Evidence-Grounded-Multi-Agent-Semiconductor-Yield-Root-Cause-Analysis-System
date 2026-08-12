from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UP_SQL = (ROOT / "db/migrations/005_runtime_resilience.up.sql").read_text(
    encoding="utf-8"
)
DOWN_SQL = (ROOT / "db/migrations/005_runtime_resilience.down.sql").read_text(
    encoding="utf-8"
)
SEED_SCRIPT = (ROOT / "scripts/seed_database.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")
JOB_STORE = (ROOT / "backend/yield_rca_api/store.py").read_text(encoding="utf-8")


class RuntimeResilienceSchemaContractTest(unittest.TestCase):
    def test_durable_job_state_is_versioned_and_indexed(self) -> None:
        self.assertIn("CREATE TABLE rca_job_state", UP_SQL)
        self.assertIn("state jsonb NOT NULL", UP_SQL)
        self.assertIn("idx_rca_job_state_status_updated", UP_SQL)
        self.assertIn("005_runtime_resilience", UP_SQL)

    def test_runtime_resilience_migration_is_reversible(self) -> None:
        self.assertIn("DROP TABLE IF EXISTS rca_job_state", DOWN_SQL)
        self.assertIn("005_runtime_resilience", DOWN_SQL)

    def test_seed_reset_and_runtime_probe_include_migration_005(self) -> None:
        self.assertIn("005_runtime_resilience.up.sql", SEED_SCRIPT)
        self.assertIn("005_runtime_resilience.down.sql", SEED_SCRIPT)
        self.assertIn("http://127.0.0.1:8000/ready", COMPOSE)

    def test_postgres_job_store_checks_schema_and_updates_locked_state(self) -> None:
        self.assertIn("class PostgresRCAJobStore", JOB_STORE)
        self.assertIn("FOR UPDATE", JOB_STORE)
        self.assertIn("UPDATE rca_job_state", JOB_STORE)
        self.assertIn("011_async_job_queue", JOB_STORE)


if __name__ == "__main__":
    unittest.main()
