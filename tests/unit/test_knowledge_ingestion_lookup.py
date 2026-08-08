from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from pypdf import PdfWriter  # noqa: E402
from yield_rca_core.knowledge_ingestion import (  # noqa: E402
    KnowledgeDocumentParser,
    KnowledgeIngestionConflictError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)
from yield_rca_core.knowledge_lookup import KnowledgeLookupService  # noqa: E402
from yield_rca_core.knowledge_models import KnowledgeIngestionCandidate  # noqa: E402
from yield_rca_core.knowledge_store import load_builtin_knowledge_store  # noqa: E402

CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"


class KnowledgeIngestionAndLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = load_builtin_knowledge_store(
            CORPUS,
            additional_case_ids={"RCA_EXISTING"},
        )
        self.ingestion = KnowledgeIngestionService(self.store)
        self.lookup = KnowledgeLookupService(self.store)

    def ingest_sop(
        self, unique_term: str = "quasarneedle"
    ) -> KnowledgeIngestionCandidate:
        return self.ingestion.ingest(
            filename="scratch-response.md",
            content_type="text/markdown",
            payload=(
                "# Purpose\nCu CMP scratch response.\n"
                f"# Step 1\nInspect retaining ring and record {unique_term}."
            ).encode(),
            document_type="SOP",
            title="Governed scratch response",
            module="Cu CMP",
            operation="Cu CMP polish",
            tags=("scratch", "retaining ring"),
        )

    def test_builtin_active_index_excludes_draft_sentinels(self) -> None:
        documents = self.store.active_documents()
        self.assertEqual(len(documents), 60)
        self.assertTrue(all(item.validation_status == "CONFIRMED" for item in documents))
        self.assertFalse(any("DRAFT" in item.document_id for item in documents))
        self.assertTrue(self.store.active_chunks())

    def test_pending_and_first_approval_are_invisible_until_second_engineer(self) -> None:
        candidate = self.ingest_sop()
        self.assertEqual(candidate.equipment_type, "CMP")
        self.assertEqual(candidate.defect_type, "scratch")
        self.assertIn("scratch", candidate.tags)

        pending = self.lookup.lookup(
            query="quasarneedle",
            question_kind="procedure_guidance",
            module="Cu CMP",
        )
        self.assertEqual(pending.status, "no_match")

        first = self.ingestion.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE001",
            engineer_role="yield_engineer",
            decision="approve",
        )
        self.assertEqual(first.status, "pending_approval")
        self.assertEqual(first.approval_count, 1)
        self.assertEqual(
            self.lookup.lookup(
                query="quasarneedle",
                question_kind="procedure_guidance",
            ).status,
            "no_match",
        )

        published = self.ingestion.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="PE001",
            engineer_role="process_engineer",
            decision="approve",
        )
        self.assertEqual(published.status, "published")
        result = self.lookup.lookup(
            query="quasarneedle",
            question_kind="procedure_guidance",
            module="Cu CMP",
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.hits[0].document.title, "Governed scratch response")

    def test_same_engineer_cannot_decide_twice(self) -> None:
        candidate = self.ingest_sop("duplicateengineerterm")
        self.ingestion.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="YE001",
            engineer_role="yield_engineer",
            decision="approve",
        )
        with self.assertRaisesRegex(
            KnowledgeIngestionConflictError, "same engineer"
        ):
            self.ingestion.decide(
                candidate_id=candidate.candidate_id,
                engineer_id="ye001",
                engineer_role="yield_engineer",
                decision="approve",
            )

    def test_rejection_is_terminal_and_never_activates_chunks(self) -> None:
        candidate = self.ingest_sop("rejectedneedle")
        rejected = self.ingestion.decide(
            candidate_id=candidate.candidate_id,
            engineer_id="QE001",
            engineer_role="quality_engineer",
            decision="reject",
        )
        self.assertEqual(rejected.status, "rejected")
        with self.assertRaises(KnowledgeIngestionConflictError):
            self.ingestion.decide(
                candidate_id=candidate.candidate_id,
                engineer_id="YE001",
                engineer_role="yield_engineer",
                decision="approve",
            )
        self.assertEqual(
            self.lookup.lookup(
                query="rejectedneedle",
                question_kind="procedure_guidance",
            ).status,
            "no_match",
        )

    def test_duplicate_pending_or_published_content_is_rejected(self) -> None:
        self.ingest_sop("duplicateneedle")
        with self.assertRaisesRegex(
            KnowledgeIngestionConflictError, "same content"
        ):
            self.ingest_sop("duplicateneedle")

    def test_question_kind_hard_maps_action_and_document_type(self) -> None:
        result = self.lookup.lookup(
            query="radial scratch retaining ring",
            question_kind="historical_match",
            module="Cu CMP",
        )
        payload = result.to_dict()
        self.assertEqual(result.plan.action, "retrieve_historical_case")
        self.assertEqual(result.plan.allowed_document_types, ("RCA_CASE",))
        self.assertEqual({item.document.document_type for item in result.hits}, {"RCA_CASE"})
        self.assertEqual([item.agent for item in result.agent_trace], ["knowledge"])
        self.assertIsNone(payload["root_cause_conclusion"])

    def test_question_document_type_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            KnowledgeIngestionError, "can only retrieve SOP"
        ):
            self.lookup.lookup(
                query="scratch response",
                question_kind="procedure_guidance",
                document_type="ENGINEERING_NOTE",
            )

    def test_user_rca_case_requires_an_existing_case(self) -> None:
        def ingest_case(case_id: str | None = None) -> KnowledgeIngestionCandidate:
            return self.ingestion.ingest(
                filename="case.txt",
                content_type="text/plain",
                payload=b"Symptom: scratch. Root cause: ring wear.",
                document_type="RCA_CASE",
                title="Case attachment",
                module="Cu CMP",
                case_id=case_id,
            )

        with self.assertRaisesRegex(KnowledgeIngestionError, "requires"):
            ingest_case()
        with self.assertRaisesRegex(KnowledgeIngestionError, "does not exist"):
            ingest_case("RCA_UNKNOWN")
        accepted = ingest_case("RCA_EXISTING")
        self.assertEqual(accepted.case_id, "RCA_EXISTING")

    def test_scanned_pdf_and_invalid_utf8_are_rejected_with_stable_codes(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buffer = BytesIO()
        writer.write(buffer)
        parser = KnowledgeDocumentParser()
        with self.assertRaises(KnowledgeIngestionError) as pdf_error:
            parser.parse(
                filename="scan.pdf",
                content_type="application/pdf",
                payload=buffer.getvalue(),
            )
        self.assertEqual(pdf_error.exception.code, "OCR_NOT_SUPPORTED")
        with self.assertRaises(KnowledgeIngestionError) as text_error:
            parser.parse(
                filename="note.txt",
                content_type="text/plain",
                payload=b"\xff\xfe",
            )
        self.assertEqual(text_error.exception.code, "INVALID_TEXT_ENCODING")


if __name__ == "__main__":
    unittest.main()
