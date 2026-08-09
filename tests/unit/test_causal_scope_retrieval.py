from __future__ import annotations

import sys
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_retrieval import CausalLaneKnowledgeRetriever  # noqa: E402
from yield_rca_core.causal_scope import (  # noqa: E402
    CausalLane,
    ObservationScope,
    RepositoryCausalContextProvider,
)
from yield_rca_core.knowledge_models import (  # noqa: E402
    KnowledgeDocument,
    KnowledgeLookupHit,
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeQuestionKind,
)
from yield_rca_core.knowledge_retrieval import TypedKnowledgeRetrieverAdapter  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402
from yield_rca_core.specialist_agents import KnowledgeAgent  # noqa: E402
from yield_rca_core.tool_layer import RetrieveSimilarCaseTool  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


def document(asset_id: str, module: str, equipment_type: str) -> KnowledgeDocument:
    content = f"Approved {module} scratch mechanism reference."
    return KnowledgeDocument(
        document_id=f"DOC_{asset_id}",
        case_id=asset_id,
        document_type="RCA_CASE",
        title=f"{module} mechanism",
        content=content,
        module=module,
        equipment_type=equipment_type,
        content_sha256=sha256(content.encode()).hexdigest(),
        publication_policy="BUILTIN_SYNTHETIC_SEED",
    )


class RecordingCatalogRetriever:
    def __init__(self) -> None:
        self.documents = (
            document("RCA_CMP_2025_032", "Cu CMP", "CMP"),
            document("CASE_THIN_FILM", "Thin Film", "CVD"),
            document("CASE_WET", "Wet Clean", "WET"),
            document("CASE_GLOBAL", "Metrology", "INSPECTION"),
        )
        self.plans: list[KnowledgeLookupPlan] = []

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        self.plans.append(plan)
        matches = [
            item
            for item in self.documents
            if (not plan.module or item.module.casefold() == plan.module.casefold())
            and (
                not plan.equipment_type
                or item.equipment_type.casefold() == plan.equipment_type.casefold()
            )
        ][: plan.top_k]
        return tuple(
            KnowledgeLookupHit(
                rank=rank,
                document=item,
                score=round(0.95 - rank * 0.05, 3),
                matched_chunk_ids=(f"CHUNK_{item.evaluation_asset_id}",),
                excerpt=item.content,
                evidence_id=f"KEV_{lookup_id}_{rank}",
                relevance_reason="Recording Retriever matched the governed catalog.",
                retrieval_strategy="recording",
                score_components={"keyword": round(0.95 - rank * 0.05, 3)},
                source_confidence=item.source_confidence,
            )
            for rank, item in enumerate(matches, start=1)
        )


def plan(*, explicit_module_limit: bool = False) -> KnowledgeLookupPlan:
    return KnowledgeLookupPlan(
        intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
        question_kind=KnowledgeQuestionKind.HISTORICAL_MATCH.value,
        query="Investigate the scratch observed after Cu CMP.",
        allowed_document_types=("RCA_CASE",),
        reason="Test causal candidate generation.",
        module="Cu CMP",
        equipment_type="CMP",
        observation_scope=ObservationScope(
            source_lot_id="LOT_A_001",
            product_id="40N_SOC",
            detected_module="Cu CMP",
            detected_operation="6400",
            detected_equipment_id="CMP_CU03",
            detected_equipment_type="CMP",
            symptom_types=("scratch",),
        ),
        explicit_module_limit=explicit_module_limit,
        top_k=5,
    )


class CausalLaneRetrieverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.delegate = RecordingCatalogRetriever()
        self.retriever = CausalLaneKnowledgeRetriever(self.delegate)
        self.provider = RepositoryCausalContextProvider(CsvFabRepository(SEED_DIR))

    def test_cu_cmp_observation_preserves_every_available_lane(self) -> None:
        prepared = self.retriever.prepare_plan(plan(), context_provider=self.provider)
        hits = self.retriever.retrieve(prepared, lookup_id="KLOOK_CAUSAL")

        represented = {lane for hit in hits for lane in hit.candidate_lanes}
        self.assertEqual(represented, {item.value for item in CausalLane})
        self.assertTrue(any(hit.document.module != "Cu CMP" for hit in hits))
        self.assertTrue(any(not item.module for item in self.delegate.plans))
        self.assertEqual(len({hit.document.evaluation_asset_id for hit in hits}), len(hits))
        self.assertLessEqual(len(hits), prepared.top_k)

    def test_explicit_module_limit_applies_to_every_lane(self) -> None:
        prepared = self.retriever.prepare_plan(
            plan(explicit_module_limit=True),
            context_provider=self.provider,
        )
        hits = self.retriever.retrieve(prepared, lookup_id="KLOOK_HARD")

        assert prepared.causal_search_scope is not None
        self.assertEqual(prepared.causal_search_scope.hard_constraints.module, "Cu CMP")
        self.assertTrue(self.delegate.plans)
        self.assertTrue(all(item.module == "Cu CMP" for item in self.delegate.plans))
        self.assertEqual({item.document.module for item in hits}, {"Cu CMP"})

    def test_missing_route_data_is_typed_and_global_lane_still_runs(self) -> None:
        prepared = self.retriever.prepare_plan(plan())
        hits = self.retriever.retrieve(prepared, lookup_id="KLOOK_NO_CONTEXT")

        assert prepared.causal_search_scope is not None
        upstream = prepared.causal_search_scope.lane(CausalLane.UPSTREAM_ROUTE.value)
        shared = prepared.causal_search_scope.lane(CausalLane.SHARED_RESOURCE.value)
        assert upstream is not None and shared is not None
        self.assertFalse(upstream.available)
        self.assertFalse(shared.available)
        self.assertIn("unavailable", upstream.reason)
        represented = {lane for hit in hits for lane in hit.candidate_lanes}
        self.assertEqual(
            represented,
            {CausalLane.SAME_STEP.value, CausalLane.GLOBAL_SEMANTIC.value},
        )

    def test_rca_knowledge_agent_preserves_scope_in_finding_and_evidence(self) -> None:
        repository = CsvFabRepository(SEED_DIR)
        adapter = TypedKnowledgeRetrieverAdapter(repository, self.retriever)
        finding = KnowledgeAgent(
            RetrieveSimilarCaseTool(repository, retriever=adapter)
        ).analyze(
            request_id="REQ_CAUSAL_AGENT",
            query="scratch observed after Cu CMP",
            module="Cu CMP",
            equipment_type="CMP",
            source_lot_id="LOT_A_001",
            product_id="40N_SOC",
            detected_operation="6400",
            detected_equipment_id="CMP_CU03",
            symptom_types=("scratch",),
        )

        scope = finding.details["causal_search_scope"]
        self.assertIsInstance(scope, dict)
        assert isinstance(scope, dict)
        self.assertEqual(
            set(scope["available_lanes"]),
            {item.value for item in CausalLane},
        )
        self.assertTrue(finding.details["candidate_lanes"])
        self.assertTrue(finding.evidence)
        evidence_scope = finding.evidence[0].metadata["causal_search_scope"]
        self.assertEqual(evidence_scope["mode"], scope["mode"])
        self.assertEqual(
            set(evidence_scope["available_lanes"]),
            set(scope["available_lanes"]),
        )


if __name__ == "__main__":
    unittest.main()
