from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMRequest,
    LLMSettings,
    build_llm_client,
)
from yield_rca_core.models import AgentKind  # noqa: E402


@unittest.skipUnless(
    os.getenv("DASHSCOPE_API_KEY") and os.getenv("RUN_REAL_QWEN_TEST") == "1",
    "set DASHSCOPE_API_KEY and RUN_REAL_QWEN_TEST=1 for a paid Qwen smoke test",
)
class OptionalQwenIntegrationTest(unittest.TestCase):
    def test_qwen_returns_structured_planner_json(self) -> None:
        settings = LLMSettings(
            agent_mode="llm",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model=os.getenv("YIELD_RCA_LLM_MODEL", "qwen-plus"),
        )
        client = build_llm_client(settings)
        assert client is not None
        response = client.complete_json(
            LLMRequest(
                agent=AgentKind.PLANNER.value,
                prompt_name="planner",
                prompt_version="v1",
                payload={
                    "user_query": "Return the supplied valid plan unchanged.",
                    "requested_plan_id": "PLAN_QWEN_SMOKE",
                    "registered_agents": [
                        "mes",
                        "fdc",
                        "defect_wat",
                        "knowledge",
                        "rca_reasoning",
                    ],
                    "required_agents": [
                        "mes",
                        "fdc",
                        "defect_wat",
                        "knowledge",
                        "rca_reasoning",
                    ],
                    "fallback_plan": {
                        "plan_id": "PLAN_QWEN_SMOKE",
                        "objective": "Smoke test",
                        "tasks": [
                            {
                                "task_id": "task_mes",
                                "agent": "mes",
                                "objective": "Resolve scope",
                                "depends_on": [],
                                "status": "pending",
                                "inputs": {"user_query": "Smoke test"},
                                "schema_version": "1.0",
                            }
                        ],
                        "schema_version": "1.0",
                    },
                },
            )
        )
        self.assertIsInstance(response.data, dict)
        self.assertGreater(response.usage.total_tokens, 0)


if __name__ == "__main__":
    unittest.main()
