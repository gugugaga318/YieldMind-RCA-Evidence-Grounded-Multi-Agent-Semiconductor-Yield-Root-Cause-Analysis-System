"""Governed knowledge parsing, structure-aware chunking, and approval service."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol, TypedDict
from uuid import uuid4

from yield_rca_core.knowledge_models import (
    KnowledgeCandidateStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentType,
    KnowledgeIngestionApproval,
    KnowledgeIngestionCandidate,
    KnowledgeSourceFormat,
    KnowledgeValidationStatus,
)
from yield_rca_core.memory_models import ApprovalDecision


class KnowledgeIngestionError(ValueError):
    """Stable error with a machine-readable API code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class KnowledgeCandidateNotFoundError(KnowledgeIngestionError):
    def __init__(self, candidate_id: str) -> None:
        super().__init__("KNOWLEDGE_CANDIDATE_NOT_FOUND", candidate_id)


class KnowledgeIngestionConflictError(KnowledgeIngestionError):
    pass


@dataclass(frozen=True)
class KnowledgeIngestionLimits:
    max_file_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 100
    max_extracted_characters: int = 500_000
    max_chunks: int = 200
    target_chunk_characters: int = 1_600
    chunk_overlap_characters: int = 160


@dataclass(frozen=True)
class ParsedKnowledgeFile:
    filename: str
    source_format: str
    content: str
    content_sha256: str


class ExtractedKnowledgeMetadata(TypedDict):
    module: str
    equipment_type: str
    operation: str
    defect_type: str
    tags: tuple[str, ...]
    equipment_ids: tuple[str, ...]


class KnowledgeMetadataExtractor:
    """Deterministically enrich explicit metadata without inventing conclusions."""

    _equipment_pattern = re.compile(
        r"\b(?:CMP|ETCH|LITHO|CVD|PVD|DIFF|WET|IMP)_[A-Z0-9_]+\b",
        re.IGNORECASE,
    )
    _defect_terms = (
        "scratch",
        "residue",
        "particle",
        "bridge",
        "open",
        "short",
        "thickness",
        "leakage",
        "划伤",
        "刮伤",
        "残留",
        "颗粒",
    )

    def extract(
        self,
        *,
        content: str,
        module: str,
        equipment_type: str,
        operation: str,
        defect_type: str,
        tags: tuple[str, ...],
    ) -> ExtractedKnowledgeMetadata:
        searchable = f"{module}\n{content}"
        normalized_equipment = equipment_type.strip()
        equipment_ids = tuple(
            dict.fromkeys(
                match.group(0).upper() for match in self._equipment_pattern.finditer(content)
            )
        )
        if not normalized_equipment:
            lowered = searchable.casefold()
            normalized_equipment = next(
                (
                    label
                    for token, label in (
                        ("cmp", "CMP"),
                        ("etch", "ETCH"),
                        ("litho", "LITHO"),
                        ("scanner", "SCANNER"),
                        ("cvd", "CVD"),
                        ("pvd", "PVD"),
                    )
                    if token in lowered
                ),
                "",
            )
        normalized_defect = defect_type.strip()
        matched_defects = tuple(
            term for term in self._defect_terms if term.casefold() in content.casefold()
        )
        if not normalized_defect and matched_defects:
            normalized_defect = matched_defects[0]
        normalized_operation = operation.strip()
        if not normalized_operation and module.strip():
            normalized_operation = f"{module.strip()} operation"
        enriched_tags = tuple(
            dict.fromkeys(
                item.strip() for item in (*tags, *matched_defects, *equipment_ids) if item.strip()
            )
        )
        return {
            "module": module.strip(),
            "equipment_type": normalized_equipment,
            "operation": normalized_operation,
            "defect_type": normalized_defect,
            "tags": enriched_tags,
            "equipment_ids": equipment_ids,
        }


