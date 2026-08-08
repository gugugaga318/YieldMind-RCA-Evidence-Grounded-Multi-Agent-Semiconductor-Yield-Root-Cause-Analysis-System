"""Independent Knowledge Agent lookup over the approval-gated Active Index."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from yield_rca_core.hybrid_retrieval import KnowledgeLookupRetriever
from yield_rca_core.knowledge_ingestion import KnowledgeIngestionError, KnowledgeStore
from yield_rca_core.knowledge_models import (
    KnowledgeAgentTrace,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeLookupHit,
    KnowledgeLookupIntent,
    KnowledgeLookupPlan,
    KnowledgeLookupResult,
    KnowledgeQuestionKind,
)

ANSWER_BOUNDARY = (
    "Retrieved engineering references only. This is not an RCA conclusion; "
    "current-Lot root cause still requires event Evidence and Hypothesis gating."
)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_+-]+|[\u3400-\u9fff]")
_STOP_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "approved",
        "case",
        "cases",
        "cmp",
        "cu",
        "describe",
        "engineering",
        "find",
        "for",
        "how",
        "in",
        "of",
        "or",
        "rca",
        "show",
        "sop",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
        "什",
        "么",
        "哪",
        "的",
    }
)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(item.casefold() for item in _TOKEN_PATTERN.findall(value))


def _matches_filter(actual: str, expected: str) -> bool:
    return not expected or actual.strip().casefold() == expected.strip().casefold()


@dataclass(frozen=True)
class _ChunkScore:
    chunk: KnowledgeChunk
    score: float
    matched_tokens: tuple[str, ...]


class DocumentChunkKeywordRetriever:
    """V1 chunk retriever; Long Task 3 replaces ranking with BM25/Vector/RRF."""

    def __init__(self, store: KnowledgeStore, *, abstain_threshold: float = 0.12) -> None:
        self.store = store
        self.abstain_threshold = abstain_threshold

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        documents = {
            item.document_id: item
            for item in self.store.active_documents()
            if item.validation_status == "CONFIRMED"
            and item.document_type in plan.allowed_document_types
            and self._document_in_scope(item, plan)
        }
        chunks_by_document: dict[str, list[_ChunkScore]] = defaultdict(list)
        scope_tokens = set(
            _tokens(
                " ".join(
                    [
                        plan.module,
                        plan.equipment_type,
                        plan.operation,
                        plan.defect_type,
                    ]
                )
            )
        )
        query_tokens = set(_tokens(plan.query)) - _STOP_TOKENS - scope_tokens
        for chunk in self.store.active_chunks():
            document_id = chunk.document_id
            if document_id is None or document_id not in documents:
                continue
            if chunk.validation_status != "CONFIRMED":
                continue
            document = documents[document_id]
            searchable = " ".join(
                [
                    document.title,
                    document.module,
                    document.equipment_type,
                    document.operation,
                    document.defect_type,
                    " ".join(document.tags),
                    chunk.heading,
                    chunk.content,
                ]
            )
            searchable_tokens = set(_tokens(searchable))
            matched = tuple(sorted(query_tokens & searchable_tokens))
            if not query_tokens or not matched:
                continue
            coverage = len(matched) / len(query_tokens)
            density = len(matched) / max(1, len(searchable_tokens))
            phrase_bonus = 0.12 if plan.query.casefold() in searchable.casefold() else 0.0
            metadata_bonus = self._metadata_bonus(document, plan)
            score = min(
                0.99,
                0.68 * coverage + 0.12 * min(1.0, density * 10) + phrase_bonus + metadata_bonus,
            )
            if score >= self.abstain_threshold:
                chunks_by_document[document_id].append(
                    _ChunkScore(chunk=chunk, score=round(score, 4), matched_tokens=matched)
                )

        logical_hits: dict[str, tuple[KnowledgeDocument, list[_ChunkScore]]] = {}
        for document_id, scores in chunks_by_document.items():
            document = documents[document_id]
            logical_id = (
                document.case_id
                if document.document_type == KnowledgeDocumentType.RCA_CASE.value
                and document.case_id
                else document.document_id
            )
            if logical_id in logical_hits:
                existing_document, existing_scores = logical_hits[logical_id]
                existing_scores.extend(scores)
                if max(item.score for item in scores) > max(
                    item.score for item in existing_scores[: -len(scores)]
                ):
                    logical_hits[logical_id] = (document, existing_scores)
                else:
                    logical_hits[logical_id] = (existing_document, existing_scores)
            else:
                logical_hits[logical_id] = (document, list(scores))

        ordered = sorted(
            logical_hits.items(),
            key=lambda item: (
                max(score.score for score in item[1][1]),
                item[0],
            ),
            reverse=True,
        )[: plan.top_k]
        hits: list[KnowledgeLookupHit] = []
        for rank, (_, (document, scores)) in enumerate(ordered, start=1):
            ranked_chunks = sorted(scores, key=lambda item: item.score, reverse=True)
            best = ranked_chunks[0]
            matched_tokens = sorted(
                {token for item in ranked_chunks[:3] for token in item.matched_tokens}
            )
            hits.append(
                KnowledgeLookupHit(
                    rank=rank,
                    document=document,
                    score=best.score,
                    matched_chunk_ids=tuple(item.chunk.chunk_id for item in ranked_chunks[:3]),
                    excerpt=best.chunk.content[:600],
                    evidence_id=f"KEV_{lookup_id.removeprefix('KLOOK_')}_{rank:03d}",
                    relevance_reason=(
                        "Python keyword and metadata rules matched: "
                        + ", ".join(matched_tokens[:12])
                    ),
                    retrieval_strategy="chunk_keyword",
                    score_components={"keyword": best.score},
                )
            )
        return tuple(hits)

    @staticmethod
    def _document_in_scope(document: KnowledgeDocument, plan: KnowledgeLookupPlan) -> bool:
        if not _matches_filter(document.module, plan.module):
            return False
        if not _matches_filter(document.equipment_type, plan.equipment_type):
            return False
        if not _matches_filter(document.operation, plan.operation):
            return False
        if not _matches_filter(document.defect_type, plan.defect_type):
            return False
        expected_tags = {item.casefold() for item in plan.tags}
        actual_tags = {item.casefold() for item in document.tags}
        return expected_tags <= actual_tags

    @staticmethod
    def _metadata_bonus(document: KnowledgeDocument, plan: KnowledgeLookupPlan) -> float:
        bonus = 0.0
        for actual, expected in (
            (document.module, plan.module),
            (document.equipment_type, plan.equipment_type),
            (document.operation, plan.operation),
            (document.defect_type, plan.defect_type),
        ):
            if expected and _matches_filter(actual, expected):
                bonus += 0.025
        if plan.tags:
            bonus += min(0.05, len(plan.tags) * 0.01)
        return bonus


class KnowledgeLookupService:
    """Plan one hard-scoped Knowledge Action and return reference-only results."""

    def __init__(
        self,
        store: KnowledgeStore,
        retriever: KnowledgeLookupRetriever | None = None,
    ) -> None:
        self.store = store
        self.retriever = retriever or DocumentChunkKeywordRetriever(store)

    def lookup(
        self,
        *,
        query: str,
        question_kind: str,
        document_type: str | None = None,
        module: str = "",
        equipment_type: str = "",
        operation: str = "",
        defect_type: str = "",
        tags: Iterable[str] = (),
        top_k: int = 5,
    ) -> KnowledgeLookupResult:
        try:
            kind = KnowledgeQuestionKind(question_kind)
        except ValueError as exc:
            raise KnowledgeIngestionError(
                "INVALID_KNOWLEDGE_QUESTION_KIND",
                "question_kind must be historical_match, procedure_guidance, "
                "or engineering_note_lookup",
            ) from exc
        if document_type is not None and document_type != kind.document_type:
            raise KnowledgeIngestionError(
                "QUESTION_DOCUMENT_TYPE_MISMATCH",
                f"{kind.value} can only retrieve {kind.document_type}",
            )
        plan = KnowledgeLookupPlan(
            intent=KnowledgeLookupIntent.KNOWLEDGE_LOOKUP.value,
            question_kind=kind.value,
            query=query.strip(),
            allowed_document_types=(kind.document_type,),
            reason=(
                f"User requested {kind.value}; Python capability mapping restricts "
                f"the Knowledge Agent to {kind.action} over {kind.document_type}."
            ),
            module=module.strip(),
            equipment_type=equipment_type.strip(),
            operation=operation.strip(),
            defect_type=defect_type.strip(),
            tags=tuple(dict.fromkeys(item.strip() for item in tags if item.strip())),
            top_k=top_k,
        )
        lookup_id = f"KLOOK_{uuid4().hex.upper()}"
        hits = self.retriever.retrieve(plan, lookup_id=lookup_id)
        status = "completed" if hits else "no_match"
        warnings = (
            ("No approved Active Index asset matched the query and metadata scope.",)
            if not hits
            else ()
        )
        trace = KnowledgeAgentTrace(
            agent="knowledge",
            action=plan.action,
            execution_reason=plan.reason,
            inputs={
                "query": plan.query,
                "question_kind": plan.question_kind,
                "allowed_document_types": list(plan.allowed_document_types),
                "module": plan.module,
                "equipment_type": plan.equipment_type,
                "operation": plan.operation,
                "defect_type": plan.defect_type,
                "tags": list(plan.tags),
                "top_k": plan.top_k,
            },
            output_evidence_ids=tuple(item.evidence_id for item in hits),
            stop_reason=(
                "Approved references returned; independent Knowledge lookup is complete."
                if hits
                else "No approved in-scope reference; abstained without running RCA Agents."
            ),
        )
        return KnowledgeLookupResult(
            lookup_id=lookup_id,
            plan=plan,
            status=status,
            hits=hits,
            agent_trace=(trace,),
            answer_boundary=ANSWER_BOUNDARY,
            warnings=warnings,
        )
