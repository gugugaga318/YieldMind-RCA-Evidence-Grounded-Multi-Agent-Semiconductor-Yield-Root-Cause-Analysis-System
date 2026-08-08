from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL") and importlib.util.find_spec("psycopg"),
    "set TEST_DATABASE_URL and install psycopg to run real PostgreSQL migration tests",
)
class OptionalPostgresMigrationTest(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_on_real_postgres(self) -> None:
        import psycopg  # type: ignore[import-not-found]

        database_url = os.environ["TEST_DATABASE_URL"]
        up_sql = [
            (ROOT / "db" / "migrations" / name).read_text(encoding="utf-8")
            for name in (
                "001_initial_schema.up.sql",
                "002_observability_audit.up.sql",
                "003_memory_approval.up.sql",
                "004_advanced_spc_analytics.up.sql",
                "005_runtime_resilience.up.sql",
                "006_memory_snapshot_index_update.up.sql",
                "007_knowledge_ingestion.up.sql",
            )
        ]
        down_sql = [
            (ROOT / "db" / "migrations" / name).read_text(encoding="utf-8")
            for name in (
                "007_knowledge_ingestion.down.sql",
                "006_memory_snapshot_index_update.down.sql",
                "005_runtime_resilience.down.sql",
                "004_advanced_spc_analytics.down.sql",
                "003_memory_approval.down.sql",
                "002_observability_audit.down.sql",
                "001_initial_schema.down.sql",
            )
        ]

        with psycopg.connect(database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                for statement in down_sql:
                    cursor.execute(statement)
                for statement in up_sql:
                    cursor.execute(statement)
                cursor.execute("SELECT to_regclass('public.process_history')")
                self.assertEqual(cursor.fetchone()[0], "process_history")
                cursor.execute("SELECT to_regclass('public.llm_usage_event')")
                self.assertEqual(cursor.fetchone()[0], "llm_usage_event")
                cursor.execute("SELECT to_regclass('public.memory_candidate')")
                self.assertEqual(cursor.fetchone()[0], "memory_candidate")
                cursor.execute("SELECT to_regclass('public.spc_baseline_profile')")
                self.assertEqual(cursor.fetchone()[0], "spc_baseline_profile")
                cursor.execute("SELECT to_regclass('public.rca_job_state')")
                self.assertEqual(cursor.fetchone()[0], "rca_job_state")
                cursor.execute("SELECT to_regclass('public.knowledge_index_update')")
                self.assertEqual(cursor.fetchone()[0], "knowledge_index_update")
                cursor.execute("SELECT to_regclass('public.knowledge_ingestion_candidate')")
                self.assertEqual(cursor.fetchone()[0], "knowledge_ingestion_candidate")
                cursor.execute("SELECT to_regclass('public.knowledge_chunk')")
                self.assertEqual(cursor.fetchone()[0], "knowledge_chunk")
                cursor.execute("SELECT to_regclass('public.active_knowledge_chunk')")
                self.assertEqual(cursor.fetchone()[0], "active_knowledge_chunk")
                cursor.execute(
                    """
                    INSERT INTO audit_event (
                        event_id, action, job_id, correlation_id, actor, outcome
                    ) VALUES (
                        'AUDIT-MEMORY-ROLLBACK',
                        'MEMORY_APPROVAL_RECORDED',
                        'JOB-MEMORY-ROLLBACK',
                        'CORR-MEMORY-ROLLBACK',
                        'engineer-test',
                        'success'
                    )
                    """
                )
                for statement in down_sql:
                    cursor.execute(statement)
                cursor.execute("SELECT to_regclass('public.process_history')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.audit_event')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.memory_approval')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.spc_excursion')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.rca_job_state')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.knowledge_index_update')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.knowledge_ingestion_candidate')")
                self.assertIsNone(cursor.fetchone()[0])
                cursor.execute("SELECT to_regclass('public.knowledge_chunk')")
                self.assertIsNone(cursor.fetchone()[0])
                for statement in up_sql:
                    cursor.execute(statement)
                cursor.execute("SELECT to_regclass('public.equipment_capability')")
                self.assertEqual(cursor.fetchone()[0], "equipment_capability")
                cursor.execute("SELECT to_regclass('public.audit_event')")
                self.assertEqual(cursor.fetchone()[0], "audit_event")
                cursor.execute("SELECT to_regclass('public.memory_approval')")
                self.assertEqual(cursor.fetchone()[0], "memory_approval")
                cursor.execute("SELECT to_regclass('public.spc_excursion_lot')")
                self.assertEqual(cursor.fetchone()[0], "spc_excursion_lot")
                cursor.execute("SELECT to_regclass('public.rca_job_state')")
                self.assertEqual(cursor.fetchone()[0], "rca_job_state")
                cursor.execute("SELECT to_regclass('public.knowledge_index_update')")
                self.assertEqual(cursor.fetchone()[0], "knowledge_index_update")
                cursor.execute("SELECT to_regclass('public.active_knowledge_chunk')")
                self.assertEqual(cursor.fetchone()[0], "active_knowledge_chunk")
                for statement in down_sql:
                    cursor.execute(statement)
            connection.commit()


if __name__ == "__main__":
    unittest.main()
