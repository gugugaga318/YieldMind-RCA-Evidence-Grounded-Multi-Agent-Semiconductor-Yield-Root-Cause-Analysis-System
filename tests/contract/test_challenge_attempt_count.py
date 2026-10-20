from __future__ import annotations

import socket
from dataclasses import asdict

import pytest
from challenge_attempt_count_support import before_patch2
from challenge_projection_support import ResponseTape, frozen, inputs
from yield_rca_core import causal_adversarial as current
from yield_rca_core.llm_gateway import LLMCallError, LLMOutputValidationError


class BudgetTape(ResponseTape):
    def __init__(self, responses, budget=None):
        super().__init__(responses)
        self.budget = budget

    @property
    def remaining_calls(self):
        return None if self.budget is None else self.budget - len(self.requests)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Attempt-count tests must not connect to a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def scenario(name):
    kwargs = inputs("qwen")
    valid = frozen("qwen")["validated_response"]
    budget = None
    if name == "preflight":
        kwargs["candidates"][0]["causal_explanation"] = "x" * 80000
        responses = []
        expected, old = 0, 2
    elif name == "repair_preflight":
        # Actual first client entry fails output decoding; its long diagnostic
        # makes the next payload fail the unchanged size gate before entry.
        responses = [LLMOutputValidationError("invalid JSON: " + "x" * 80000)]
        expected, old = 1, 2
    elif name == "success":
        responses, expected, old = [valid], 1, 1
    elif name == "repair_success":
        responses, expected, old = [{}, valid], 2, 2
    elif name == "exhausted":
        responses, expected, old = [{}, {}], 2, 2
    elif name in ("provider_first", "provider_repair"):
        error = LLMCallError("offline timeout", failure_category="timeout", call_attempt_count=2)
        responses = [error] if name == "provider_first" else [{}, error]
        expected = old = len(responses)
    elif name == "initial_budget":
        responses, expected, old, budget = [], 0, 0, 1
    elif name == "repair_budget":
        responses, expected, old, budget = [{}], 1, 1, 2
    elif name == "empty":
        kwargs["candidates"] = []
        kwargs["matrices"] = []
        responses, expected, old = [], 0, 0
    else:
        raise AssertionError(name)
    return kwargs, responses, budget, expected, old


@pytest.mark.parametrize(
    "name",
    [
        "preflight",
        "repair_preflight",
        "success",
        "repair_success",
        "exhausted",
        "provider_first",
        "provider_repair",
        "initial_budget",
        "repair_budget",
        "empty",
    ],
)
def test_attempt_count_matches_client_entries_and_preserves_all_other_results(name):
    kwargs, responses, budget, expected, previous = scenario(name)
    old_client = BudgetTape(responses, budget)
    new_client = BudgetTape(responses, budget)
    old = before_patch2().QwenAdversarialChallenger(old_client).generate(**kwargs)
    new = current.QwenAdversarialChallenger(new_client).generate(**kwargs)
    assert old.attempt_count == previous  # Executable reproduction, not assumed.
    assert len(old_client.requests) == len(new_client.requests) == expected
    assert new.attempt_count == len(new_client.requests)
    old_value, new_value = asdict(old), asdict(new)
    old_value.pop("attempt_count")
    new_value.pop("attempt_count")
    assert old_value == new_value
    assert [asdict(r) for r in old_client.requests] == [asdict(r) for r in new_client.requests]
    if name in ("preflight", "repair_preflight"):
        assert new.output_invalid
        assert (
            new.validation_errors[-1]
            == "adversarial challenge prompt exceeds the governed payload limit"
        )
    if name == "exhausted":
        assert new.output_invalid
    if name in ("initial_budget", "repair_budget"):
        assert new.repair_skipped_due_to_budget
    assert current._OUTPUT_ATTEMPTS == 2
    assert current._MAX_CHALLENGE_PAYLOAD_CHARS == 64000
