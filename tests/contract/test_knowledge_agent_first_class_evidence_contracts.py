from __future__ import annotations

import inspect
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.models import AgentFinding, ToolInput, ToolOutput  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository, Row  # noqa: E402
from yield_rca_core.specialist_agents import KnowledgeAgent  # noqa: E402
from yield_rca_core.tool_layer import RetrieveSimilarCaseTool  # noqa: E402

GOLDEN_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


class UnconfirmedKnowledgeRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "rca_case": [
                {
                    "case_id": "RCA_DRAFT_001",
                    "title": "Draft finding",
                    "module": "Cu CMP",
                    "equipment_type": "CMP",
                    "symptom": "slurry flow",
                    "root_cause": "draft root cause",
                    "solution": "draft action",
                    "confidence": "0.99",
                    "created_at": "2026-07-01T00:00:00+00:00",
                    "validation_status": "DRAFT",
                }
            ],
            "knowledge_document": [],
        }
        return [dict(row) for row in tables[table_name]]


class WeakMatchKnowledgeRepository:
    def rows(self, table_name: str) -> list[Row]:
        tables: dict[str, list[Row]] = {
            "rca_case": [
                {
                    "case_id": "RCA_CONFIRMED_WEAK",
                    "title": "Unrelated confirmed event",
                    "module": "Lithography",
                    "equipment_type": "Scanner",
                    "symptom": "overlay excursion",
                    "root_cause": "reticle alignment",
                    "solution": "recalibrate alignment",
                    "confidence": "0.50",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "validation_status": "CONFIRMED",
                }
            ],
            "knowledge_document": [],
        }
        return [dict(row) for row in tables[table_name]]


@dataclass
class RecordingTool:
    delegate: Any
    outputs: list[ToolOutput] = field(default_factory=list)

    def run(self, tool_input: ToolInput) -> ToolOutput:
        output = cast(ToolOutput, self.delegate.run(tool_input))
        # Agent evidence transport must not depend on the generated legacy mirror.
        output.data.pop("evidence", None)
        self.outputs.append(output)
        return output


class KnowledgeAgentFirstClassEvidenceContractTest(unittest.TestCase):
    def run_agent(
        self,
        *,
        repository: Any,
        request_id: str,
        query: str,
        module: str = "",
        equipment_type: str = "",
    ) -> tuple[ToolOutput, AgentFinding]:
        tool = RecordingTool(RetrieveSimilarCaseTool(repository))
        agent = KnowledgeAgent(retrieve_similar_case_tool=cast(Any, tool))
        finding = agent.analyze(
            request_id=request_id,
            query=query,
            module=module,
            equipment_type=equipment_type,
        )
        return tool.outputs[0], finding

    def assert_original_tool_evidence_is_transported(
        self,
        output: ToolOutput,
        finding: AgentFinding,
    ) -> None:
        self.assertEqual(finding.evidence, output.evidence)
        self.assertTrue(
            all(
                actual is source
                for actual, source in zip(finding.evidence, output.evidence, strict=True)
            )
        )
        self.assertEqual(finding.evidence_ids, [item.evidence_id for item in output.evidence])
        self.assertEqual(
            finding.details["evidence"],
            [item.to_dict() for item in output.evidence],
        )

    def test_confirmed_case_path_consumes_first_class_tool_evidence(self) -> None:
        output, finding = self.run_agent(
            repository=CsvFabRepository(GOLDEN_SEED_DIR),
            request_id="REQ_KNOWLEDGE_FIRST_CLASS_MATCH",
            query="Cu CMP slurry flow scratch leakage",
            module="Cu CMP",
            equipment_type="CMP",
        )

        self.assert_original_tool_evidence_is_transported(output, finding)
        self.assertEqual(finding.evidence_ids, ["EV_KNOWLEDGE_MATCH"])
        self.assertEqual(finding.details["top_case"]["case_id"], "RCA_CMP_2025_032")
        self.assertGreaterEqual(finding.confidence, 0.9)
        self.assertEqual(finding.warnings, [])

    def test_no_confirmed_case_path_preserves_missing_data_evidence(self) -> None:
        output, finding = self.run_agent(
            repository=UnconfirmedKnowledgeRepository(),
            request_id="REQ_KNOWLEDGE_FIRST_CLASS_MISSING",
            query="CMP slurry flow",
            module="Cu CMP",
        )

        self.assert_original_tool_evidence_is_transported(output, finding)
        self.assertEqual(finding.evidence_ids, ["EV_KNOWLEDGE_NO_CONFIRMED_MATCH"])
        self.assertEqual(finding.details["top_case"], {})
        self.assertEqual(finding.confidence, 0.0)
        self.assertEqual(
            {warning.warning_id for warning in finding.warnings},
            {"WARN_KNOWLEDGE_NO_CONFIRMED_CASE"},
        )

    def test_weak_match_path_preserves_warning_and_confidence(self) -> None:
        output, finding = self.run_agent(
            repository=WeakMatchKnowledgeRepository(),
            request_id="REQ_KNOWLEDGE_FIRST_CLASS_WEAK",
            query="copper contamination",
        )

        self.assert_original_tool_evidence_is_transported(output, finding)
        self.assertEqual(finding.confidence, 0.4)
        self.assertEqual(
            {warning.warning_id for warning in finding.warnings},
            {"WARN_KNOWLEDGE_WEAK_MATCH"},
        )

    def test_knowledge_agent_does_not_build_or_deserialize_evidence(self) -> None:
        source = inspect.getsource(KnowledgeAgent)
        self.assertNotIn("EvidenceBuilder", source)
        self.assertNotIn('data.get("evidence"', source)
        self.assertNotIn('data["evidence"]', source)


if __name__ == "__main__":
    unittest.main()
