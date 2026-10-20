import sys
import types
from pathlib import Path


def before_observability():
    name = "frozen_challenge_pre_observability"
    if name not in sys.modules:
        module = types.ModuleType(name)
        sys.modules[name] = module
        path = Path(__file__).parent / "fixtures/challenge_diagnostics/pre_observability.py.fixture"
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return sys.modules[name]
