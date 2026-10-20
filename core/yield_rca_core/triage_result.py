"""Copilot R1 value contracts. No selection, ranking, inference or execution here.

The future Python Finalizer alone may publish this container. Its authority is
hypothesis triage, never confirmation or human engineering sign-off.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from types import UnionType
from typing import Any, Literal, Self, get_args, get_origin, get_type_hints

from yield_rca_core.evidence_models import ModelValidationError

TRIAGE_SCHEMA_VERSION = "triage/1.0"
ClaimStatus = Literal["supported", "plausible", "incomplete", "conflicted", "unavailable"]
UncertaintyKind = Literal[
    "not_observed",
    "not_acquired",
    "unavailable",
    "conflicting",
    "unresolved_alternative",
    "unsupported_mechanism",
    "insufficient_scope",
    "insufficient_temporal_support",
]
PreferenceStatus = Literal[
    "preference_established",
    "preference_not_established",
    "tied",
    "not_distinguishable",
]
AlternativeDisposition = Literal[
    "retained",
    "no_valid_alternative_identified",
    "alternative_not_investigated",
]
TriageTerminalReason = Literal[
    "budget_exhausted",
    "no_high_value_action",
    "data_unavailable",
    "no_allowed_action",
    "critical_contradiction",
    "hypotheses_tied",
    "engineer_evidence_required",
    "single_candidate_unconfirmed",
    "insufficient_candidate_evidence",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelValidationError(message)


def _typed(value: Any, annotation: Any, path: str, *, decode: bool = False) -> Any:
    """Strict JSON/dataclass boundary; no str/bool coercion or silent extra keys."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is UnionType:
        for option in args:
            try:
                return _typed(value, option, path, decode=decode)
            except ModelValidationError:
                pass
        raise ModelValidationError(f"{path}: invalid optional value")
    if origin is Literal:
        _require(
            any(type(value) is type(x) and value == x for x in args),
            f"{path}: expected one of {args}",
        )
    elif origin is tuple:
        _require(isinstance(value, (list, tuple) if decode else tuple), f"{path}: array required")
        value = tuple(_typed(v, args[0], path, decode=decode) for v in value)
        _require(len(value) == len(set(value)), f"{path}: duplicates forbidden")
    elif isinstance(annotation, type) and issubclass(annotation, TriageValue):
        if decode and isinstance(value, Mapping):
            return annotation.from_dict(value)
        _require(type(value) is annotation, f"{path}: expected {annotation.__name__}")
    elif annotation is type(None):
        _require(value is None, f"{path}: expected null")
    else:
        _require(type(value) is annotation, f"{path}: invalid type")
        if annotation is str:
            _require(bool(value.strip()), f"{path}: blank text forbidden")
    return value


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    return value


