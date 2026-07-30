"""Repository quality commands for the Python core baseline.

This module intentionally uses only the Python standard library so Step 1 can
run before project dependencies are installed.
"""

from __future__ import annotations

import argparse
import compileall
import json
import platform
import subprocess
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKED_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".json"}
SKIPPED_DIRS = {
    ".git",
    ".agents",
    ".codex",
    ".mypy_cache",
    ".pytest_cache",
    ".pnpm-store",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "outputs",
    "node_modules",
    "dist",
    "work",
}


def iter_checked_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in CHECKED_SUFFIXES:
            files.append(path)
    return sorted(files)


def cmd_lint() -> int:
    failures: list[str] = []
    for path in iter_checked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            failures.append(f"{path}: not valid UTF-8")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            markdown_hard_break = path.suffix.lower() == ".md" and line.endswith("  ")
            if line.rstrip(" \t") != line and not markdown_hard_break:
                failures.append(f"{path}:{lineno}: trailing whitespace")
            if "\t" in line:
                failures.append(f"{path}:{lineno}: tab character")
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(f"lint ok: checked {len(iter_checked_files())} files")
    return 0


def cmd_type_check() -> int:
    ok = compileall.compile_dir(ROOT / "core", quiet=1)
    ok = compileall.compile_dir(ROOT / "backend", quiet=1) and ok
    ok = compileall.compile_dir(ROOT / "tests", quiet=1) and ok
    ok = compileall.compile_dir(ROOT / "tools", quiet=1) and ok
    if not ok:
        return 1
    print("type-check baseline ok: Python files compile")
    return 0


def run_unittest_suite(name: str) -> int:
    test_dir = ROOT / "tests" / name
    suite = unittest.defaultTestLoader.discover(str(test_dir), pattern="test*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def cmd_benchmark() -> int:
    start = time.perf_counter()
    subprocess.run(
        [sys.executable, "-c", "import sys; import pathlib; pathlib.Path('.')"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    payload = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "elapsed_ms": round(elapsed_ms, 3),
        "benchmark": "baseline-subprocess-startup",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_test_all() -> int:
    commands = [
        ("lint", cmd_lint),
        ("type-check", cmd_type_check),
        ("unit", lambda: run_unittest_suite("unit")),
        ("contract", lambda: run_unittest_suite("contract")),
        ("integration", lambda: run_unittest_suite("integration")),
        ("performance", lambda: run_unittest_suite("performance")),
        ("benchmark", cmd_benchmark),
    ]
    for name, command in commands:
        print(f"== {name} ==")
        exit_code = command()
        if exit_code != 0:
            return exit_code
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repository quality commands.")
    parser.add_argument(
        "command",
        choices=[
            "lint",
            "type-check",
            "unit",
            "contract",
            "integration",
            "performance",
            "benchmark",
            "test-all",
        ],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "lint":
        return cmd_lint()
    if args.command == "type-check":
        return cmd_type_check()
    if args.command == "unit":
        return run_unittest_suite("unit")
    if args.command == "contract":
        return run_unittest_suite("contract")
    if args.command == "integration":
        return run_unittest_suite("integration")
    if args.command == "performance":
        return run_unittest_suite("performance")
    if args.command == "benchmark":
        return cmd_benchmark()
    if args.command == "test-all":
        return cmd_test_all()
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
