"""Seed PostgreSQL with the offline golden Synthetic Fab dataset."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED_DIR = ROOT / "data" / "seeds" / "golden_case"
UP_MIGRATIONS = [
    ROOT / "db" / "migrations" / "001_initial_schema.up.sql",
    ROOT / "db" / "migrations" / "002_observability_audit.up.sql",
    ROOT / "db" / "migrations" / "003_memory_approval.up.sql",
    ROOT / "db" / "migrations" / "004_advanced_spc_analytics.up.sql",
    ROOT / "db" / "migrations" / "005_runtime_resilience.up.sql",
    ROOT / "db" / "migrations" / "006_memory_snapshot_index_update.up.sql",
]
DOWN_MIGRATIONS = [
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


def normalize_value(table_name: str, column_name: str, value: str) -> Any:
    if value == "":
        return None
    if column_name in ARRAY_COLUMNS.get(table_name, set()):
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


def seed_database(database_url: str, seed_dir: Path, reset_schema: bool) -> None:
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
        connection.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed PostgreSQL with golden synthetic Fab data.")
    parser.add_argument("--database-url", default=os.environ.get("TEST_DATABASE_URL"))
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--reset-schema", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.database_url:
        raise SystemExit("database URL required: pass --database-url or set TEST_DATABASE_URL")
    seed_database(args.database_url, args.seed_dir, args.reset_schema)
    print(f"Seeded database from {args.seed_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
