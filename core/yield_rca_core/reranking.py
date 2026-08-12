"""Feature-flagged Cross-Encoder reranking and explicit score calibration."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from importlib import import_module
from math import exp, log
from pathlib import Path
from typing import Protocol

from yield_rca_core.hybrid_retrieval import (
    HybridRetrievalConfigurationError,
    KnowledgeLookupRetriever,
)
from yield_rca_core.knowledge_models import KnowledgeLookupHit, KnowledgeLookupPlan

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + exp(-value))
    exponential = exp(value)
    return exponential / (1.0 + exponential)


def reranker_document_text(hit: KnowledgeLookupHit) -> str:
    document = hit.document
    return " ".join(
        (
            document.title,
            document.module,
            document.equipment_type,
            document.operation,
            document.defect_type,
            " ".join(document.tags),
            hit.excerpt,
        )
    )


class RerankerBackend(Protocol):
    model_name: str
    model_revision: str
    device: str

    def score_logits(
        self,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]: ...


class SentenceTransformerRerankerBackend:
    """Lazy multilingual CrossEncoder returning raw relevance logits."""

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        *,
        revision: str = DEFAULT_RERANKER_REVISION,
        device: str = "auto",
        batch_size: int = 16,
        max_length: int = 512,
        model_path: Path | None = None,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"} and not device.startswith("cuda:"):
            raise ValueError("reranker device must be auto, cpu, cuda, or cuda:<index>")
        if batch_size < 1 or max_length < 32:
            raise ValueError("reranker batch_size and max_length must be positive")
        self.model_name = model_name
        self.model_revision = revision
        self.requested_device = device
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.model_path = model_path.resolve() if model_path is not None else None
        if self.model_path is not None and not self.model_path.is_dir():
            raise HybridRetrievalConfigurationError(
                f"local Reranker model path does not exist: {self.model_path}"
            )
        self._model: object | None = None

    def _load_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            torch = import_module("torch")
            CrossEncoder = import_module("sentence_transformers").CrossEncoder
        except ImportError as exc:
            raise HybridRetrievalConfigurationError(
                "install the retrieval extra to use CrossEncoder reranking"
            ) from exc
        if self.requested_device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        elif self.requested_device.startswith("cuda") and not torch.cuda.is_available():
            raise HybridRetrievalConfigurationError(
                f"reranker device {self.requested_device!r} requested but CUDA is unavailable"
            )
        else:
            self.device = self.requested_device
        self._model = CrossEncoder(
            str(self.model_path or self.model_name),
            revision=self.model_revision,
            device=self.device,
            max_length=self.max_length,
            activation_fn=torch.nn.Identity(),
        )
        return self._model

    def score_logits(
        self,
        query: str,
        documents: Sequence[str],
    ) -> tuple[float, ...]:
        if not documents:
            return ()
        model = self._load_model()
        scores = model.predict(  # type: ignore[attr-defined]
            [[query, document] for document in documents],
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        values = scores.tolist()
        return tuple(float(item[0] if isinstance(item, list) else item) for item in values)


@dataclass(frozen=True)
class ScoreCalibrationArtifact:
    schema_version: str
    calibrator: str
    model_name: str
    model_revision: str
    slope: float
    intercept: float
    calibration_query_ids: tuple[str, ...]
    training_pair_count: int

    @classmethod
    def load(cls, path: Path) -> ScoreCalibrationArtifact:
        data = json.loads(path.read_text(encoding="utf-8"))
        artifact = cls(
            schema_version=str(data.get("schema_version", "")),
            calibrator=str(data.get("calibrator", "")),
            model_name=str(data.get("model_name", "")),
            model_revision=str(data.get("model_revision", "")),
            slope=float(data.get("slope", 0)),
            intercept=float(data.get("intercept", 0)),
            calibration_query_ids=tuple(
                str(item) for item in data.get("calibration_query_ids", [])
            ),
            training_pair_count=int(data.get("training_pair_count", 0)),
        )
        artifact.validate()
        return artifact

    def validate(self) -> None:
        if self.schema_version != "1.0" or self.calibrator != "platt_logistic":
            raise HybridRetrievalConfigurationError("unsupported score calibration artifact")
        if not self.model_name or not self.model_revision or self.slope <= 0:
            raise HybridRetrievalConfigurationError("invalid score calibration parameters")
        if not self.calibration_query_ids or self.training_pair_count < 2:
            raise HybridRetrievalConfigurationError("calibration artifact has no training data")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "calibrator": self.calibrator,
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "slope": self.slope,
            "intercept": self.intercept,
            "calibration_query_ids": list(self.calibration_query_ids),
            "training_pair_count": self.training_pair_count,
        }


class PlattScoreCalibrator:
    def __init__(
        self,
        artifact: ScoreCalibrationArtifact,
        *,
        model_name: str,
        model_revision: str,
    ) -> None:
        artifact.validate()
        if (
            artifact.model_name != model_name
            or artifact.model_revision != model_revision
        ):
            raise HybridRetrievalConfigurationError(
                "calibration artifact does not match the configured Reranker"
            )
        self.artifact = artifact

    def calibrate_logit(self, logit: float) -> float:
        return sigmoid(self.artifact.slope * logit + self.artifact.intercept)


def fit_platt_score_calibration(
    logits: Sequence[float],
    labels: Sequence[int],
    *,
    model_name: str,
    model_revision: str,
    calibration_query_ids: Sequence[str],
    maximum_iterations: int = 100,
    l2_penalty: float = 1e-4,
) -> ScoreCalibrationArtifact:
    """Fit a two-parameter logistic calibrator with damped Newton steps."""

    if len(logits) != len(labels) or len(logits) < 2:
        raise HybridRetrievalConfigurationError(
            "calibration requires matching logit/label arrays with at least two pairs"
        )
    if set(labels) != {0, 1}:
        raise HybridRetrievalConfigurationError(
            "calibration labels must contain both 0 and 1"
        )
    if not calibration_query_ids:
        raise HybridRetrievalConfigurationError("calibration query IDs must not be empty")
    slope = 1.0
    positive_rate = min(0.999, max(0.001, sum(labels) / len(labels)))
    intercept = -1.0 * log((1.0 - positive_rate) / positive_rate)
    for _ in range(maximum_iterations):
        gradient_slope = l2_penalty * slope
        gradient_intercept = 0.0
        hessian_ss = l2_penalty
        hessian_si = 0.0
        hessian_ii = 0.0
        for logit, label in zip(logits, labels, strict=True):
            probability = sigmoid(slope * float(logit) + intercept)
            residual = probability - label
            weight = max(probability * (1.0 - probability), 1e-9)
            gradient_slope += residual * logit
            gradient_intercept += residual
            hessian_ss += weight * logit * logit
            hessian_si += weight * logit
            hessian_ii += weight
        determinant = hessian_ss * hessian_ii - hessian_si * hessian_si
        if abs(determinant) < 1e-12:
            break
        delta_slope = (
            hessian_ii * gradient_slope - hessian_si * gradient_intercept
        ) / determinant
        delta_intercept = (
            -hessian_si * gradient_slope + hessian_ss * gradient_intercept
        ) / determinant
        step = 1.0
        while slope - step * delta_slope <= 1e-6 and step > 1e-4:
            step *= 0.5
        slope -= step * delta_slope
        intercept -= step * delta_intercept
        if max(abs(step * delta_slope), abs(step * delta_intercept)) < 1e-7:
            break
    if slope <= 0:
        raise HybridRetrievalConfigurationError(
            "fitted calibration slope is not positive; Reranker ordering is inconsistent"
        )
    artifact = ScoreCalibrationArtifact(
        schema_version="1.0",
        calibrator="platt_logistic",
        model_name=model_name,
        model_revision=model_revision,
        slope=round(slope, 12),
        intercept=round(intercept, 12),
        calibration_query_ids=tuple(dict.fromkeys(calibration_query_ids)),
        training_pair_count=len(logits),
    )
    artifact.validate()
    return artifact


class RerankedKnowledgeRetriever:
    """Rerank logical Hybrid candidates without changing governance scope."""

    def __init__(
        self,
        base_retriever: KnowledgeLookupRetriever,
        reranker: RerankerBackend,
        *,
        calibrator: PlattScoreCalibrator | None = None,
        candidate_k: int = 20,
    ) -> None:
        if not 1 <= candidate_k <= 20:
            raise ValueError("reranker candidate_k must be between 1 and 20")
        self.base_retriever = base_retriever
        self.reranker = reranker
        self.calibrator = calibrator
        self.candidate_k = candidate_k

    def retrieve(
        self,
        plan: KnowledgeLookupPlan,
        *,
        lookup_id: str,
    ) -> tuple[KnowledgeLookupHit, ...]:
        candidate_plan = replace(plan, top_k=max(plan.top_k, self.candidate_k))
        candidates = self.base_retriever.retrieve(candidate_plan, lookup_id=lookup_id)
        if not candidates:
            return ()
        documents = tuple(reranker_document_text(item) for item in candidates)
        logits = self.reranker.score_logits(plan.query, documents)
        if len(logits) != len(candidates):
            raise HybridRetrievalConfigurationError(
                "Reranker returned a different number of scores than candidates"
            )
        reranked: list[tuple[float, int, KnowledgeLookupHit]] = []
        for candidate, logit in zip(candidates, logits, strict=True):
            reranker_score = sigmoid(logit)
            calibrated = (
                self.calibrator.calibrate_logit(logit) if self.calibrator else None
            )
            components = dict(candidate.score_components)
            components["reranker"] = round(reranker_score, 6)
            updated = replace(
                candidate,
                score=round(reranker_score, 6),
                retrieval_strategy=f"{candidate.retrieval_strategy}+cross_encoder",
                score_components=components,
                calibrated_relevance=(round(calibrated, 6) if calibrated is not None else None),
                source_confidence=candidate.document.source_confidence,
                relevance_reason=(
                    candidate.relevance_reason
                    + "; CrossEncoder reranked the approved logical-asset candidate."
                ),
            )
            reranked.append((reranker_score, candidate.rank, updated))
        ordered = sorted(reranked, key=lambda item: (-item[0], item[1]))[: plan.top_k]
        return tuple(
            replace(item, rank=rank)
            for rank, (_, _, item) in enumerate(ordered, start=1)
        )
