from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.store import InMemoryRCAJobStore  # noqa: E402
from yield_rca_core.models import AgentKind, RCAJob, RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
QUERY = "Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
EXPECTED_ROOT_CAUSE = "CMP_CU03_CH02 slurry delivery degradation"


class NotReadyJobStore(InMemoryRCAJobStore):
    def check_ready(self) -> None:
        raise RuntimeError("migration 005 is missing")


def seed_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(SEED_DIR.iterdir())
        if path.is_file()
    }


class FastAPIBackendIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seed_hashes_before = seed_hashes()
        app = create_app(workflow=build_csv_workflow(SEED_DIR))
        cls.client = TestClient(app)
        cls.create_response = cls.client.post("/rca/jobs", json={"user_query": QUERY})
        cls.create_payload = cls.create_response.json()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_post_creates_and_completes_rca_job(self) -> None:
        self.assertEqual(self.create_response.status_code, 201)
        self.assertEqual(self.create_payload["status"], TaskStatus.COMPLETED.value)
        self.assertTrue(self.create_payload["job_id"].startswith("RCA_"))
        self.assertEqual(
            self.create_payload["state_url"],
            f"/rca/jobs/{self.create_payload['job_id']}",
        )
        self.assertTrue(self.create_payload["memory_candidate_id"].startswith("MEM_"))

    def test_get_returns_complete_traceable_rca_state(self) -> None:
        job_id = self.create_payload["job_id"]
        response = self.client.get(f"/rca/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job_id"], job_id)
        self.assertEqual(payload["status"], TaskStatus.COMPLETED.value)
        state = RCAState.from_dict(payload["state"])
        self.assertEqual(state.hypotheses[-1].root_cause, EXPECTED_ROOT_CAUSE)
        self.assertEqual(len(state.findings), 7)
        self.assertEqual(state.findings[-1].agent, AgentKind.IMPROVEMENT.value)
        known_evidence_ids = {item.evidence_id for item in state.evidence}
        self.assertTrue(set(state.hypotheses[-1].evidence_ids) <= known_evidence_ids)

    def test_get_report_returns_markdown_and_citations(self) -> None:
        job_id = self.create_payload["job_id"]
        response = self.client.get(f"/rca/jobs/{job_id}/report")

        self.assertEqual(response.status_code, 200)
        report = response.json()["report"]
        self.assertIn(EXPECTED_ROOT_CAUSE, report["markdown"])
        self.assertTrue(report["cited_evidence_ids"])

    def test_unknown_job_returns_not_found(self) -> None:
        self.assertEqual(self.client.get("/rca/jobs/UNKNOWN").status_code, 404)
        self.assertEqual(self.client.get("/rca/jobs/UNKNOWN/report").status_code, 404)
        self.assertEqual(
            self.client.get("/rca/jobs/UNKNOWN/memory-candidate").status_code,
            404,
        )

    def test_memory_candidate_requires_two_engineers_and_process_role(self) -> None:
        app = create_app(workflow=build_csv_workflow(SEED_DIR))
        with TestClient(app) as client:
            created = client.post("/rca/jobs", json={"user_query": QUERY}).json()
            candidate_id = created["memory_candidate_id"]
            candidate = client.get(f"/memory/candidates/{candidate_id}").json()[
                "candidate"
            ]
            self.assertEqual(candidate["status"], "pending_approval")

            first = client.post(
                f"/memory/candidates/{candidate_id}/approvals",
                json={
                    "engineer_id": "YE001",
                    "engineer_role": "yield_engineer",
                    "decision": "approve",
                    "comment": "Evidence reviewed.",
                },
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["candidate"]["approval_count"], 1)

            invalid_second = client.post(
                f"/memory/candidates/{candidate_id}/approvals",
                json={
                    "engineer_id": "EE001",
                    "engineer_role": "equipment_engineer",
                    "decision": "approve",
                },
            )
            self.assertEqual(invalid_second.status_code, 422)

            published = client.post(
                f"/memory/candidates/{candidate_id}/approvals",
                json={
                    "engineer_id": "PE001",
                    "engineer_role": "process_engineer",
                    "decision": "approve",
                    "comment": "Recipe DOE gate accepted; no direct production change.",
                },
            )
            self.assertEqual(published.status_code, 200)
            candidate = published.json()["candidate"]
            self.assertEqual(candidate["status"], "published")
            self.assertEqual(candidate["approval_count"], 2)
            self.assertTrue(candidate["published_case_id"].startswith("RCA_MEMORY_"))

            duplicate = client.post(
                f"/memory/candidates/{candidate_id}/approvals",
                json={
                    "engineer_id": "PE001",
                    "engineer_role": "process_engineer",
                    "decision": "approve",
                },
            )
            self.assertEqual(duplicate.status_code, 409)

    def test_report_returns_conflict_until_available(self) -> None:
        store = InMemoryRCAJobStore()
        pending_state = RCAState(
            job=RCAJob(job_id="RCA_PENDING", user_query=QUERY, status=TaskStatus.RUNNING.value)
        )
        store.create(pending_state)
        app = create_app(workflow=build_csv_workflow(SEED_DIR), store=store)

        with TestClient(app) as client:
            response = client.get("/rca/jobs/RCA_PENDING/report")

        self.assertEqual(response.status_code, 409)

    def test_api_does_not_generate_or_modify_synthetic_seed(self) -> None:
        self.assertEqual(seed_hashes(), self.seed_hashes_before)

    def test_ready_identifies_runtime_dataset(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(SEED_DIR),
            runtime_dataset="golden_case",
        )
        with TestClient(app) as client:
            payload = client.get("/ready").json()

        self.assertEqual(payload["dataset"], "golden_case")
        self.assertEqual(payload["agent_mode"], "deterministic")
        self.assertEqual(payload["orchestration_mode"], "fixed")

    def test_controlled_react_cutover_and_fallback_are_exposed_to_the_frontend(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(
                SEED_DIR,
                orchestration_mode="controlled_react",
            ),
            runtime_dataset="golden_case",
        )
        with TestClient(app) as client:
            ready = client.get("/ready").json()
            self.assertEqual(ready["orchestration_mode"], "controlled_react")

            controlled = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "lot",
                    "lot_id": "LOT_A_001",
                    "user_query": (
                        "Investigate LOT_A_001 scratch in Cu CMP and identify "
                        "root cause and impact lots."
                    ),
                },
            )
            self.assertEqual(controlled.status_code, 201)
            controlled_created = controlled.json()
            self.assertIsNone(controlled_created["memory_candidate_id"])
            controlled_state = client.get(controlled_created["state_url"]).json()["state"]
            self.assertEqual(
                controlled_state["execution_metadata"]["orchestration_requested_mode"],
                "controlled_react",
            )
            self.assertEqual(
                controlled_state["execution_metadata"]["orchestration_mode"],
                "controlled_react",
            )
            self.assertNotIn(
                "orchestration_fallback_reason",
                controlled_state["execution_metadata"],
            )
            self.assertEqual(
                [record["action"]["kind"] for record in controlled_state["action_history"]],
                [
                    "inspect_defect_pattern",
                    "find_shared_exposure",
                    "validate_shared_defect_pattern",
                    "inspect_fdc_spc",
                    "run_rca_reasoning",
                ],
            )
            self.assertEqual(controlled_state["goal_status"], "satisfied")
            self.assertEqual(controlled_state["stop_reason"], "goal_satisfied")

            no_defect_clue = client.post(
                "/rca/jobs",
                json={
                    "investigation_mode": "lot",
                    "lot_id": "LOT_A_001",
                    "user_query": "Identify the impact lots for LOT_A_001.",
                },
            ).json()
            fallback_state = client.get(no_defect_clue["state_url"]).json()["state"]
            self.assertEqual(
                fallback_state["execution_metadata"]["orchestration_mode"],
                "fixed",
            )
            self.assertEqual(
                fallback_state["execution_metadata"]["orchestration_fallback_reason"],
                "controlled_react_requires_explicit_defect_clue",
            )

            product_window = client.post(
                "/rca/jobs",
                json={"investigation_mode": "product_window", "user_query": QUERY},
            ).json()
            product_state = client.get(product_window["state_url"]).json()["state"]
            self.assertEqual(
                product_state["execution_metadata"]["orchestration_mode"],
                "fixed",
            )
            self.assertEqual(
                product_state["execution_metadata"]["orchestration_fallback_reason"],
                "controlled_react_requires_lot_investigation",
            )

    def test_ready_returns_503_when_runtime_store_is_not_ready(self) -> None:
        app = create_app(
            workflow=build_csv_workflow(SEED_DIR),
            store=NotReadyJobStore(),
        )
        with TestClient(app) as client:
            response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "not_ready")
        self.assertIn("migration 005", response.json()["detail"]["reason"])


if __name__ == "__main__":
    unittest.main()
