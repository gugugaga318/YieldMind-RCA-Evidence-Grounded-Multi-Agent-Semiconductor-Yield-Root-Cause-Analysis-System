from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.llm_gateway import (  # noqa: E402
    FakeLLMClient,
    LLMRequest,
    capture_llm_usage,
    load_prompt,
)
from yield_rca_core.specialist_models import (  # noqa: E402
    MAX_SPECIALIST_TOOL_STEPS,
    SpecialistAnalysis,
    SpecialistStepRecord,
    SpecialistToolCandidate,
    SpecialistToolDecision,
    SpecialistValidationError,
)


class SpecialistV2ModelContractTest(unittest.TestCase):
    def test_tool_candidate_round_trip_preserves_python_bound_parameters(self) -> None:
        candidate = SpecialistToolCandidate(
            candidate_id="MES_CONTEXT",
            tool_name="get_lot_context",
            parameters={
                "lot_id": "LOT_01",
                "scope": {"module": "CU_CMP"},
            },
            purpose="Resolve the source Lot context without broadening its scope.",
        )

        restored = SpecialistToolCandidate.from_dict(candidate.to_dict())

        self.assertEqual(restored, candidate)
        self.assertEqual(restored.parameters["lot_id"], "LOT_01")
        json.dumps(restored.to_dict())

        payload = candidate.to_dict()
        payload["model_parameters"] = {"lot_id": "LOT_99"}
        with self.assertRaisesRegex(SpecialistValidationError, "unknown fields"):
            SpecialistToolCandidate.from_dict(payload)

    def test_tool_decision_is_exactly_call_tool_or_finish(self) -> None:
        call_tool = SpecialistToolDecision(
            decision_id="SD_01",
            action_id="ACTION_01",
            agent="mes",
            decision_type="call_tool",
            reason="Resolve the source Lot process context.",
            candidate_id="MES_CONTEXT",
            stop_reason=None,
        )
        finish = SpecialistToolDecision(
            decision_id="SD_02",
            action_id="ACTION_01",
            agent="mes",
            decision_type="finish",
            reason="The two bounded observations answer the local question.",
            candidate_id=None,
            stop_reason="sufficient_evidence",
        )

        self.assertEqual(
            SpecialistToolDecision.from_dict(call_tool.to_dict()),
            call_tool,
        )
        self.assertEqual(SpecialistToolDecision.from_dict(finish.to_dict()), finish)

        invalid_payloads = [
            {**call_tool.to_dict(), "stop_reason": "sufficient_evidence"},
            {**finish.to_dict(), "candidate_id": "MES_CONTEXT"},
            {**call_tool.to_dict(), "decision_type": "call_specialist"},
            {**call_tool.to_dict(), "agent": "rca_reasoning"},
            {**call_tool.to_dict(), "parameters": {"lot_id": "LOT_99"}},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(SpecialistValidationError):
                    SpecialistToolDecision.from_dict(payload)

    def test_step_record_is_strict_auditable_and_bounded_to_two_steps(self) -> None:
        record = SpecialistStepRecord(
            step_id="STEP_02",
            step_index=2,
            action_id="ACTION_01",
            agent="fdc",
            decision_id="SD_02",
            candidate_id="FDC_OOC",
            tool_name="find_ooc_events",
            parameters={"lot_id": "LOT_01", "equipment_id": "CMP_01"},
            reason="Check whether the shifted parameter crossed an operating limit.",
            evidence_ids=["EV_FDC_OOC"],
            output_summary="One OOC event overlaps the source Lot process window.",
        )

        self.assertEqual(SpecialistStepRecord.from_dict(record.to_dict()), record)
        self.assertEqual(MAX_SPECIALIST_TOOL_STEPS, 2)
        json.dumps(record.to_dict())

        with self.assertRaisesRegex(SpecialistValidationError, "step_index"):
            SpecialistStepRecord(
                **{
                    **record.__dict__,
                    "step_index": 3,
                }
            )
        with self.assertRaisesRegex(SpecialistValidationError, "cannot claim evidence"):
            SpecialistStepRecord(
                **{
                    **record.__dict__,
                    "status": "failed",
                }
            )
        payload = record.to_dict()
        del payload["output_summary"]
        with self.assertRaisesRegex(SpecialistValidationError, "missing fields"):
            SpecialistStepRecord.from_dict(payload)

    def test_analysis_is_a_strict_evidence_backed_finding_draft(self) -> None:
        analysis = SpecialistAnalysis(
            summary="Thickness shifted high after Cu CMP.",
            confidence=0.78,
            evidence_ids=["EV_SHIFT", "EV_OOC"],
            engineering_interpretation=(
                "The aligned shift and OOC event support a process excursion signal, "
                "but do not independently prove the final root cause."
            ),
        )

        self.assertEqual(SpecialistAnalysis.from_dict(analysis.to_dict()), analysis)
        json.dumps(analysis.to_dict())

        invalid_payloads = [
            {**analysis.to_dict(), "confidence": True},
            {**analysis.to_dict(), "confidence": 1.1},
            {**analysis.to_dict(), "evidence_ids": []},
            {**analysis.to_dict(), "evidence_ids": ["EV_SHIFT", "EV_SHIFT"]},
            {**analysis.to_dict(), "root_cause": "EPD failure"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(SpecialistValidationError):
                    SpecialistAnalysis.from_dict(payload)

    def test_fake_client_routes_both_v2_prompts_and_preserves_legacy_specialist(self) -> None:
        client = FakeLLMClient()
        decision = SpecialistToolDecision(
            decision_id="SD_FAKE",
            action_id="ACTION_FAKE",
            agent="defect_wat",
            decision_type="call_tool",
            reason="Inspect the bounded defect signature.",
            candidate_id="DEFECT_SUMMARY",
            stop_reason=None,
        )
        analysis = SpecialistAnalysis(
            summary="Scratch is concentrated after Cu CMP.",
            confidence=0.72,
            evidence_ids=["EV_DEFECT"],
            engineering_interpretation="The spatial pattern warrants Cu CMP correlation.",
        )

        with capture_llm_usage() as usage:
            decision_response = client.complete_json(
                LLMRequest(
                    agent="defect_wat",
                    prompt_name="specialist_tool_planner",
                    prompt_version="v1",
                    payload={
                        "deterministic_specialist_decision": decision.to_dict(),
                    },
                )
            )
            analysis_response = client.complete_json(
                LLMRequest(
                    agent="defect_wat",
                    prompt_name="specialist_analysis",
                    prompt_version="v2",
                    payload={
                        "deterministic_specialist_analysis": analysis.to_dict(),
                    },
                )
            )
            legacy_response = client.complete_json(
                LLMRequest(
                    agent="defect_wat",
                    prompt_name="specialist",
                    prompt_version="v1",
                    payload={
                        "deterministic_finding": {
                            "summary": "Legacy deterministic finding.",
                            "confidence": 0.6,
                            "evidence_ids": ["EV_LEGACY"],
                        }
                    },
                )
            )

        self.assertEqual(
            SpecialistToolDecision.from_dict(decision_response.data),
            decision,
        )
        self.assertEqual(
            SpecialistAnalysis.from_dict(analysis_response.data),
            analysis,
        )
        self.assertEqual(legacy_response.data["evidence_ids"], ["EV_LEGACY"])
        self.assertEqual(len(usage), 3)
        self.assertTrue(all(event.provider == "fake" for event in usage))

    def test_prompts_keep_tool_arguments_and_evidence_authority_in_python(self) -> None:
        planner_prompt = load_prompt("specialist_tool_planner", "v1").lower()
        analysis_prompt = load_prompt("specialist_analysis", "v2").lower()

        self.assertIn("select exactly one candidate_id", planner_prompt)
        self.assertIn("never emit a tool name or tool parameters", planner_prompt)
        self.assertIn("at most two local tool steps", planner_prompt)
        self.assertIn("every evidence_id must be copied", analysis_prompt)
        self.assertIn("do not claim a final root cause", analysis_prompt)


if __name__ == "__main__":
    unittest.main()
