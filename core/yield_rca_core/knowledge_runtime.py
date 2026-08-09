"""Environment-owned factory for the online Knowledge retrieval runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from yield_rca_core.causal_retrieval import CausalLaneKnowledgeRetriever
from yield_rca_core.hybrid_retrieval import (
    ChunkCandidateSource,
    ExactVectorCandidateSource,
    HybridDocumentChunkRetriever,
    HybridRetrievalConfigurationError,
    KnowledgeLookupRetriever,
    PostgresBM25CandidateSource,
    PythonBM25CandidateSource,
    SentenceTransformerEmbeddingBackend,
)
from yield_rca_core.knowledge_ingestion import KnowledgeStore
from yield_rca_core.knowledge_lookup import DocumentChunkKeywordRetriever
from yield_rca_core.knowledge_vector_store import PostgresExactVectorCandidateSource
from yield_rca_core.reranking import (
    DEFAULT_RERANKER_MODEL,
    DEFAULT_RERANKER_REVISION,
    PlattScoreCalibrator,
    RerankedKnowledgeRetriever,
    ScoreCalibrationArtifact,
    SentenceTransformerRerankerBackend,
)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HybridRetrievalConfigurationError(
        f"{name} must be one of 1/0, true/false, yes/no, or on/off"
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise HybridRetrievalConfigurationError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class KnowledgeRetrievalSettings:
    mode: str = "keyword"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    embedding_device: str = "auto"
    embedding_batch_size: int = 32
    reranker_enabled: bool = False
    reranker_model: str = DEFAULT_RERANKER_MODEL
    reranker_revision: str = DEFAULT_RERANKER_REVISION
    reranker_device: str = "auto"
    reranker_batch_size: int = 16
    reranker_candidate_k: int = 20
    reranker_local_path: Path | None = None
    calibration_artifact: Path | None = None
    causal_scope_enabled: bool = False
    causal_candidate_budget: int = 20
    causal_lane_minimum: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"keyword", "hybrid"}:
            raise HybridRetrievalConfigurationError(
                "YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE must be keyword or hybrid"
            )
        if self.reranker_enabled and self.mode != "hybrid":
            raise HybridRetrievalConfigurationError(
                "Knowledge Reranker requires YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE=hybrid"
            )
        if self.embedding_batch_size < 1 or self.reranker_batch_size < 1:
            raise HybridRetrievalConfigurationError("Knowledge model batch sizes must be positive")
        if not 1 <= self.reranker_candidate_k <= 20:
            raise HybridRetrievalConfigurationError(
                "YIELD_RCA_KNOWLEDGE_RERANKER_CANDIDATE_K must be between 1 and 20"
            )
        if not 4 <= self.causal_candidate_budget <= 80:
            raise HybridRetrievalConfigurationError(
                "YIELD_RCA_CAUSAL_SCOPE_CANDIDATE_BUDGET must be between 4 and 80"
            )
        if not 1 <= self.causal_lane_minimum <= 5:
            raise HybridRetrievalConfigurationError(
                "YIELD_RCA_CAUSAL_SCOPE_LANE_MINIMUM must be between 1 and 5"
            )

    @classmethod
    def from_env(cls) -> KnowledgeRetrievalSettings:
        artifact = os.getenv("YIELD_RCA_KNOWLEDGE_CALIBRATION_ARTIFACT", "").strip()
        local_reranker = os.getenv(
            "YIELD_RCA_KNOWLEDGE_RERANKER_LOCAL_PATH", ""
        ).strip()
        return cls(
            mode=os.getenv("YIELD_RCA_KNOWLEDGE_RETRIEVER_MODE", "keyword")
            .strip()
            .casefold(),
            embedding_model=os.getenv(
                "YIELD_RCA_KNOWLEDGE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
            ).strip(),
            embedding_revision=os.getenv(
                "YIELD_RCA_KNOWLEDGE_EMBEDDING_REVISION",
                DEFAULT_EMBEDDING_REVISION,
            ).strip(),
            embedding_device=os.getenv(
                "YIELD_RCA_KNOWLEDGE_EMBEDDING_DEVICE", "auto"
            ).strip(),
            embedding_batch_size=_env_int(
                "YIELD_RCA_KNOWLEDGE_EMBEDDING_BATCH_SIZE", 32
            ),
            reranker_enabled=_env_bool(
                "YIELD_RCA_KNOWLEDGE_RERANKER_ENABLED", False
            ),
            reranker_model=os.getenv(
                "YIELD_RCA_KNOWLEDGE_RERANKER_MODEL", DEFAULT_RERANKER_MODEL
            ).strip(),
            reranker_revision=os.getenv(
                "YIELD_RCA_KNOWLEDGE_RERANKER_REVISION",
                DEFAULT_RERANKER_REVISION,
            ).strip(),
            reranker_device=os.getenv(
                "YIELD_RCA_KNOWLEDGE_RERANKER_DEVICE", "auto"
            ).strip(),
            reranker_batch_size=_env_int(
                "YIELD_RCA_KNOWLEDGE_RERANKER_BATCH_SIZE", 16
            ),
            reranker_candidate_k=_env_int(
                "YIELD_RCA_KNOWLEDGE_RERANKER_CANDIDATE_K", 20
            ),
            reranker_local_path=(
                Path(local_reranker).resolve() if local_reranker else None
            ),
            calibration_artifact=Path(artifact).resolve() if artifact else None,
            causal_scope_enabled=_env_bool(
                "YIELD_RCA_CAUSAL_SCOPE_ENABLED", False
            ),
            causal_candidate_budget=_env_int(
                "YIELD_RCA_CAUSAL_SCOPE_CANDIDATE_BUDGET", 20
            ),
            causal_lane_minimum=_env_int(
                "YIELD_RCA_CAUSAL_SCOPE_LANE_MINIMUM", 1
            ),
        )


def build_knowledge_retriever(
    store: KnowledgeStore,
    *,
    database_url: str = "",
    settings: KnowledgeRetrievalSettings | None = None,
) -> KnowledgeLookupRetriever:
    """Build the explicit online mode without loading models at service startup."""

    configured = settings or KnowledgeRetrievalSettings.from_env()
    retriever: KnowledgeLookupRetriever
    if configured.mode == "keyword":
        retriever = DocumentChunkKeywordRetriever(store)
    else:
        embedding = SentenceTransformerEmbeddingBackend(
            configured.embedding_model,
            revision=configured.embedding_revision,
            device=configured.embedding_device,
            batch_size=configured.embedding_batch_size,
        )
        lexical_source: ChunkCandidateSource
        vector_source: ChunkCandidateSource
        if database_url:
            lexical_source = PostgresBM25CandidateSource(database_url)
            vector_source = PostgresExactVectorCandidateSource(database_url, embedding)
        else:
            lexical_source = PythonBM25CandidateSource(store)
            vector_source = ExactVectorCandidateSource(store, embedding)
        retriever = HybridDocumentChunkRetriever(
            lexical_source,
            vector_source,
        )
    if configured.causal_scope_enabled:
        retriever = CausalLaneKnowledgeRetriever(
            retriever,
            candidate_budget=configured.causal_candidate_budget,
            lane_minimum=configured.causal_lane_minimum,
        )
    if not configured.reranker_enabled:
        return retriever

    reranker = SentenceTransformerRerankerBackend(
        configured.reranker_model,
        revision=configured.reranker_revision,
        device=configured.reranker_device,
        batch_size=configured.reranker_batch_size,
        model_path=configured.reranker_local_path,
    )
    calibrator = None
    if configured.calibration_artifact is not None:
        artifact = ScoreCalibrationArtifact.load(configured.calibration_artifact)
        calibrator = PlattScoreCalibrator(
            artifact,
            model_name=reranker.model_name,
            model_revision=reranker.model_revision,
        )
    result: KnowledgeLookupRetriever = RerankedKnowledgeRetriever(
        retriever,
        reranker,
        calibrator=calibrator,
        candidate_k=configured.reranker_candidate_k,
    )
    return result
