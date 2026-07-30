from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from yield_rca_api.app import create_app  # noqa: E402
from yield_rca_api.schemas import CreateRCAJobRequest  # noqa: E402


class FastAPIContractTest(unittest.TestCase):
    def test_required_rca_routes_are_registered(self) -> None:
        app = create_app()
        routes = {
            (method, route.path)
            for route in app.routes
            for method in getattr(route, "methods", set())
        }

        self.assertIn(("POST", "/rca/jobs"), routes)
        self.assertIn(("GET", "/rca/jobs/{job_id}"), routes)
        self.assertIn(("GET", "/rca/jobs/{job_id}/report"), routes)
        self.assertIn(("GET", "/ready"), routes)

    def test_create_request_normalizes_query_and_rejects_unknown_fields(self) -> None:
        request = CreateRCAJobRequest(user_query="  Analyze the July yield drop.  ")
        self.assertEqual(request.user_query, "Analyze the July yield drop.")

        with self.assertRaises(ValueError):
            CreateRCAJobRequest(user_query="Analyze.", generate_synthetic_data=True)

    def test_api_has_no_synthetic_generator_dependency(self) -> None:
        import yield_rca_api.app as api_app

        source = inspect.getsource(api_app).lower()
        forbidden = (
            "generate_synthetic_fab_data",
            "scripts.generate_synthetic",
            "subprocess",
            "seed_database",
        )
        for dependency in forbidden:
            self.assertNotIn(dependency, source)

    def test_job_response_openapi_describes_typed_evidence(self) -> None:
        app = create_app()
        schema = app.openapi()
        components = schema["components"]["schemas"]

        evidence_schema = components["EvidenceResponse"]["properties"]
        self.assertIn("evidence_type", evidence_schema)
        self.assertIn("source_agent", evidence_schema)
        self.assertIn("source_tool", evidence_schema)
        self.assertIn("observation", evidence_schema)
        self.assertIn("entities", evidence_schema)
        self.assertIn("confidence", evidence_schema)
        self.assertIn("evidence_schema_version", evidence_schema)


if __name__ == "__main__":
    unittest.main()
