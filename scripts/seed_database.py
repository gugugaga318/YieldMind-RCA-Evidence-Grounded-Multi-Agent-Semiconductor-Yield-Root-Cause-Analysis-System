"""Seed PostgreSQL with the offline golden Synthetic Fab dataset."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.knowledge_ingestion import KnowledgeChunker  # noqa: E402
from yield_rca_core.knowledge_models import KnowledgeDocument  # noqa: E402

DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
DEFAULT_KNOWLEDGE_CORPUS = ROOT / "data" / "knowledge" / "synthetic_v1" / "corpus.json"
UP_MIGRATIONS = [
    ROOT / "db" / "migrations" / "001_initial_schema.up.sql",
    ROOT / "db" / "migrations" / "002_observability_audit.up.sql",
    ROOT / "db" / "migrations" / "003_memory_approval.up.sql",
    ROOT / "db" / "migrations" / "004_advanced_spc_analytics.up.sql",
    ROOT / "db" / "migrations" / "005_runtime_resilience.up.sql",
    ROOT / "db" / "migrations" / "006_memory_snapshot_index_update.up.sql",
    ROOT / "db" / "migrations" / "007_knowledge_ingestion.up.sql",
    ROOT / "db" / "migrations" / "008_hybrid_retrieval.up.sql",
    ROOT / "db" / "migrations" / "009_pgvector_knowledge_index.up.sql",
]
DOWN_MIGRATIONS = [
    ROOT / "db" / "migrations" / "009_pgvector_knowledge_index.down.sql",
    ROOT / "db" / "migrations" / "008_hybrid_retrieval.down.sql",
    ROOT / "db" / "migrations" / "007_knowledge_ingestion.down.sql",
    ROOT / "db" / "migrations" / "006_memory_snapshot_index_update.down.sql",
    ROOT / "db" / "migrations" / "005_runtime_resilience.down.sql",
    ROOT / "db" / "migrations" / "004_advanced_spc_analytics.down.sql",
    ROOT / "db" / "migrations" / "003_memory_approval.down.sql",
    ROOT / "db" / "migrations" / "002_observability_audit.down.sql",
    ROOT / "db" / "migrations" / "001_initial_schema.down.sql",
]

TABLE_ORDER = [
    "lot_master",
    "wafer_master",
    "operation_master",
    "process_route",
    "equipment_master",
    "chamber_master",
    "equipment_capability",
    "recipe_master",
    "recipe_history",
    "process_history",
    "hold_history",
    "fdc_feature",
    "spc_baseline_profile",
    "spc_excursion",
    "ooc_event",
    "spc_excursion_lot",
    "defect_summary",
    "metrology_result",
    "wat_result",
    "rca_case",
    "knowledge_document",
]

ARRAY_COLUMNS = {
    "knowledge_document": {"tags"},
    "ooc_event": {"spc_rule_codes"},
}
OPTIONAL_TABLES = {
    "metrology_result",
    "spc_baseline_profile",
    "spc_excursion",
    "spc_excursion_lot",
}


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def normalize_value(table_name: str, column_name: str, value: Any) -> Any:
    if value == "":
        return None
    if column_name in ARRAY_COLUMNS.get(table_name, set()):
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(";") if item.strip()]
    return value


def insert_rows(connection: Any, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    sql = f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})"
    values = [
        tuple(normalize_value(table_name, column, row[column]) for column in columns)
        for row in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)


def apply_sql(connection: Any, sql_path: Path) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql_path.read_text(encoding="utf-8"))


def seed_builtin_knowledge(connection: Any, corpus_path: Path) -> None:
    """Publish only explicitly confirmed built-in Synthetic assets."""

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    case_path = corpus_path.parent / "rca_case.csv"
    cases = [
        row
        for row in load_csv(case_path)
        if row.get("validation_status") == "CONFIRMED"
    ]
    insert_rows(connection, "rca_case", cases)

    documents: list[dict[str, Any]] = []
    for item in corpus.get("documents", []):
        if item.get("validation_status") != "CONFIRMED":
            continue
        content = str(item["content"])
        documents.append(
            {
                "document_id": item["document_id"],
                "case_id": item.get("case_id"),
                "document_type": item["document_type"],
                "title": item["title"],
                "content": content,
                "tags": item.get("tags", []),
                "created_at": item.get("created_at", "2026-08-08T00:00:00+08:00"),
                "validation_status": "CONFIRMED",
                "module": item.get("module", ""),
                "equipment_type": item.get("equipment_type", ""),
                "operation": item.get("operation", ""),
                "defect_type": item.get("defect_type", ""),
                "source_format": "synthetic",
                "content_sha256": item.get("content_hash")
                or sha256(content.encode("utf-8")).hexdigest(),
                "publication_policy": "BUILTIN_SYNTHETIC_SEED",
            }
        )
    insert_rows(connection, "knowledge_document", documents)


def backfill_active_knowledge_chunks(connection: Any) -> None:
    """Create Active Index chunks for legacy, Synthetic, and approved Memory documents."""

    chunker = KnowledgeChunker()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT kd.document_id, kd.case_id, kd.document_type, kd.title, kd.content,
                   kd.tags, kd.created_at, kd.validation_status,
                   COALESCE(NULLIF(kd.module, ''), rc.module, '') AS module,
                   COALESCE(NULLIF(kd.equipment_type, ''), rc.equipment_type, '')
                       AS equipment_type,
                   kd.operation, kd.defect_type, kd.source_format,
                   kd.content_sha256, kd.publication_policy,
                   kd.source_ingestion_candidate_id
            FROM knowledge_document kd
            LEFT JOIN rca_case rc ON rc.case_id = kd.case_id
            WHERE kd.validation_status = 'CONFIRMED'
            ORDER BY kd.document_id
            """
        )
        columns = [item.name for item in cursor.description or []]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        for row in rows:
            content = str(row["content"])
            content_hash = str(
                row["content_sha256"] or sha256(content.encode("utf-8")).hexdigest()
            )
            cursor.execute(
                """
                UPDATE knowledge_document
                SET module = %s, equipment_type = %s, content_sha256 = %s
                WHERE document_id = %s
                """,
                (row["module"], row["equipment_type"], content_hash, row["document_id"]),
            )
            document = KnowledgeDocument(
                document_id=str(row["document_id"]),
                case_id=str(row["case_id"]) if row["case_id"] else None,
                document_type=str(row["document_type"]),
                title=str(row["title"]),
                content=content,
                module=str(row["module"]),
                equipment_type=str(row["equipment_type"]),
                operation=str(row["operation"]),
                defect_type=str(row["defect_type"]),
                tags=tuple(str(item) for item in row["tags"] or []),
                source_format=str(row["source_format"]),
                content_sha256=content_hash,
                validation_status=str(row["validation_status"]),
                publication_policy=str(row["publication_policy"]),
                source_candidate_id=(
                    str(row["source_ingestion_candidate_id"])
                    if row["source_ingestion_candidate_id"]
                    else None
                ),
                created_at=str(row["created_at"]),
            )
            cursor.executemany(
                """
                INSERT INTO knowledge_chunk (
                    chunk_id, document_id, chunk_index, section_type, heading,
                    content, token_count, metadata, validation_status,
                    embedding_status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (document_id, chunk_index) DO NOTHING
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.chunk_index,
                        chunk.section_type,
                        chunk.heading,
                        chunk.content,
                        chunk.token_count,
                        json.dumps(chunk.metadata),
                        chunk.validation_status,
                        chunk.embedding_status,
                    )
                    for chunk in chunker.chunk_document(document)
                ],
            )


def seed_database(
    database_url: str,
    seed_dir: Path,
    reset_schema: bool,
    knowledge_corpus: Path | None = DEFAULT_KNOWLEDGE_CORPUS,
) -> None:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        if reset_schema:
            for migration in DOWN_MIGRATIONS:
                apply_sql(connection, migration)
            for migration in UP_MIGRATIONS:
                apply_sql(connection, migration)

        for table_name in TABLE_ORDER:
            csv_path = seed_dir / f"{table_name}.csv"
            if not csv_path.exists():
                if table_name in OPTIONAL_TABLES:
                    continue
                raise FileNotFoundError(f"missing seed file: {csv_path}")
            insert_rows(connection, table_name, load_csv(csv_path))
        if knowledge_corpus is not None:
            seed_builtin_knowledge(connection, knowledge_corpus)
        backfill_active_knowledge_chunks(connection)
        connection.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed PostgreSQL with golden synthetic Fab data.")
    parser.add_argument("--database-url", default=os.environ.get("TEST_DATABASE_URL"))
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument(
        "--knowledge-corpus",
        type=Path,
        default=DEFAULT_KNOWLEDGE_CORPUS,
        help="confirmed built-in Synthetic Knowledge corpus",
    )
    parser.add_argument("--skip-knowledge-corpus", action="store_true")
    parser.add_argument("--reset-schema", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.database_url:
        raise SystemExit("database URL required: pass --database-url or set TEST_DATABASE_URL")
    seed_database(
        args.database_url,
        args.seed_dir,
        args.reset_schema,
        None if args.skip_knowledge_corpus else args.knowledge_corpus,
    )
    print(f"Seeded database from {args.seed_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
