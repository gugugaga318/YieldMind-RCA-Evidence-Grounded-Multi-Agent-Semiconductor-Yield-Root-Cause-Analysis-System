"""Read-only old implementation oracle from the exact approved commit."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "tests/fixtures/provider_abstraction/legacy_llm_gateway_8d9192f.py.fixture"
LEGACY_SHA256 = "b57c0fc053e0a1b0801723461ed253fbdadf5ec004da3d2947ae3bdf3141a939"


def load_legacy():
    assert hashlib.sha256(LEGACY_PATH.read_bytes()).hexdigest() == LEGACY_SHA256
    name = "yield_rca_core._test_legacy_gateway_8d9192f"
    if name not in sys.modules:
        loader = SourceFileLoader(name, str(LEGACY_PATH))
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
    return sys.modules[name]
