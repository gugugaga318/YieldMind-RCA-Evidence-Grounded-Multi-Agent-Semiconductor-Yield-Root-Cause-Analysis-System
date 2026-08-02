"""Python-owned Question capability and pre-dispatch Action gates.

The Qwen planner may choose the order of investigation, but it cannot declare
which Agent or Evidence type is capable of answering a Question.  This module
is the single deterministic registry for that boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from yield_rca_core.evidence_models import EvidenceType
from yield_rca_core.investigation_models import (
    ActionKind,
    CapabilityNotice,
    InvestigationAction,
    InvestigationQuestion,
    InvestigationValidationError,
    QuestionKind,
)


class QuestionCapabilityError(InvestigationValidationError):
    """A deterministic Question/Action capability gate rejection."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class QuestionCapabilityDefinition:
    """Static capability declaration owned by Python, never by Qwen."""

    question_kind: str
    supported: bool
    direct_actions: frozenset[str] = frozenset()
    supporting_actions: frozenset[str] = frozenset()
    accepted_evidence_types: frozenset[str] = frozenset()
    closure_evidence_groups: frozenset[str] = frozenset()
    action_contributions: Mapping[str, frozenset[str]] = field(default_factory=dict)
    unsupported_reason: str | None = None
    available_alternatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            normalized_kind = QuestionKind(self.question_kind).value
        except ValueError as exc:
            raise ValueError("question_kind is not a known QuestionKind") from exc
        object.__setattr__(self, "question_kind", normalized_kind)
        if not isinstance(self.supported, bool):
            raise ValueError("supported must be a boolean")
        actions = self.direct_actions | self.supporting_actions
        if not actions and self.supported:
            raise ValueError("a supported capability must expose an Action")
        if not self.supported and not self.unsupported_reason:
            raise ValueError("an unsupported capability requires unsupported_reason")
        if not isinstance(self.action_contributions, Mapping):
            raise ValueError("action_contributions must be a mapping")
        contributions = {
            str(action): frozenset(str(group) for group in groups)
            for action, groups in self.action_contributions.items()
        }
        if set(contributions) - actions:
            raise ValueError(
                "action_contributions may only declare direct or supporting Actions"
            )
        object.__setattr__(self, "action_contributions", MappingProxyType(contributions))
        object.__setattr__(self, "direct_actions", frozenset(self.direct_actions))
        object.__setattr__(self, "supporting_actions", frozenset(self.supporting_actions))
        object.__setattr__(
            self,
            "accepted_evidence_types",
            frozenset(self.accepted_evidence_types),
        )
        object.__setattr__(
            self,
            "closure_evidence_groups",
            frozenset(self.closure_evidence_groups),
        )
        object.__setattr__(self, "available_alternatives", tuple(self.available_alternatives))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return self.direct_actions | self.supporting_actions

    def contribution_for(self, action_kind: str) -> frozenset[str]:
        return self.action_contributions.get(action_kind, frozenset())


def _definition(
    kind: QuestionKind,
    *,
    direct: Sequence[ActionKind] = (),
    supporting: Sequence[ActionKind] = (),
    evidence: Sequence[EvidenceType | str] = (),
    closure: Sequence[str] = (),
    contributions: Mapping[ActionKind | str, Sequence[str]] | None = None,
    supported: bool = True,
    unsupported_reason: str | None = None,
    alternatives: Sequence[str] = (),
) -> QuestionCapabilityDefinition:
    direct_values = frozenset(
        item.value if isinstance(item, ActionKind) else str(item)
        for item in direct
    )
    supporting_values = frozenset(
        item.value if isinstance(item, ActionKind) else str(item) for item in supporting
    )
    normalized_contributions = {
        action.value if isinstance(action, ActionKind) else str(action): frozenset(groups)
        for action, groups in (contributions or {}).items()
    }
    return QuestionCapabilityDefinition(
        question_kind=kind.value,
        supported=supported,
        direct_actions=direct_values,
        supporting_actions=supporting_values,
        accepted_evidence_types=frozenset(
            item.value if isinstance(item, EvidenceType) else str(item) for item in evidence
        ),
        closure_evidence_groups=frozenset(closure),
        action_contributions=normalized_contributions,
        unsupported_reason=unsupported_reason,
        available_alternatives=tuple(alternatives),
    )


