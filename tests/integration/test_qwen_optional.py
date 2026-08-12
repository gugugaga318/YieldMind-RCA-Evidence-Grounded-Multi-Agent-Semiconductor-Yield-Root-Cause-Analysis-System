from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.investigation_models import (  # noqa: E402
    DecisionType,
    InvestigationIntent,
)
from yield_rca_core.llm_gateway import (  # noqa: E402
    LLMCallError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMSettings,
    build_llm_client,
)
from yield_rca_core.models import RCAState, TaskStatus  # noqa: E402
from yield_rca_core.workflow import build_csv_workflow  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
IMPACT_QUERY = "Identify the impact lots for LOT_A_001."
MAX_PAID_LLM_CALLS = 12


def _real_qwen_test_enabled() -> bool:
    return bool(os.getenv("DASHSCOPE_API_KEY", "").strip()) and (
        os.getenv("RUN_REAL_QWEN_TEST") == "1"
    )


class CappedLLMClient:
    """Prevent an optional paid smoke test from making unbounded model calls."""

    def __init__(self, delegate: LLMClient, *, max_calls: int) -> None:
        self.delegate = delegate
        self.max_calls = max_calls
        self.call_count = 0
        self.limit_exceeded = False
        self.provider = delegate.provider
        self.model = delegate.model

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if self.call_count >= self.max_calls:
            self.limit_exceeded = True
            raise LLMCallError(
                "optional Qwen smoke test exceeded its paid LLM-call limit"
            )
        self.call_count += 1
        return self.delegate.complete_json(request)


@unittest.skipUnless(
    _real_qwen_test_enabled(),
    "set DASHSCOPE_API_KEY and RUN_REAL_QWEN_TEST=1 for a paid Qwen smoke test",
)
class OptionalQwenIntegrationTest(unittest.TestCase):
    def test_real_qwen_runs_bounded_impact_scope_react_workflow(self) -> None:
        settings = LLMSettings(
            agent_mode="llm",
            api_key=os.environ["DASHSCOPE_API_KEY"].strip(),
            model=os.getenv("YIELD_RCA_LLM_MODEL", "qwen-plus").strip()
            or "qwen-plus",
            base_url=os.getenv(
                "YIELD_RCA_LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).strip(),
            timeout_seconds=float(
                os.getenv("YIELD_RCA_LLM_TIMEOUT_SECONDS", "60")
            ),
            max_retries=0,
        )
        delegate = build_llm_client(settings)
        assert delegate is not None
        client = CappedLLMClient(delegate, max_calls=MAX_PAID_LLM_CALLS)
        workflow = build_csv_workflow(
            SEED_DIR,
            llm_settings=settings,
            llm_client=client,
            orchestration_mode="llm_react",
        )

        state = workflow.run(
            IMPACT_QUERY,
            job_id="JOB_REAL_QWEN_IMPACT_SMOKE",
            lot_id="LOT_A_001",
        )

        self.assertEqual(state.job.status, TaskStatus.COMPLETED.value)
        self.assertIsNotNone(state.investigation_goal)
        assert state.investigation_goal is not None
        self.assertEqual(
            state.investigation_goal.intent,
            InvestigationIntent.IMPACT_SCOPE.value,
        )

        metadata = state.execution_metadata
        self.assertEqual(metadata["agent_mode"], "llm")
        self.assertEqual(metadata["orchestration_requested_mode"], "llm_react")
        self.assertEqual(metadata["orchestration_mode"], "llm_react")
        self.assertNotIn("orchestration_fallback_reason", metadata)
        self.assertNotIn("orchestration_fallback_stage", metadata)
        self.assertNotIn("orchestration_fallback_after_action_count", metadata)

        decisions = state.planner_decisions
        self.assertGreaterEqual(len(decisions), 2)
        self.assertTrue(
            any(decision.decision_type == DecisionType.ACT.value for decision in decisions)
        )
        self.assertEqual(decisions[-1].decision_type, DecisionType.STOP.value)
        self.assertLessEqual(
            len(state.action_history),
            state.investigation_goal.max_steps,
        )
        self.assertLessEqual(
            int(metadata["tool_call_count"]),
            state.investigation_goal.max_tool_calls,
        )

        findings_by_id = {
            finding.finding_id: finding
            for finding in state.findings
        }
        specialist_records = [
            record
            for record in state.action_history
            if record.action.agent in {"mes", "fdc", "defect_wat", "knowledge"}
        ]
        self.assertTrue(specialist_records)
        for record in specialist_records:
            self.assertEqual(len(record.produced_finding_ids), 1)
            finding = findings_by_id[record.produced_finding_ids[0]]
            trace = finding.details.get("specialist_v2")
            self.assertIsInstance(trace, dict)
            assert isinstance(trace, dict)
            self.assertEqual(trace.get("action_id"), record.action.action_id)
            self.assertEqual(trace.get("agent"), record.action.agent)
            self.assertEqual(trace.get("analysis_source"), "qwen")
            self.assertFalse(trace.get("fallback_reason"))

        self.assertGreater(len(state.llm_usage), 0)
        self.assertTrue(all(event.status == "success" for event in state.llm_usage))
        self.assertGreater(sum(event.total_tokens for event in state.llm_usage), 0)
        self.assertEqual(metadata["llm_call_count"], len(state.llm_usage))
        self.assertEqual(client.call_count, len(state.llm_usage))
        self.assertFalse(client.limit_exceeded)
        self.assertLessEqual(client.call_count, MAX_PAID_LLM_CALLS)

        evaluation = state.run_evaluation
        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertTrue(evaluation.goal_success)
        self.assertTrue(evaluation.stop_correct)
        self.assertTrue(
            all(item.decision_valid for item in evaluation.decision_evaluations)
        )
        self.assertTrue(
            all(not item.redundant for item in evaluation.decision_evaluations)
        )
        self.assertTrue(RCAState.from_dict(state.to_dict()) == state)


if __name__ == "__main__":
    unittest.main()
