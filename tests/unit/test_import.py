from __future__ import annotations

import unittest


class ImportTest(unittest.TestCase):
    def test_core_package_imports(self) -> None:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "core"))

        import yield_rca_core

        self.assertEqual(yield_rca_core.__version__, "0.1.0")


if __name__ == "__main__":
    unittest.main()