QUESTION_CAPABILITY_REGISTRY: Mapping[str, QuestionCapabilityDefinition] = MappingProxyType(
    {
        QuestionKind.DEFECT_SIGNATURE.value: _definition(
            QuestionKind.DEFECT_SIGNATURE,
            direct=(ActionKind.INSPECT_DEFECT_PATTERN, ActionKind.VALIDATE_SHARED_DEFECT_PATTERN),
            # Legacy product-window traces may collect shared exposure before
            # the defect inspection; this is contextual support only and can
            # never satisfy the product-signal closure group.
            supporting=(ActionKind.FIND_SHARED_EXPOSURE,),
            evidence=(
                EvidenceType.DEFECT_SIGNAL,
                EvidenceType.ELECTRICAL_FAILURE,
                EvidenceType.METROLOGY_DEVIATION,
                EvidenceType.NEGATIVE_SIGNAL,
            ),
            closure=("product_signal",),
            contributions={
                ActionKind.INSPECT_DEFECT_PATTERN: ("product_signal",),
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN: ("product_signal",),
                ActionKind.FIND_SHARED_EXPOSURE: ("context",),
            },
        ),
        QuestionKind.IMPACT_SCOPE.value: _definition(
            QuestionKind.IMPACT_SCOPE,
            direct=(ActionKind.FIND_SHARED_EXPOSURE,),
            supporting=(ActionKind.VALIDATE_SHARED_DEFECT_PATTERN,),
            evidence=(
                EvidenceType.IMPACT_SCOPE,
                EvidenceType.LOT_CONTEXT,
                EvidenceType.PROCESS_EXPOSURE,
                EvidenceType.EQUIPMENT_EXPOSURE,
                EvidenceType.EXCURSION_WINDOW,
            ),
            closure=("shared_exposure", "impact_scope"),
            contributions={
                ActionKind.FIND_SHARED_EXPOSURE: ("shared_exposure", "impact_scope"),
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN: ("shared_exposure",),
            },
        ),
        QuestionKind.SPC_SIGNAL.value: _definition(
            QuestionKind.SPC_SIGNAL,
            direct=(ActionKind.INSPECT_FDC_SPC,),
            supporting=(ActionKind.FIND_SHARED_EXPOSURE,),
            evidence=(
                EvidenceType.PARAMETER_DEVIATION,
                EvidenceType.TREND_DEVIATION,
                EvidenceType.SPC_VIOLATION,
                EvidenceType.OOC_EVENT,
                EvidenceType.EXCURSION_WINDOW,
            ),
            closure=("process_anomaly",),
            contributions={
                ActionKind.INSPECT_FDC_SPC: ("process_anomaly",),
                ActionKind.FIND_SHARED_EXPOSURE: ("shared_exposure",),
            },
        ),
        QuestionKind.PROCESS_MECHANISM.value: _definition(
            QuestionKind.PROCESS_MECHANISM,
            direct=(ActionKind.INSPECT_FDC_SPC, ActionKind.RUN_RCA_REASONING),
            supporting=(
                ActionKind.INSPECT_DEFECT_PATTERN,
                ActionKind.FIND_SHARED_EXPOSURE,
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN,
                ActionKind.VALIDATE_HISTORICAL_CASE,
            ),
            evidence=(
                EvidenceType.PARAMETER_DEVIATION,
                EvidenceType.TREND_DEVIATION,
                EvidenceType.SPC_VIOLATION,
                EvidenceType.OOC_EVENT,
                EvidenceType.EXCURSION_WINDOW,
                EvidenceType.DEFECT_SIGNAL,
                EvidenceType.METROLOGY_DEVIATION,
                EvidenceType.ELECTRICAL_FAILURE,
                EvidenceType.HISTORICAL_CASE_MATCH,
            ),
            closure=("process_anomaly", "product_signal", "shared_exposure"),
            contributions={
                ActionKind.INSPECT_DEFECT_PATTERN: ("product_signal",),
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN: ("product_signal", "shared_exposure"),
                ActionKind.FIND_SHARED_EXPOSURE: ("shared_exposure",),
                ActionKind.INSPECT_FDC_SPC: ("process_anomaly",),
                ActionKind.VALIDATE_HISTORICAL_CASE: ("historical_context",),
                ActionKind.RUN_RCA_REASONING: ("hypothesis_synthesis",),
            },
        ),
        QuestionKind.PRODUCT_OUTCOME.value: _definition(
            QuestionKind.PRODUCT_OUTCOME,
            direct=(ActionKind.INSPECT_DEFECT_PATTERN, ActionKind.VALIDATE_SHARED_DEFECT_PATTERN),
            supporting=(ActionKind.FIND_SHARED_EXPOSURE,),
            evidence=(
                EvidenceType.ELECTRICAL_FAILURE,
                EvidenceType.DEFECT_SIGNAL,
                EvidenceType.METROLOGY_DEVIATION,
                EvidenceType.NEGATIVE_SIGNAL,
            ),
            closure=("product_signal",),
            contributions={
                ActionKind.INSPECT_DEFECT_PATTERN: ("product_signal",),
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN: ("product_signal",),
            },
        ),
        QuestionKind.HISTORICAL_MATCH.value: _definition(
            QuestionKind.HISTORICAL_MATCH,
            direct=(ActionKind.VALIDATE_HISTORICAL_CASE,),
            supporting=(
                ActionKind.FIND_SHARED_EXPOSURE,
                ActionKind.INSPECT_FDC_SPC,
                ActionKind.INSPECT_DEFECT_PATTERN,
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN,
            ),
            evidence=(EvidenceType.HISTORICAL_CASE_MATCH,),
            closure=("historical_context",),
            contributions={ActionKind.VALIDATE_HISTORICAL_CASE: ("historical_context",)},
        ),
        QuestionKind.TOOL_HISTORY.value: _definition(
            QuestionKind.TOOL_HISTORY,
            direct=(ActionKind.FIND_SHARED_EXPOSURE,),
            supporting=(ActionKind.INSPECT_FDC_SPC,),
            evidence=(
                EvidenceType.LOT_CONTEXT,
                EvidenceType.PROCESS_EXPOSURE,
                EvidenceType.EQUIPMENT_EXPOSURE,
                EvidenceType.RECIPE_CHANGE,
                EvidenceType.HOLD_EVENT,
            ),
            closure=("lot_context", "process_exposure"),
            contributions={ActionKind.FIND_SHARED_EXPOSURE: ("lot_context", "process_exposure")},
        ),
        QuestionKind.RECIPE_HISTORY.value: _definition(
            QuestionKind.RECIPE_HISTORY,
            direct=(ActionKind.FIND_SHARED_EXPOSURE,),
            evidence=(EvidenceType.LOT_CONTEXT, EvidenceType.RECIPE_CHANGE),
            closure=("recipe_context",),
            contributions={ActionKind.FIND_SHARED_EXPOSURE: ("recipe_context",)},
        ),
        QuestionKind.METROLOGY_CORRELATION.value: _definition(
            QuestionKind.METROLOGY_CORRELATION,
            direct=(ActionKind.INSPECT_DEFECT_PATTERN,),
            supporting=(ActionKind.VALIDATE_SHARED_DEFECT_PATTERN,),
            evidence=(EvidenceType.METROLOGY_DEVIATION, EvidenceType.NEGATIVE_SIGNAL),
            closure=("metrology_signal",),
            contributions={
                ActionKind.INSPECT_DEFECT_PATTERN: ("metrology_signal",),
                ActionKind.VALIDATE_SHARED_DEFECT_PATTERN: ("metrology_signal",),
            },
        ),
        QuestionKind.MATERIAL_TRACE.value: _definition(
            QuestionKind.MATERIAL_TRACE,
            supported=False,
            closure=("material_trace",),
            unsupported_reason=(
                "Material, supplier, and consumable genealogy is not configured "
                "in the current RCA deployment."
            ),
            alternatives=("MES process exposure", "FDC process anomaly"),
        ),
        QuestionKind.UNSUPPORTED.value: _definition(
            QuestionKind.UNSUPPORTED,
            supported=False,
            closure=(),
            unsupported_reason="The legacy Question has no recognized capability kind.",
            alternatives=(),
        ),
    }
)


