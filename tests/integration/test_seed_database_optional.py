from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL") and importlib.util.find_spec("psycopg"),
    "set TEST_DATABASE_URL and install psycopg to run real PostgreSQL seed tests",
)
class OptionalSeedDatabaseTest(unittest.TestCase):
    def test_seed_database_imports_golden_dataset(self) -> None:
        import psycopg  # type: ignore[import-not-found]

        database_url = os.environ["TEST_DATABASE_URL"]
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_synthetic_fab_data.py"),
                "--output-dir",
                str(SEED_DIR),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "seed_database.py"),
                "--database-url",
                database_url,
                "--seed-dir",
                str(SEED_DIR),
                "--reset-schema",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        with psycopg.connect(database_url, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM lot_master WHERE lot_id LIKE 'LOT_A_%'")
                self.assertEqual(cursor.fetchone()[0], 20)
                cursor.execute("SELECT count(*) FROM lot_master WHERE lot_id LIKE 'LOT_N_%'")
                self.assertEqual(cursor.fetchone()[0], 30)
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM process_history
                    WHERE lot_id LIKE 'LOT_A_%'
                      AND operation_no = '6400'
                      AND equipment_id = 'CMP_CU03'
                      AND chamber_id = 'CMP_CU03_CH02'
                    """
                )
                self.assertEqual(cursor.fetchone()[0], 20)
                cursor.execute(
                    """
                    SELECT count(*)
                    FROM process_history ph
                    JOIN equipment_master em ON em.equipment_id = ph.equipment_id
                    WHERE ph.operation_no = '6400'
                      AND (em.module <> 'Cu CMP' OR em.material <> 'Copper')
                    """
                )
                self.assertEqual(cursor.fetchone()[0], 0)
                cursor.execute("SELECT count(*) FROM hold_history WHERE hold_comment ILIKE '%slurry%'")
                self.assertEqual(cursor.fetchone()[0], 20)


if __name__ == "__main__":
    unittest.main()
