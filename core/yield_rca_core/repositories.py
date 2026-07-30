"""Repository interfaces and seed-backed implementations for Fab data access.

Repositories are data access boundaries. Agents must not depend on this module
directly; they should call tools, and tools may depend on repository protocols.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Protocol

Row = dict[str, str]
SUPPORTED_TABLES = {
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
    "ooc_event",
    "defect_summary",
    "metrology_result",
    "wat_result",
    "rca_case",
    "knowledge_document",
    "spc_baseline_profile",
    "spc_excursion",
    "spc_excursion_lot",
}
OPTIONAL_TABLES = {
    "metrology_result",
    "spc_baseline_profile",
    "spc_excursion",
    "spc_excursion_lot",
}


class FabRepository(Protocol):
    """Read-only repository contract used by the Tool Layer."""

    def rows(self, table_name: str) -> list[Row]:
        """Return rows for a supported table."""


class CsvFabRepository:
    """Read-only repository backed by the offline golden seed CSV files."""

    def __init__(self, seed_dir: Path) -> None:
        self.seed_dir = seed_dir
        self._cache: dict[str, list[Row]] = {}

    def rows(self, table_name: str) -> list[Row]:
        _validate_table_name(table_name)
        if table_name not in self._cache:
            path = self.seed_dir / f"{table_name}.csv"
            if not path.exists():
                if table_name in OPTIONAL_TABLES:
                    self._cache[table_name] = []
                    return []
                raise FileNotFoundError(f"missing seed table: {path}")
            with path.open(newline="", encoding="utf-8") as handle:
                self._cache[table_name] = list(csv.DictReader(handle))
        return [dict(row) for row in self._cache[table_name]]


class PostgresFabRepository:
    """Read-only PostgreSQL repository for the same table contract."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def rows(self, table_name: str) -> list[Row]:
        _validate_table_name(table_name)
        import psycopg

        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {table_name}")
                columns = [item.name for item in cursor.description or []]
                result: list[Row] = []
                for row in cursor.fetchall():
                    result.append(
                        {
                            column: _stringify_database_value(value)
                            for column, value in zip(columns, row, strict=True)
                        }
                    )
                return result


def _validate_table_name(table_name: str) -> None:
    if table_name not in SUPPORTED_TABLES:
        raise ValueError(f"unsupported table: {table_name}")


def _stringify_database_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return str(value)


def filter_rows(
    rows: list[Row], **criteria: str | set[str] | list[str] | tuple[str, ...]
) -> list[Row]:
    """Filter rows by exact match criteria."""

    filtered = rows
    for column, expected in criteria.items():
        if isinstance(expected, set | list | tuple):
            expected_values = {str(item) for item in expected}
            filtered = [row for row in filtered if row.get(column, "") in expected_values]
        else:
            filtered = [row for row in filtered if row.get(column, "") == str(expected)]
    return filtered