def capability_for_question(question: InvestigationQuestion) -> QuestionCapabilityDefinition:
    """Return the registered definition for a typed Question."""

    if not isinstance(question, InvestigationQuestion):
        raise TypeError("question must be an InvestigationQuestion")
    return QUESTION_CAPABILITY_REGISTRY[question.question_kind]


def capability_notice_for(
    capability: str | QuestionKind,
    *,
    request_source: str = "user",
) -> CapabilityNotice:
    """Build a bounded plain-language notice for a supported/unsupported kind."""

    normalized = capability.value if isinstance(capability, QuestionKind) else str(capability)
    definition = QUESTION_CAPABILITY_REGISTRY.get(normalized)
    if definition is None:
        return CapabilityNotice(
            capability=normalized,
            supported=False,
            reason="This investigation capability is not registered in the deployment.",
            available_alternatives=[],
            request_source=request_source,
        )
    return CapabilityNotice(
        capability=normalized,
        supported=definition.supported,
        reason=definition.unsupported_reason or "The capability is configured.",
        available_alternatives=list(definition.available_alternatives),
        request_source=request_source,
    )


def requested_capability_notices(user_query: str) -> list[CapabilityNotice]:
    """Detect explicit unsupported capability requests at the user boundary.

    This is intentionally conservative: it only reports the material/supplier
    genealogy capability when the user names a material-like trace request.
    It never turns a normal RCA question into a material question.
    """

    lowered = " ".join(user_query.casefold().split())
    material_tokens = (
        "material genealogy",
        "supplier genealogy",
        "consumable genealogy",
        "material trace",
        "material batch",
        "supplier batch",
        "材料追溯",
        "供应商",
        "耗材批次",
    )
    if not any(token in lowered for token in material_tokens):
        return []
    return [capability_notice_for(QuestionKind.MATERIAL_TRACE)]


