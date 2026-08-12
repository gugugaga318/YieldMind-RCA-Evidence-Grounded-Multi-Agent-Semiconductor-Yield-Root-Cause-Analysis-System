"""Deterministic and optional-Qwen Synthetic knowledge corpus generation.

Canonical facts are the reviewed source of truth.  Document generation may see
the full facts, while query generation receives a separate projection that
excludes root cause, corrective action, answer IDs, and generated document text.
Qrels are always built by Python and are never accepted from an LLM.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from yield_rca_core.llm_gateway import LLMClient, LLMRequest
from yield_rca_core.models import AgentKind

SYNTHETIC_KNOWLEDGE_SCHEMA_VERSION = "1.0"
DOCUMENT_PROMPT_NAME = "synthetic_knowledge_document"
QUERY_PROMPT_NAME = "synthetic_knowledge_query"
PROMPT_VERSION = "v1"


class SyntheticKnowledgeError(ValueError):
    """Raised when canonical facts or generated knowledge violate the contract."""


@dataclass(frozen=True)
class GeneratedDocument:
    generation_key: str
    title: str
    content: str


@dataclass(frozen=True)
class GeneratedQuery:
    query_key: str
    text: str


class SyntheticKnowledgeTextProvider(Protocol):
    """Document/query writer used by the governed corpus builder."""

    provider_name: str
    model_name: str
    paid_call_count: int

    def generate_documents(
        self, document_inputs: list[dict[str, Any]]
    ) -> list[GeneratedDocument]: ...

    def generate_queries(self, query_inputs: list[dict[str, Any]]) -> list[GeneratedQuery]: ...


class TemplateSyntheticKnowledgeTextProvider:
    """No-cost deterministic provider for CI and a committed baseline snapshot."""

    provider_name = "deterministic_template"
    model_name = "none"
    paid_call_count = 0

    def generate_documents(self, document_inputs: list[dict[str, Any]]) -> list[GeneratedDocument]:
        documents: list[GeneratedDocument] = []
        for item in document_inputs:
            kind = item["document_type"]
            if kind == "RCA_CASE":
                content = (
                    "Synthetic RCA case. "
                    f"Observed symptom: {item['observable_context_en']}. "
                    f"Root cause: {item['root_cause']}. "
                    f"Corrective actions: {'; '.join(item['corrective_actions'])}. "
                    f"Engineering boundary: {item['engineering_boundary']}"
                )
            elif kind == "SOP":
                content = (
                    "Synthetic SOP. "
                    f"Trigger and objective: {item['observable_context_en']}. "
                    f"Procedure: {'; '.join(item['procedure_steps'])}. "
                    f"Safety and decision boundary: {item['engineering_boundary']}"
                )
            else:
                content = (
                    "Synthetic engineering note. "
                    f"Observation: {item['observable_context_en']}. "
                    f"Engineering interpretation: {item['interpretation']}. "
                    f"Boundary: {item['engineering_boundary']}"
                )
            documents.append(
                GeneratedDocument(
                    generation_key=item["generation_key"],
                    title=item["title_seed"],
                    content=content,
                )
            )
        return documents

    def generate_queries(self, query_inputs: list[dict[str, Any]]) -> list[GeneratedQuery]:
        queries: list[GeneratedQuery] = []
        for item in query_inputs:
            language = item["language"]
            context = (
                item["observable_context_zh"]
                if language in {"zh", "mixed"}
                else item["observable_context_en"]
            )
            kind = item["question_kind"]
            if kind == "historical_match":
                suffix = (
                    "，请检索可以参考的相似历史RCA案例。"
                    if language in {"zh", "mixed"}
                    else ". Find similar historical RCA cases."
                )
            elif kind == "procedure_guidance":
                suffix = (
                    "，应该按照什么SOP进行确认和处置？"
                    if language in {"zh", "mixed"}
                    else ". Which SOP should be followed for verification and containment?"
                )
            else:
                suffix = (
                    "，查找相关工程笔记和适用边界。"
                    if language in {"zh", "mixed"}
                    else ". Retrieve the relevant engineering note and its boundary."
                )
            queries.append(
                GeneratedQuery(
                    query_key=item["query_key"],
                    text=f"{item['module']}: {context}{suffix}",
                )
            )
        return queries


class QwenSyntheticKnowledgeTextProvider:
    """Explicit paid provider; the CLI owns opt-in and call-budget enforcement."""

    provider_name = "qwen"

    def __init__(
        self,
        client: LLMClient,
        *,
        batch_size: int = 10,
        max_paid_calls: int = 20,
    ) -> None:
        if batch_size <= 0 or max_paid_calls <= 0:
            raise SyntheticKnowledgeError("batch size and paid-call cap must be positive")
        self.client = client
        self.model_name = client.model
        self.batch_size = batch_size
        self.max_paid_calls = max_paid_calls
        self.paid_call_count = 0

    def _complete(self, *, prompt_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.paid_call_count >= self.max_paid_calls:
            raise SyntheticKnowledgeError("synthetic generation exceeded paid LLM-call cap")
        response = self.client.complete_json(
            LLMRequest(
                agent=AgentKind.KNOWLEDGE.value,
                prompt_name=prompt_name,
                prompt_version=PROMPT_VERSION,
                payload=payload,
                temperature=0.2,
            )
        )
        self.paid_call_count += 1
        return dict(response.data)

    def generate_documents(self, document_inputs: list[dict[str, Any]]) -> list[GeneratedDocument]:
        generated: list[GeneratedDocument] = []
        for offset in range(0, len(document_inputs), self.batch_size):
            batch = document_inputs[offset : offset + self.batch_size]
            data = self._complete(
                prompt_name=DOCUMENT_PROMPT_NAME,
                payload={"documents": batch},
            )
            raw_items = data.get("documents")
            if not isinstance(raw_items, list):
                raise SyntheticKnowledgeError("Qwen document output must contain documents[]")
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise SyntheticKnowledgeError("Qwen document output item must be an object")
                generated.append(
                    GeneratedDocument(
                        generation_key=str(raw.get("generation_key", "")).strip(),
                        title=str(raw.get("title", "")).strip(),
                        content=str(raw.get("content", "")).strip(),
                    )
                )
        return generated

    def generate_queries(self, query_inputs: list[dict[str, Any]]) -> list[GeneratedQuery]:
        generated: list[GeneratedQuery] = []
        for offset in range(0, len(query_inputs), self.batch_size):
            batch = query_inputs[offset : offset + self.batch_size]
            data = self._complete(
                prompt_name=QUERY_PROMPT_NAME,
                payload={"queries": batch},
            )
            raw_items = data.get("queries")
            if not isinstance(raw_items, list):
                raise SyntheticKnowledgeError("Qwen query output must contain queries[]")
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise SyntheticKnowledgeError("Qwen query output item must be an object")
                generated.append(
                    GeneratedQuery(
                        query_key=str(raw.get("query_key", "")).strip(),
                        text=str(raw.get("text", "")).strip(),
                    )
                )
        return generated


def load_canonical_facts(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SyntheticKnowledgeError("canonical facts must be a JSON object")
    validate_canonical_facts(data)
    return data


def _all_assets(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for section, document_type in (
        ("rca_cases", "RCA_CASE"),
        ("sops", "SOP"),
        ("engineering_notes", "ENGINEERING_NOTE"),
    ):
        for raw in canonical[section]:
            item = dict(raw)
            item["document_type"] = document_type
            item["validation_status"] = "CONFIRMED"
            assets.append(item)
    for raw in canonical.get("unapproved_assets", []):
        item = dict(raw)
        item["validation_status"] = str(item.get("validation_status", "DRAFT")).upper()
        assets.append(item)
    return assets


def _evaluation_asset_id(asset: dict[str, Any]) -> str:
    if asset["document_type"] == "RCA_CASE":
        return str(asset["case_id"])
    return str(asset["document_id"])


def validate_canonical_facts(canonical: dict[str, Any]) -> None:
    if canonical.get("schema_version") != SYNTHETIC_KNOWLEDGE_SCHEMA_VERSION:
        raise SyntheticKnowledgeError("unsupported Synthetic knowledge schema version")
    if not canonical.get("synthetic"):
        raise SyntheticKnowledgeError("canonical facts must be explicitly synthetic")
    for key in (
        "corpus_version",
        "dataset_created_at",
        "rca_cases",
        "sops",
        "engineering_notes",
        "no_answer_queries",
    ):
        if key not in canonical:
            raise SyntheticKnowledgeError(f"canonical facts missing {key}")

    if not 30 <= len(canonical["rca_cases"]) <= 40:
        raise SyntheticKnowledgeError("canonical corpus requires 30-40 confirmed RCA cases")
    if not 10 <= len(canonical["sops"]) <= 15:
        raise SyntheticKnowledgeError("canonical corpus requires 10-15 confirmed SOPs")
    if not 10 <= len(canonical["engineering_notes"]) <= 15:
        raise SyntheticKnowledgeError("canonical corpus requires 10-15 confirmed Engineering Notes")
    if not 15 <= len(canonical["no_answer_queries"]) <= 20:
        raise SyntheticKnowledgeError("canonical corpus requires 15-20 no-answer queries")

    assets = _all_assets(canonical)
    evaluation_ids = [_evaluation_asset_id(item) for item in assets]
    document_ids = [str(item.get("document_id", "")) for item in assets]
    if any(not item for item in evaluation_ids + document_ids):
        raise SyntheticKnowledgeError("all assets require evaluation and document IDs")
    if len(evaluation_ids) != len(set(evaluation_ids)):
        raise SyntheticKnowledgeError("evaluation asset IDs must be unique")
    if len(document_ids) != len(set(document_ids)):
        raise SyntheticKnowledgeError("document IDs must be unique")

    known_ids = set(evaluation_ids)
    confirmed_ids = {
        _evaluation_asset_id(item) for item in assets if item["validation_status"] == "CONFIRMED"
    }
    for item in assets:
        required = {
            "generation_key",
            "document_id",
            "title_seed",
            "module",
            "equipment_type",
            "operation",
            "observable_context_en",
            "observable_context_zh",
            "engineering_boundary",
            "tags",
        }
        missing = sorted(key for key in required if not item.get(key))
        if missing:
            raise SyntheticKnowledgeError(
                f"asset {_evaluation_asset_id(item)} missing fields: {missing}"
            )
        if item["document_type"] == "RCA_CASE":
            for key in ("case_id", "technology", "root_cause", "corrective_actions"):
                if not item.get(key):
                    raise SyntheticKnowledgeError(
                        f"RCA asset {_evaluation_asset_id(item)} missing {key}"
                    )
            hard_negative = str(item.get("hard_negative_asset_id", ""))
            if item["validation_status"] == "CONFIRMED":
                if hard_negative not in confirmed_ids:
                    raise SyntheticKnowledgeError(
                        f"RCA asset {_evaluation_asset_id(item)} has unknown hard negative"
                    )
                if hard_negative == _evaluation_asset_id(item):
                    raise SyntheticKnowledgeError("RCA hard negative cannot reference itself")
                related = str(item.get("related_asset_id", ""))
                if related and related not in confirmed_ids:
                    raise SyntheticKnowledgeError("related_asset_id must be CONFIRMED")
                background = str(item.get("background_asset_id", ""))
                if background and background not in confirmed_ids:
                    raise SyntheticKnowledgeError("background_asset_id must be CONFIRMED")
        elif item["document_type"] == "SOP":
            if not item.get("procedure_steps"):
                raise SyntheticKnowledgeError("SOP requires procedure_steps")
        elif item["document_type"] == "ENGINEERING_NOTE":
            if not item.get("interpretation"):
                raise SyntheticKnowledgeError("Engineering Note requires interpretation")
        else:
            raise SyntheticKnowledgeError("unsupported document_type")

    generation_keys = [str(item["generation_key"]) for item in assets]
    if len(generation_keys) != len(set(generation_keys)):
        raise SyntheticKnowledgeError("generation_key values must be unique")
    if not known_ids:
        raise SyntheticKnowledgeError("canonical corpus has no assets")


def _document_input(asset: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generation_key": asset["generation_key"],
        "document_type": asset["document_type"],
        "title_seed": asset["title_seed"],
        "technology": asset.get("technology", ""),
        "module": asset["module"],
        "equipment_type": asset["equipment_type"],
        "operation": asset["operation"],
        "observable_context_en": asset["observable_context_en"],
        "root_cause": asset.get("root_cause", ""),
        "corrective_actions": list(asset.get("corrective_actions", [])),
        "procedure_steps": list(asset.get("procedure_steps", [])),
        "interpretation": asset.get("interpretation", ""),
        "engineering_boundary": asset["engineering_boundary"],
        "tags": list(asset["tags"]),
        "synthetic": True,
    }
    return payload


def _query_specs(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    confirmed = [item for item in assets if item["validation_status"] == "CONFIRMED"]
    for index, asset in enumerate(confirmed, start=1):
        languages: tuple[str, ...]
        if asset["document_type"] == "RCA_CASE":
            languages = ("en", "zh")
            question_kind = "historical_match"
        elif asset["document_type"] == "SOP":
            languages = ("zh",) if index % 2 == 0 else ("en",)
            question_kind = "procedure_guidance"
        else:
            languages = ("mixed",) if index % 2 == 0 else ("en",)
            question_kind = "engineering_note_lookup"
        for language in languages:
            query_id = f"Q_{asset['generation_key']}_{language.upper()}"
            specs.append(
                {
                    "query_key": f"QUERY_{len(specs) + 1:03d}",
                    "query_id": query_id,
                    "asset_id": _evaluation_asset_id(asset),
                    "language": language,
                    "question_kind": question_kind,
                    "module": asset["module"],
                    "equipment_type": asset["equipment_type"],
                    "observable_context_en": asset["observable_context_en"],
                    "observable_context_zh": asset["observable_context_zh"],
                    "hard_negative_asset_ids": (
                        [asset["hard_negative_asset_id"]]
                        if asset["document_type"] == "RCA_CASE"
                        else []
                    ),
                    "related_asset_id": asset.get("related_asset_id", ""),
                    "background_asset_id": asset.get("background_asset_id", ""),
                    "forbidden_query_terms": [
                        asset.get("root_cause", ""),
                        *asset.get("forbidden_query_terms", []),
                    ],
                }
            )
    return specs


def _query_input(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the only payload a query-writing LLM is permitted to see."""

    return {
        "query_key": spec["query_key"],
        "language": spec["language"],
        "question_kind": spec["question_kind"],
        "module": spec["module"],
        "equipment_type": spec["equipment_type"],
        "observable_context_en": spec["observable_context_en"],
        "observable_context_zh": spec["observable_context_zh"],
    }