@dataclass(frozen=True)
class TriageValue:
    def __post_init__(self) -> None:
        for name, annotation in get_type_hints(type(self)).items():
            _typed(getattr(self, name), annotation, f"{type(self).__name__}.{name}")
        self._validate()

    def _validate(self) -> None:
        pass

    def to_dict(self) -> dict[str, Any]:
        return dict(_json(asdict(self)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        _require(isinstance(data, Mapping), f"{cls.__name__}: object required")
        annotations = get_type_hints(cls)
        _require(not (set(data) - annotations.keys()), f"{cls.__name__}: unknown fields")
        try:
            return cls(**{k: _typed(v, annotations[k], k, decode=True) for k, v in data.items()})
        except TypeError as exc:
            raise ModelValidationError(f"{cls.__name__}: missing or invalid fields") from exc


@dataclass(frozen=True)
class TriageScope(TriageValue):
    """Declared candidate scope; null/empty means unknown, not negative/all."""

    entity: str | None
    operation: str | None
    equipment: str | None
    chamber: str | None
    recipe: str | None
    recipe_version: str | None
    scope_kind: str
    lane_ids: tuple[str, ...]
    time_window: tuple[str, ...]

    def _validate(self) -> None:
        _require(len(self.time_window) in (0, 2), "time_window needs zero or two timestamps")
        if self.time_window:
            try:
                start, end = (
                    datetime.fromisoformat(t.replace("Z", "+00:00")) for t in self.time_window
                )
            except ValueError as exc:
                raise ModelValidationError("invalid time_window") from exc
            _require(
                start.tzinfo is not None and end.tzinfo is not None, "time_window needs timezones"
            )
            _require(start <= end, "time_window start must precede end")
        _require(
            self.recipe_version is None or self.recipe is not None, "recipe_version requires recipe"
        )
        _require(self.chamber is None or self.equipment is not None, "chamber requires equipment")
        _require(
            self.scope_kind
            in {"lane", "recipe", "chamber", "equipment", "operation", "unresolved"},
            "invalid scope_kind",
        )


@dataclass(frozen=True)
class TriageHypothesis(TriageValue):
    candidate_id: str
    engineering_statement: str
    causal_explanation: str
    primary_mechanism: str
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    scope: TriageScope
    authority: Literal["suspected_hypothesis"] = "suspected_hypothesis"
    engineering_sign_off: Literal["not_recorded"] = "not_recorded"

    def _validate(self) -> None:
        _require(bool(self.supporting_evidence_ids), "hypothesis needs supporting Evidence")
        _require(
            not set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids),
            "Evidence cannot be both supporting and contradicting",
        )


@dataclass(frozen=True)
class TriageComparison(TriageValue):
    preference_status: PreferenceStatus
    ordering_basis: Literal["evidence_preference", "stable_presentation_order", "single", "none"]
    primary_ordering_reason: str
    alternative_retention_reason: str | None
    alternative_disposition: AlternativeDisposition
    discriminating_evidence_ids: tuple[str, ...]
    preferred_candidate_id: str | None
    # A ranking preference is not a causal winner; no probability/score field.
    causal_winner_established: Literal[False] = False


@dataclass(frozen=True)
class TriageClaimAssessment(TriageValue):
    status: ClaimStatus
    evidence_ids: tuple[str, ...]
    explanation: str
    source_path: str
    observation_status: Literal[
        "observed", "not_observed", "not_acquired", "unavailable", "unknown"
    ]

    def _validate(self) -> None:
        if self.observation_status == "not_observed":
            _require(bool(self.evidence_ids), "not_observed requires a scoped observation")
        if self.status == "supported":
            _require(
                self.observation_status in {"observed", "not_observed"} and bool(self.evidence_ids),
                "unknown cannot become supported/negative",
            )


@dataclass(frozen=True)
class TriageEvidenceAssessment(TriageValue):
    """Per-candidate projections of existing matrix/checks. None = not assessed."""

    candidate_id: str
    parameter_support: TriageClaimAssessment | None
    mechanism_support: TriageClaimAssessment | None
    entity_consistency: TriageClaimAssessment | None
    operation_consistency: TriageClaimAssessment | None
    recipe_version_consistency: TriageClaimAssessment | None
    scope_consistency: TriageClaimAssessment | None
    temporal_consistency: TriageClaimAssessment | None
    contradiction: TriageClaimAssessment | None
    causal_chain: TriageClaimAssessment | None
    alternative_status: TriageClaimAssessment | None


@dataclass(frozen=True)
class TriageUncertainty(TriageValue):
    kind: UncertaintyKind
    description: str
    candidate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    scope: TriageScope

    def _validate(self) -> None:
        if self.kind == "not_observed":
            _require(bool(self.evidence_ids), "not_observed requires observation Evidence")


@dataclass(frozen=True)
class DiscriminatingOutcome(TriageValue):
    candidate_id: str
    expected_observation: str
    interpretation: str


@dataclass(frozen=True)
class NextInvestigationAction(TriageValue):
    action_id: str
    purpose: Literal["discriminate_hypotheses", "acquire_missing_evidence"]
    target_candidate_ids: tuple[str, ...]
    requested_observation: str
    scope: TriageScope
    expected_discriminating_outcomes: tuple[DiscriminatingOutcome, ...]
    inconclusive_outcome: str
    executor: Literal["system_tool", "human_engineer", "external_process"]
    approval_required: bool
    executability: Literal[
        "executable", "requires_approval", "external_only", "not_executable", "unknown"
    ]
    availability: Literal["available", "unavailable", "unknown"]
    availability_reason: str
    recommendation_only: Literal[True] = True

    def _validate(self) -> None:
        outcomes = [x.candidate_id for x in self.expected_discriminating_outcomes]
        _require(len(outcomes) == len(set(outcomes)), "one outcome per target hypothesis")
        _require(
            set(outcomes) == set(self.target_candidate_ids),
            "every action target needs a conditional expected outcome",
        )
        if self.purpose == "discriminate_hypotheses":
            _require(len(self.target_candidate_ids) == 2, "discrimination needs two hypotheses")
            _require(
                len(
                    {
                        x.expected_observation.strip().casefold()
                        for x in self.expected_discriminating_outcomes
                    }
                )
                == 2,
                "discriminating observations cannot be identical",
            )
        if self.executability == "executable":
            _require(
                self.executor == "system_tool"
                and self.availability == "available"
                and not self.approval_required,
                "executable requires available approved tool",
            )
        if self.executability == "requires_approval":
            _require(self.approval_required, "requires_approval needs approval_required")
        if self.executor != "system_tool":
            _require(self.executability != "executable", "external recommendation is not execution")


@dataclass(frozen=True)
class TriageResult(TriageValue):
    result_id: str
    source_finding_id: str
    primary: TriageHypothesis | None
    strongest_alternative: TriageHypothesis | None
    comparison: TriageComparison
    evidence_assessment: tuple[TriageEvidenceAssessment, ...]
    remaining_uncertainty: tuple[TriageUncertainty, ...]
    next_investigation_action: NextInvestigationAction | None
    no_next_action_reason: str | None
    terminal_reason: TriageTerminalReason
    schema_version: Literal["triage/1.0"] = "triage/1.0"
    authority: Literal["hypothesis_triage"] = "hypothesis_triage"
    writer: Literal["python_finalizer"] = "python_finalizer"
    execution_completed: Literal[True] = True
    scientific_question_resolved: Literal[False] = False
    engineering_sign_off: Literal["not_recorded"] = "not_recorded"

    @property
    def hypotheses(self) -> tuple[TriageHypothesis, ...]:
        return tuple(x for x in (self.primary, self.strongest_alternative) if x is not None)

    def _validate(self) -> None:
        ids = {x.candidate_id for x in self.hypotheses}
        _require(len(ids) == len(self.hypotheses), "hypotheses must have distinct IDs")
        _require(
            self.primary is not None or self.strongest_alternative is None,
            "alternative requires primary",
        )
        c = self.comparison
        if self.strongest_alternative is None:
            _require(
                c.alternative_disposition != "retained" and c.alternative_retention_reason is None,
                "absent alternative is unidentified or uninvestigated, never eliminated",
            )
            _require(
                c.preference_status == "preference_not_established"
                and c.ordering_basis == ("single" if self.primary else "none"),
                "zero/one candidate cannot establish comparative preference",
            )
        else:
            _require(
                c.alternative_disposition == "retained"
                and c.alternative_retention_reason is not None,
                "alternative needs retention basis",
            )
            _require(
                self.primary is not None
                and self.primary.primary_mechanism.strip().casefold()
                != self.strongest_alternative.primary_mechanism.strip().casefold(),
                "scope/recipe modifier is not an independent alternative",
            )
            expected = (
                "evidence_preference"
                if c.preference_status == "preference_established"
                else "stable_presentation_order"
            )
            _require(
                c.ordering_basis == expected, "tie/unknown preference requires stable UI order"
            )
        if c.preference_status == "preference_established":
            _require(
                self.primary is not None
                and c.preferred_candidate_id == self.primary.candidate_id
                and bool(c.discriminating_evidence_ids),
                "preference needs discriminating Evidence",
            )
        else:
            _require(c.preferred_candidate_id is None, "unestablished preference has no winner")
        assessment_ids = [a.candidate_id for a in self.evidence_assessment]
        _require(
            len(assessment_ids) == len(set(assessment_ids)) and set(assessment_ids) == ids,
            "exactly one assessment per published candidate",
        )
        _require(bool(self.remaining_uncertainty), "unconfirmed triage must preserve uncertainty")
        for gap in self.remaining_uncertainty:
            _require(set(gap.candidate_ids) <= ids, "uncertainty targets unknown candidate")
        action = self.next_investigation_action
        _require(
            (action is None) == (self.no_next_action_reason is not None),
            "absent next action needs explicit reason",
        )
        if action is not None:
            _require(set(action.target_candidate_ids) <= ids, "action targets unknown candidate")
            if action.purpose == "discriminate_hypotheses":
                _require(
                    set(action.target_candidate_ids) == ids, "action must compare published pair"
                )
        if self.terminal_reason == "single_candidate_unconfirmed":
            _require(len(ids) == 1, "single_candidate_unconfirmed requires one candidate")
        if self.terminal_reason == "hypotheses_tied":
            _require(
                len(ids) == 2 and c.preference_status in {"tied", "not_distinguishable"},
                "hypotheses_tied requires unresolved pair",
            )

    def validate_state_references(self, findings: Any, evidence_ids: set[str]) -> None:
        """Validate provenance only; never select or populate a triage result.

        Existing ranked records are accepted model proposals, looked up by ID,
        not by rank. Semantic profiles supply meaning, never text inference.
        """
        sources = [f for f in findings if f.finding_id == self.source_finding_id]
        _require(
            len(sources) == 1 and sources[0].agent == "rca_reasoning",
            "triage source must be a unique RCA Finding",
        )
        details = sources[0].details
        candidates = details.get("ranked_candidates", [])
        profiles = details.get("candidate_semantic_profiles", [])
        for h in self.hypotheses:
            rows = [r for r in candidates if r.get("candidate_id") == h.candidate_id]
            _require(len(rows) == 1, "triage hypothesis must reference an admitted candidate")
            row = rows[0]
            _require(
                row.get("basis") == "llm_evidence_composition",
                "triage requires a model-authored admitted proposal",
            )
            for name, actual in (
                ("root_cause", h.engineering_statement),
                ("causal_explanation", h.causal_explanation),
            ):
                _require(row.get(name) == actual, "triage must preserve original model text")
            for name in ("supporting_evidence_ids", "contradicting_evidence_ids"):
                _require(
                    tuple(row.get(name, [])) == getattr(h, name),
                    "triage must preserve candidate Evidence roles and citations",
                )
            ps = [p for p in profiles if p.get("candidate_id") == h.candidate_id]
            _require(len(ps) == 1, "triage needs admitted semantic profile")
            p = ps[0]
            _require(
                p.get("primary_mechanism") == h.primary_mechanism,
                "triage must preserve mechanism identity",
            )
            _require(
                p.get("mechanism_relation") in {"reference", "independent_alternative"}
                and not p.get("depends_on_candidate_id"),
                "modifier/dependent/unknown profile cannot occupy independent hypothesis slot",
            )
            scope = h.scope
            for field_name, source_key in (
                ("operation", "claimed_operation"),
                ("equipment", "claimed_equipment"),
                ("chamber", "claimed_chamber"),
                ("recipe", "claimed_recipe"),
                ("scope_kind", "claimed_scope_kind"),
            ):
                _require(
                    getattr(scope, field_name) == p.get(source_key),
                    "triage must preserve declared scope",
                )
            _require(
                scope.lane_ids == tuple(p.get("claimed_lane_ids", [])),
                "triage must preserve claimed lanes",
            )
            # Current profiles do not always carry these claims. Never infer them
            # from query scope, Evidence coverage, or a recipe's name.
            for name in ("entity", "recipe_version"):
                _require(
                    getattr(scope, name) == p.get(name), f"undeclared {name} cannot be inferred"
                )
            _require(
                scope.time_window == tuple(p.get("time_window", [])),
                "undeclared time_window cannot be inferred",
            )
        if self.hypotheses:
            generation = details.get("hypothesis_candidate_generation", {})
            _require(
                generation.get("candidate_output_invalid") is False,
                "triage requires successful candidate admission provenance",
            )

        def check(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for k, v in value.items():
                    check(v, k)
            elif key.endswith("evidence_ids"):
                _require(set(value) <= evidence_ids, "triage references unknown Evidence")
            elif isinstance(value, list):
                for v in value:
                    check(v)

        check(self.to_dict())