class KnowledgeDocumentParser:
    """Parse text-bearing files without persisting the uploaded binary."""

    def __init__(self, limits: KnowledgeIngestionLimits | None = None) -> None:
        self.limits = limits or KnowledgeIngestionLimits()

    def parse(
        self,
        *,
        filename: str,
        content_type: str | None,
        payload: bytes,
    ) -> ParsedKnowledgeFile:
        clean_filename = Path(filename).name.strip()
        if not clean_filename:
            raise KnowledgeIngestionError("INVALID_FILENAME", "filename is required")
        if not payload:
            raise KnowledgeIngestionError("EMPTY_DOCUMENT", "uploaded document is empty")
        if len(payload) > self.limits.max_file_bytes:
            raise KnowledgeIngestionError(
                "FILE_TOO_LARGE",
                f"document exceeds {self.limits.max_file_bytes} bytes",
            )

        extension = Path(clean_filename).suffix.casefold()
        normalized_type = (content_type or "").split(";", 1)[0].strip().casefold()
        if extension == ".pdf":
            if normalized_type and normalized_type not in {
                "application/pdf",
                "application/octet-stream",
            }:
                raise KnowledgeIngestionError(
                    "CONTENT_TYPE_MISMATCH", "PDF filename requires application/pdf"
                )
            content = self._parse_pdf(payload)
            source_format = KnowledgeSourceFormat.PDF.value
        elif extension in {".md", ".markdown"}:
            self._validate_text_content_type(normalized_type)
            content = self._decode_utf8(payload)
            source_format = KnowledgeSourceFormat.MARKDOWN.value
        elif extension == ".txt":
            self._validate_text_content_type(normalized_type)
            content = self._decode_utf8(payload)
            source_format = KnowledgeSourceFormat.TEXT.value
        else:
            raise KnowledgeIngestionError(
                "UNSUPPORTED_FILE_TYPE",
                "only text PDF, Markdown, and TXT are supported",
            )

        content = content.replace("\x00", "").strip()
        if not content:
            raise KnowledgeIngestionError("EMPTY_EXTRACTED_TEXT", "document has no usable text")
        if len(content) > self.limits.max_extracted_characters:
            raise KnowledgeIngestionError(
                "EXTRACTED_TEXT_TOO_LARGE",
                "extracted document text exceeds the configured limit",
            )
        return ParsedKnowledgeFile(
            filename=clean_filename,
            source_format=source_format,
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _validate_text_content_type(content_type: str) -> None:
        if content_type and content_type not in {
            "text/plain",
            "text/markdown",
            "application/octet-stream",
        }:
            raise KnowledgeIngestionError(
                "CONTENT_TYPE_MISMATCH", "text filename requires a text content type"
            )

    @staticmethod
    def _decode_utf8(payload: bytes) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KnowledgeIngestionError(
                "INVALID_TEXT_ENCODING", "Markdown and TXT must use UTF-8"
            ) from exc

    def _parse_pdf(self, payload: bytes) -> str:
        if not payload.startswith(b"%PDF-"):
            raise KnowledgeIngestionError("INVALID_PDF", "invalid PDF magic bytes")
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - packaging contract covers this
            raise KnowledgeIngestionError(
                "PDF_PARSER_UNAVAILABLE", "pypdf is not installed"
            ) from exc
        try:
            reader = PdfReader(BytesIO(payload))
            if reader.is_encrypted:
                raise KnowledgeIngestionError(
                    "ENCRYPTED_PDF_UNSUPPORTED", "encrypted PDF is not supported"
                )
            if len(reader.pages) > self.limits.max_pdf_pages:
                raise KnowledgeIngestionError(
                    "PDF_PAGE_LIMIT_EXCEEDED",
                    f"PDF exceeds {self.limits.max_pdf_pages} pages",
                )
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except KnowledgeIngestionError:
            raise
        except Exception as exc:
            raise KnowledgeIngestionError("INVALID_PDF", "PDF parsing failed") from exc
        if not text.strip():
            raise KnowledgeIngestionError(
                "OCR_NOT_SUPPORTED",
                "the PDF has no extractable text; scanned PDF/OCR is not supported",
            )
        return text


_HEADING_PATTERN = re.compile(r"(?m)^(#{1,6})\s+(.+?)\s*$")
_SOP_STEP_PATTERN = re.compile(
    r"(?m)^(?:step\s+\d+|\d+[.)]|[一二三四五六七八九十]+[、.])\s*.+$",
    re.IGNORECASE,
)
_WORD_PATTERN = re.compile(r"[A-Za-z0-9_+-]+|[\u3400-\u9fff]")


class KnowledgeChunker:
    """Create deterministic chunks while preserving semantic section labels."""

    def __init__(self, limits: KnowledgeIngestionLimits | None = None) -> None:
        self.limits = limits or KnowledgeIngestionLimits()

    def chunk_candidate(
        self,
        *,
        candidate_id: str,
        document_type: str,
        content: str,
        metadata: dict[str, object],
    ) -> tuple[KnowledgeChunk, ...]:
        sections = self._sections(document_type, content)
        raw_chunks: list[tuple[str, str, str]] = []
        for section_type, heading, section_content in sections:
            raw_chunks.extend(
                (section_type, heading, item)
                for item in self._split_text(section_content)
                if item.strip()
            )
        if len(raw_chunks) > self.limits.max_chunks:
            raise KnowledgeIngestionError(
                "CHUNK_LIMIT_EXCEEDED",
                f"document produces more than {self.limits.max_chunks} chunks",
            )
        return tuple(
            KnowledgeChunk(
                chunk_id=f"STG_CHK_{candidate_id.removeprefix('KING_')}_{index:04d}",
                candidate_id=candidate_id,
                chunk_index=index,
                section_type=section_type,
                heading=heading,
                content=chunk_content,
                token_count=max(1, len(_WORD_PATTERN.findall(chunk_content))),
                metadata={**metadata, "section_type": section_type, "heading": heading},
                validation_status=KnowledgeValidationStatus.STAGED.value,
            )
            for index, (section_type, heading, chunk_content) in enumerate(raw_chunks)
        )

    def chunk_document(self, document: KnowledgeDocument) -> tuple[KnowledgeChunk, ...]:
        candidate_chunks = self.chunk_candidate(
            candidate_id=f"KING_{document.document_id}",
            document_type=document.document_type,
            content=document.content,
            metadata={
                "document_type": document.document_type,
                "module": document.module,
                "equipment_type": document.equipment_type,
                "operation": document.operation,
                "defect_type": document.defect_type,
                "tags": list(document.tags),
                "case_id": document.case_id,
            },
        )
        return tuple(
            replace(
                item,
                chunk_id=f"CHK_{document.document_id}_{item.chunk_index:04d}",
                candidate_id=None,
                document_id=document.document_id,
                validation_status=KnowledgeValidationStatus.CONFIRMED.value,
            )
            for item in candidate_chunks
        )

    def _sections(self, document_type: str, content: str) -> list[tuple[str, str, str]]:
        markdown_sections = self._markdown_sections(content)
        if len(markdown_sections) > 1:
            return [
                (self._section_type(document_type, heading), heading, body)
                for heading, body in markdown_sections
            ]
        if document_type == KnowledgeDocumentType.SOP.value:
            matches = list(_SOP_STEP_PATTERN.finditer(content))
            if matches:
                sections: list[tuple[str, str, str]] = []
                prefix = content[: matches[0].start()].strip()
                if prefix:
                    sections.append(("purpose_and_scope", "Purpose and scope", prefix))
                for index, match in enumerate(matches):
                    end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
                    body = content[match.start() : end].strip()
                    sections.append(("procedure_step", match.group(0).strip(), body))
                return sections
        if document_type == KnowledgeDocumentType.RCA_CASE.value:
            return self._labelled_rca_sections(content)
        return [(self._section_type(document_type, ""), "", content)]

    @staticmethod
    def _markdown_sections(content: str) -> list[tuple[str, str]]:
        matches = list(_HEADING_PATTERN.finditer(content))
        if not matches:
            return [("", content)]
        sections: list[tuple[str, str]] = []
        prefix = content[: matches[0].start()].strip()
        if prefix:
            sections.append(("Introduction", prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = content[match.end() : end].strip()
            if body:
                sections.append((match.group(2).strip(), body))
        return sections or [("", content)]

    @staticmethod
    def _labelled_rca_sections(content: str) -> list[tuple[str, str, str]]:
        labels = [
            ("symptom", r"(?:observed symptom|symptom|现象|症状)\s*[:：]"),
            ("root_cause", r"(?:root cause|根因|原因)\s*[:：]"),
            ("corrective_action", r"(?:corrective actions?|solution|处置|措施)\s*[:：]"),
            ("engineering_boundary", r"(?:engineering boundary|边界|注意事项)\s*[:：]"),
        ]
        matches: list[tuple[int, int, str]] = []
        for section_type, pattern in labels:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                matches.append((match.start(), match.end(), section_type))
        matches.sort()
        if not matches:
            return [("rca_case", "", content)]
        result: list[tuple[str, str, str]] = []
        prefix = content[: matches[0][0]].strip()
        if prefix:
            result.append(("case_summary", "Case summary", prefix))
        for index, (_, body_start, section_type) in enumerate(matches):
            body_end = matches[index + 1][0] if index + 1 < len(matches) else len(content)
            body = content[body_start:body_end].strip(" .;\n")
            if body:
                result.append((section_type, section_type.replace("_", " ").title(), body))
        return result

    @staticmethod
    def _section_type(document_type: str, heading: str) -> str:
        normalized = heading.casefold()
        if document_type == KnowledgeDocumentType.SOP.value:
            return (
                "procedure_step"
                if any(term in normalized for term in ("step", "步骤", "procedure"))
                else "procedure_context"
            )
        if document_type == KnowledgeDocumentType.ENGINEERING_NOTE.value:
            return "engineering_interpretation"
        if "root cause" in normalized or "根因" in normalized:
            return "root_cause"
        if "solution" in normalized or "corrective" in normalized or "措施" in normalized:
            return "corrective_action"
        return "rca_case"

    def _split_text(self, content: str) -> list[str]:
        target = self.limits.target_chunk_characters
        overlap = self.limits.chunk_overlap_characters
        if len(content) <= target:
            return [content.strip()]
        chunks: list[str] = []
        start = 0
        while start < len(content):
            tentative_end = min(len(content), start + target)
            end = tentative_end
            if tentative_end < len(content):
                boundary = max(
                    content.rfind("\n", start + target // 2, tentative_end),
                    content.rfind("。", start + target // 2, tentative_end),
                    content.rfind(". ", start + target // 2, tentative_end),
                )
                if boundary > start:
                    end = boundary + 1
            chunk = content[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(content):
                break
            start = max(start + 1, end - overlap)
        return chunks


class KnowledgeStore(Protocol):
    def check_ready(self) -> None: ...

    def case_exists(self, case_id: str) -> bool: ...

    def create_candidate(
        self, candidate: KnowledgeIngestionCandidate
    ) -> KnowledgeIngestionCandidate: ...

    def get_candidate(self, candidate_id: str) -> KnowledgeIngestionCandidate | None: ...

    def list_candidates(self, status: str | None = None) -> list[KnowledgeIngestionCandidate]: ...

    def commit_approval(
        self, approval: KnowledgeIngestionApproval
    ) -> KnowledgeIngestionCandidate: ...

    def active_documents(self) -> list[KnowledgeDocument]: ...

    def active_chunks(self) -> list[KnowledgeChunk]: ...


class KnowledgeIngestionService:
    """Application service that keeps parsing separate from publication."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        parser: KnowledgeDocumentParser | None = None,
        chunker: KnowledgeChunker | None = None,
        metadata_extractor: KnowledgeMetadataExtractor | None = None,
    ) -> None:
        self.store = store
        self.parser = parser or KnowledgeDocumentParser()
        self.chunker = chunker or KnowledgeChunker(self.parser.limits)
        self.metadata_extractor = metadata_extractor or KnowledgeMetadataExtractor()

    def ingest(
        self,
        *,
        filename: str,
        content_type: str | None,
        payload: bytes,
        document_type: str,
        title: str,
        module: str,
        equipment_type: str = "",
        operation: str = "",
        defect_type: str = "",
        tags: tuple[str, ...] = (),
        case_id: str | None = None,
    ) -> KnowledgeIngestionCandidate:
        normalized_title = title.strip()
        normalized_module = module.strip()
        if not normalized_title:
            raise KnowledgeIngestionError("TITLE_REQUIRED", "knowledge title is required")
        if not normalized_module:
            raise KnowledgeIngestionError("MODULE_REQUIRED", "knowledge module is required")
        try:
            normalized_type = KnowledgeDocumentType(document_type).value
        except ValueError as exc:
            raise KnowledgeIngestionError(
                "INVALID_DOCUMENT_TYPE", "unsupported knowledge document type"
            ) from exc
        normalized_case_id = case_id.strip().upper() if case_id else None
        if normalized_type == KnowledgeDocumentType.RCA_CASE.value:
            if not normalized_case_id:
                raise KnowledgeIngestionError(
                    "CASE_ID_REQUIRED", "RCA_CASE ingestion requires an existing case_id"
                )
            if not self.store.case_exists(normalized_case_id):
                raise KnowledgeIngestionError(
                    "CASE_NOT_FOUND", f"RCA case does not exist: {normalized_case_id}"
                )

        parsed = self.parser.parse(
            filename=filename,
            content_type=content_type,
            payload=payload,
        )
        candidate_id = f"KING_{uuid4().hex.upper()}"
        extracted = self.metadata_extractor.extract(
            content=parsed.content,
            module=normalized_module,
            equipment_type=equipment_type,
            operation=operation,
            defect_type=defect_type,
            tags=tags,
        )
        extracted_tags = tuple(str(item) for item in extracted["tags"])
        metadata: dict[str, object] = {
            "document_type": normalized_type,
            **extracted,
            "tags": list(extracted_tags),
            "case_id": normalized_case_id,
        }
        chunks = self.chunker.chunk_candidate(
            candidate_id=candidate_id,
            document_type=normalized_type,
            content=parsed.content,
            metadata=metadata,
        )
        candidate = KnowledgeIngestionCandidate(
            candidate_id=candidate_id,
            filename=parsed.filename,
            source_format=parsed.source_format,
            document_type=normalized_type,
            case_id=normalized_case_id,
            title=normalized_title,
            parsed_content=parsed.content,
            content_sha256=parsed.content_sha256,
            module=normalized_module,
            equipment_type=str(extracted["equipment_type"]),
            operation=str(extracted["operation"]),
            defect_type=str(extracted["defect_type"]),
            tags=extracted_tags,
            chunks=chunks,
        )
        return self.store.create_candidate(candidate)

    def get(self, candidate_id: str) -> KnowledgeIngestionCandidate:
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None:
            raise KnowledgeCandidateNotFoundError(candidate_id)
        return candidate

    def list(self, status: str | None = None) -> list[KnowledgeIngestionCandidate]:
        if status is not None:
            try:
                status = KnowledgeCandidateStatus(status).value
            except ValueError as exc:
                raise KnowledgeIngestionError(
                    "INVALID_CANDIDATE_STATUS", "unsupported candidate status"
                ) from exc
        return self.store.list_candidates(status)

    def decide(
        self,
        *,
        candidate_id: str,
        engineer_id: str,
        engineer_role: str,
        decision: str,
        comment: str = "",
    ) -> KnowledgeIngestionCandidate:
        if self.store.get_candidate(candidate_id) is None:
            raise KnowledgeCandidateNotFoundError(candidate_id)
        approval = KnowledgeIngestionApproval(
            approval_id=f"KAPP_{uuid4().hex.upper()}",
            candidate_id=candidate_id,
            engineer_id=engineer_id.strip().upper(),
            engineer_role=engineer_role,
            decision=ApprovalDecision(decision).value,
            comment=comment.strip(),
        )
        return self.store.commit_approval(approval)
