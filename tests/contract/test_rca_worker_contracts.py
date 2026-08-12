from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE = (ROOT / "backend/yield_rca_api/store.py").read_text(encoding="utf-8")
WORKER = (ROOT / "backend/yield_rca_api/worker.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "compose.yaml").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "docker/backend.Dockerfile").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


class RCAWorkerContractTest(unittest.TestCase):
    def test_postgres_claim_is_skip_locked_and_bounded(self) -> None:
        self.assertIn("FOR UPDATE SKIP LOCKED", STORE)
        self.assertIn("LIMIT 1", STORE)
        self.assertIn("lease_expires_at", STORE)
        self.assertIn("WORKER_LEASE_EXPIRED", STORE)

    def test_retry_policy_is_bounded_and_transient_only(self) -> None:
        self.assertIn("record.attempt_count < record.max_attempts", STORE)
        self.assertIn("status_code == 429", WORKER)
        self.assertIn("status_code >= 500", WORKER)
        self.assertIn("LLM_OUTPUT_INVALID", WORKER)
        self.assertIn("retryable\": False", WORKER)

    def test_worker_is_a_separate_compose_process(self) -> None:
        self.assertIn("  worker:", COMPOSE)
        self.assertIn("FROM base AS worker", DOCKERFILE)
        self.assertIn("FROM retrieval-base AS retrieval-worker", DOCKERFILE)
        self.assertIn("scripts/run_rca_worker.py", DOCKERFILE)
        self.assertIn("YIELD_RCA_WORKER_LEASE_SECONDS", ENV_EXAMPLE)
        self.assertIn("YIELD_RCA_WORKER_HEARTBEAT_SECONDS", ENV_EXAMPLE)


if __name__ == "__main__":
    unittest.main()
