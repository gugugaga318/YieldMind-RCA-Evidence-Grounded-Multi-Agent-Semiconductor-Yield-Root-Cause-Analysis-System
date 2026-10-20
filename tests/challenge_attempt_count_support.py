"""Frozen Patch 1 implementation for attempt-count-only parity."""

import sys
import types
from pathlib import Path


def before_patch2():
    name = "frozen_challenge_patch1"
    if name not in sys.modules:
        module = types.ModuleType(name)
        sys.modules[name] = module
        path = (
            Path(__file__).parent
            / "fixtures/challenge_attempt_count/patch1_causal_adversarial.py.fixture"
        )
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return sys.modules[name]
