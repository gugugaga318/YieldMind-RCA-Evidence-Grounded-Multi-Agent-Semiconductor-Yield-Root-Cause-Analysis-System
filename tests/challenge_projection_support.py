"""Offline-only frozen Challenge fixtures; no environment/provider configuration."""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

from yield_rca_core.causal_chain import CausalChainAssessment
from yield_rca_core.causal_evidence_matrix import CausalClaimResult, CausalEvidenceMatrix
from yield_rca_core.causal_hypothesis import CausalHypothesis
from yield_rca_core.evidence_models import Evidence
from yield_rca_core.llm_gateway import FakeLLMClient, LLMResponse

FIXTURES = Path(__file__).parent / "fixtures/challenge_projection"


def frozen(name):
    return json.loads((FIXTURES / f"formal012_{name}.json").read_text(encoding="utf-8"))


def inputs(name):
    value = copy.deepcopy(frozen(name)["inputs"])
    value["evidence_by_id"] = {
        key: Evidence.from_dict(raw) for key, raw in value["evidence_by_id"].items()
    }
    matrices = []
    for candidate, matrix in zip(value["candidates"], value["matrices"], strict=True):
        matrices.append(
            CausalEvidenceMatrix(
                candidate=CausalHypothesis.from_mapping(candidate),
                claims={key: CausalClaimResult(**raw) for key, raw in matrix["claims"].items()},
                invalid_evidence_ids=tuple(matrix["invalid_evidence_ids"]),
                data_missing_evidence_ids=tuple(matrix["data_missing_evidence_ids"]),
                data_missing_sources=tuple(matrix["data_missing_sources"]),
                causal_chain=CausalChainAssessment(**matrix["causal_chain"]),
            )
        )
    value["matrices"] = matrices
    return value


def legacy():
    name = "frozen_challenge_before_projection"
    if name not in sys.modules:
        module = types.ModuleType(name)
        sys.modules[name] = module
        source = FIXTURES / "legacy_causal_adversarial.py.fixture"
        exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), module.__dict__)
    return sys.modules[name]


class ResponseTape(FakeLLMClient):
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        base = super().complete_json(request)
        return LLMResponse(data=response, usage=base.usage)