def _index_generated_documents(
    generated: list[GeneratedDocument], expected_keys: set[str]
) -> dict[str, GeneratedDocument]:
    mapping: dict[str, GeneratedDocument] = {}
    for item in generated:
        if not item.generation_key or not item.title or not item.content:
            raise SyntheticKnowledgeError("generated document fields must not be empty")
        if item.generation_key in mapping:
            raise SyntheticKnowledgeError("generated document key must be unique")
        mapping[item.generation_key] = item
    if set(mapping) != expected_keys:
        raise SyntheticKnowledgeError("generated document keys do not match canonical assets")
    return mapping


def _normalize_for_leakage(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _index_generated_queries(
    generated: list[GeneratedQuery], specs: list[dict[str, Any]]
) -> dict[str, GeneratedQuery]:
    by_key: dict[str, GeneratedQuery] = {}
    expected = {item["query_key"] for item in specs}
    specs_by_key = {item["query_key"]: item for item in specs}
    normalized_texts: set[str] = set()
    for item in generated:
        normalized = _normalize_for_leakage(item.text)
        if not item.query_key or not normalized:
            raise SyntheticKnowledgeError("generated query fields must not be empty")
        if item.query_key in by_key:
            raise SyntheticKnowledgeError("generated query key must be unique")
        if normalized in normalized_texts:
            raise SyntheticKnowledgeError("generated queries must be unique after normalization")
        spec = specs_by_key.get(item.query_key)
        if spec is None:
            raise SyntheticKnowledgeError("generated query returned an unknown query key")
        forbidden = [
            value
            for value in (
                spec["asset_id"],
                spec["query_id"],
                *spec["forbidden_query_terms"],
            )
            if value
        ]
        for value in forbidden:
            normalized_forbidden = _normalize_for_leakage(str(value))
            if normalized_forbidden and normalized_forbidden in normalized:
                raise SyntheticKnowledgeError(
                    f"query {spec['query_id']} leaks a forbidden answer term"
                )
        by_key[item.query_key] = item
        normalized_texts.add(normalized)
    if set(by_key) != expected:
        raise SyntheticKnowledgeError("generated query keys do not match query specifications")
    return by_key


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_synthetic_knowledge_corpus(
    canonical: dict[str, Any],
    provider: SyntheticKnowledgeTextProvider,
    *,
    canonical_sha256: str,
) -> dict[str, Any]:
    """Build governed CSV rows, corpus metadata, ground truth, and manifest."""

    validate_canonical_facts(canonical)
    assets = _all_assets(canonical)
    document_inputs = [_document_input(item) for item in assets]
    generated_documents = _index_generated_documents(
        provider.generate_documents(document_inputs),
        {str(item["generation_key"]) for item in assets},
    )

    query_specs = _query_specs(assets)
    generated_queries = _index_generated_queries(
        provider.generate_queries([_query_input(item) for item in query_specs]),
        query_specs,
    )

    rca_rows: list[dict[str, str]] = []
    document_rows: list[dict[str, str]] = []
    corpus_documents: list[dict[str, Any]] = []
    manifest_assets: list[dict[str, str]] = []
    for asset in assets:
        generated = generated_documents[asset["generation_key"]]
        evaluation_id = _evaluation_asset_id(asset)
        status = asset["validation_status"]
        if asset["document_type"] == "RCA_CASE":
            rca_rows.append(
                {
                    "case_id": asset["case_id"],
                    "title": generated.title,
                    "technology": asset["technology"],
                    "module": asset["module"],
                    "equipment_type": asset["equipment_type"],
                    "symptom": asset["observable_context_en"],
                    "root_cause": asset["root_cause"],
                    "solution": "; ".join(asset["corrective_actions"]),
                    "confidence": str(asset.get("confidence", "0.90")),
                    "created_at": canonical["dataset_created_at"],
                    "validation_status": status,
                }
            )
        document_rows.append(
            {
                "document_id": asset["document_id"],
                "case_id": asset.get("case_id", ""),
                "document_type": asset["document_type"],
                "title": generated.title,
                "content": generated.content,
                "tags": ";".join(asset["tags"]),
                "created_at": canonical["dataset_created_at"],
                "validation_status": status,
            }
        )
        corpus_documents.append(
            {
                "evaluation_asset_id": evaluation_id,
                "document_id": asset["document_id"],
                "case_id": asset.get("case_id") or None,
                "document_type": asset["document_type"],
                "title": generated.title,
                "content": generated.content,
                "language": "en",
                "module": asset["module"],
                "equipment_type": asset["equipment_type"],
                "operation": asset["operation"],
                "tags": list(asset["tags"]),
                "validation_status": status,
                "synthetic": True,
                "content_hash": _content_hash(generated.content),
                "publication_policy": canonical["publication_policy"],
                "generator_provider": provider.provider_name,
                "generator_model": provider.model_name,
                "prompt_version": PROMPT_VERSION,
            }
        )
        manifest_assets.append(
            {
                "asset_id": evaluation_id,
                "document_id": asset["document_id"],
                "document_type": asset["document_type"],
                "validation_status": status,
            }
        )

    queries: list[dict[str, Any]] = []
    qrels: dict[str, list[dict[str, int | str]]] = {}
    for spec in query_specs:
        generated_query = generated_queries[spec["query_key"]]
        queries.append(
            {
                "query_id": spec["query_id"],
                "text": generated_query.text,
                "language": spec["language"],
                "cross_language": spec["language"] in {"zh", "mixed"},
                "question_kind": spec["question_kind"],
                "module": spec["module"],
                "equipment_type": spec["equipment_type"],
                "no_answer": False,
                "hard_negative_asset_ids": spec["hard_negative_asset_ids"],
                "split": "test",
            }
        )
        judgments: dict[str, int] = {spec["asset_id"]: 3}
        if spec["related_asset_id"]:
            judgments[spec["related_asset_id"]] = 2
        if spec["background_asset_id"]:
            judgments[spec["background_asset_id"]] = 1
        for asset_id in spec["hard_negative_asset_ids"]:
            judgments[asset_id] = 0
        qrels[spec["query_id"]] = [
            {"asset_id": asset_id, "relevance": relevance}
            for asset_id, relevance in sorted(judgments.items())
        ]

    for item in canonical["no_answer_queries"]:
        query_id = str(item["query_id"])
        hard_negatives = [str(value) for value in item.get("hard_negative_asset_ids", [])]
        queries.append(
            {
                "query_id": query_id,
                "text": item["text"],
                "language": item["language"],
                "cross_language": False,
                "question_kind": item["question_kind"],
                "module": item.get("module", ""),
                "equipment_type": item.get("equipment_type", ""),
                "no_answer": True,
                "hard_negative_asset_ids": hard_negatives,
                "split": "test",
            }
        )
        qrels[query_id] = [
            {"asset_id": asset_id, "relevance": 0} for asset_id in sorted(hard_negatives)
        ]

    corpus = {
        "schema_version": SYNTHETIC_KNOWLEDGE_SCHEMA_VERSION,
        "corpus_version": canonical["corpus_version"],
        "synthetic": True,
        "publication_policy": canonical["publication_policy"],
        "documents": corpus_documents,
    }
    ground_truth = {
        "schema_version": "1.0",
        "corpus_version": canonical["corpus_version"],
        "relevance_threshold": 2,
        "queries": queries,
        "qrels": qrels,
        "generation_contract": {
            "query_generator_saw_root_cause": False,
            "query_generator_saw_solution": False,
            "query_generator_saw_document_text": False,
            "qrels_generated_by_python": True,
        },
    }
    manifest = {
        "schema_version": SYNTHETIC_KNOWLEDGE_SCHEMA_VERSION,
        "corpus_version": canonical["corpus_version"],
        "synthetic": True,
        "canonical_sha256": canonical_sha256,
        "dataset_created_at": canonical["dataset_created_at"],
        "publication_policy": canonical["publication_policy"],
        "generation": {
            "provider": provider.provider_name,
            "model": provider.model_name,
            "document_prompt": f"{DOCUMENT_PROMPT_NAME}_{PROMPT_VERSION}",
            "query_prompt": f"{QUERY_PROMPT_NAME}_{PROMPT_VERSION}",
            "paid_call_count": provider.paid_call_count,
            "qrels_generated_by_python": True,
            "query_root_cause_exposed": False,
        },
        "counts": {
            "confirmed_rca_cases": sum(
                item["document_type"] == "RCA_CASE" and item["validation_status"] == "CONFIRMED"
                for item in assets
            ),
            "confirmed_sops": sum(
                item["document_type"] == "SOP" and item["validation_status"] == "CONFIRMED"
                for item in assets
            ),
            "confirmed_engineering_notes": sum(
                item["document_type"] == "ENGINEERING_NOTE"
                and item["validation_status"] == "CONFIRMED"
                for item in assets
            ),
            "unapproved_assets": sum(item["validation_status"] != "CONFIRMED" for item in assets),
            "answerable_queries": len(query_specs),
            "no_answer_queries": len(canonical["no_answer_queries"]),
            "total_queries": len(queries),
        },
        "assets": manifest_assets,
    }
    return {
        "rca_rows": rca_rows,
        "document_rows": document_rows,
        "corpus": corpus,
        "ground_truth": ground_truth,
        "manifest": manifest,
    }


def canonical_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_synthetic_knowledge_corpus(
    built: dict[str, Any],
    *,
    output_dir: Path,
    ground_truth_path: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "rca_case.csv",
        built["rca_rows"],
        [
            "case_id",
            "title",
            "technology",
            "module",
            "equipment_type",
            "symptom",
            "root_cause",
            "solution",
            "confidence",
            "created_at",
            "validation_status",
        ],
    )
    _write_csv(
        output_dir / "knowledge_document.csv",
        built["document_rows"],
        [
            "document_id",
            "case_id",
            "document_type",
            "title",
            "content",
            "tags",
            "created_at",
            "validation_status",
        ],
    )
    _write_json(output_dir / "corpus.json", built["corpus"])
    _write_json(output_dir / "generation_manifest.json", built["manifest"])
    _write_json(ground_truth_path, built["ground_truth"])


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