def compatible_actions_for_question(question: InvestigationQuestion) -> frozenset[str]:
    definition = capability_for_question(question)
    if not definition.supported:
        return frozenset()
    return definition.allowed_actions


def _normalized(value: object) -> str | None:
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _values_for_key(payload: Mapping[str, Any], key: str) -> set[str]:
    values: set[str] = set()
    for source in (payload,):
        for raw_key, raw_value in source.items():
            if str(raw_key).casefold() != key.casefold():
                continue
            if isinstance(raw_value, (list, tuple, set)):
                values.update(item for item in (_normalized(value) for value in raw_value) if item)
            else:
                normalized = _normalized(raw_value)
                if normalized:
                    values.add(normalized)
    return values


def action_scope_matches_question(
    action: InvestigationAction,
    question: InvestigationQuestion,
) -> bool:
    """Check explicit scope keys without requiring an action to repeat defaults."""

    action_scope: dict[str, Any] = {**action.inputs, **action.scope}
    for key, question_value in question.scope.items():
        normalized_key = str(key).casefold()
        if normalized_key in {"lot_id", "source_lot_id"}:
            expected = _normalized(question_value)
            actual = _values_for_key(action_scope, "lot_id") | _values_for_key(
                action_scope, "source_lot_id"
            ) | _values_for_key(action_scope, "lot_ids")
            if expected and actual and expected not in actual:
                return False
        elif normalized_key == "lot_ids":
            expected_values = _values_for_key(question.scope, "lot_ids")
            actual_values = _values_for_key(action_scope, "lot_ids") | _values_for_key(
                action_scope, "lot_id"
            )
            if expected_values and actual_values and not expected_values <= actual_values:
                return False
        elif question_value is None:
            continue
        elif normalized_key in {"product_id", "module", "operation", "equipment_id", "chamber_id"}:
            action_value = action_scope.get(key)
            if action_value is None:
                action_value = next(
                    (
                        value
                        for action_key, value in action_scope.items()
                        if str(action_key).casefold() == normalized_key
                    ),
                    None,
                )
            if (
                action_value is not None
                and str(action_value).casefold() != str(question_value).casefold()
            ):
                return False
    return True


def validate_action_for_questions(
    action: InvestigationAction,
    questions: Sequence[InvestigationQuestion],
    *,
    missing_evidence_groups: Mapping[str, frozenset[str]] | None = None,
) -> None:
    """Atomically validate an Action against every targeted Question.

    The function raises before dispatch.  Callers must not filter targets or
    mutate state after a rejection.
    """

    if not questions:
        raise QuestionCapabilityError(
            "action_question_mismatch",
            "an Action decision must target at least one Question",
        )
    for question in questions:
        definition = capability_for_question(question)
        if not definition.supported:
            raise QuestionCapabilityError(
                "unsupported_question_kind",
                f"Question {question.question_id} uses unsupported kind "
                f"{question.question_kind}",
            )
        if action.kind not in definition.allowed_actions:
            raise QuestionCapabilityError(
                "action_question_mismatch",
                f"Action {action.kind} cannot answer Question "
                f"{question.question_id} ({question.question_kind})",
            )
        if not action_scope_matches_question(action, question):
            raise QuestionCapabilityError(
                "action_scope_mismatch",
                f"Action {action.action_id} scope is incompatible with "
                f"Question {question.question_id}",
            )
        if missing_evidence_groups is not None:
            missing = missing_evidence_groups.get(question.question_id, frozenset())
            contribution = definition.contribution_for(action.kind)
            if missing and not (missing & contribution):
                raise QuestionCapabilityError(
                    "no_expected_evidence_gain",
                    f"Action {action.kind} cannot fill a currently missing Evidence "
                    f"group for Question {question.question_id}",
                )


__all__ = [
    "QUESTION_CAPABILITY_REGISTRY",
    "QuestionCapabilityDefinition",
    "QuestionCapabilityError",
    "action_scope_matches_question",
    "capability_for_question",
    "capability_notice_for",
    "compatible_actions_for_question",
    "requested_capability_notices",
    "validate_action_for_questions",
]
