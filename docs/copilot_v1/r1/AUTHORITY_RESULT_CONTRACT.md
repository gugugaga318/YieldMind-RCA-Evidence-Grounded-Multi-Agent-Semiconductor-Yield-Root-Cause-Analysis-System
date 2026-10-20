# RCA Investigation Copilot v1 — R1 Authority / Result Contract

Status: `triage/1.0` contract freeze for review; **no production writer enabled**.
Code baseline: `8783da9b22c9120f1475115a6fe8e406f5f2b16d`.
This is a separate product contract, not a change to `development-rca/1.1` or
`formal-r2-scorer/1.1`. The old 18-run Pilot, GT, results and scoring remain untouched.

## 1. Authority boundaries

`authoritative_rca_result` and `triage_result` are separate authorities. A triage
hypothesis is **suspected**, never a confirmed root cause or confirmed impact.
No triage field is copied into legacy `root_cause`, Hypothesis, confirmation,
impact or report authority. Existing Confirmation supported requirements remain
exactly as implemented. Internal `supported` is not human engineering sign-off.

The future **Python Finalizer** is the sole authoritative triage writer. LLM may
propose hypotheses, original explanations, comparisons and investigation actions;
it cannot submit an authoritative result. API, Report and Web may only project an
already published result; they must not infer one from `ranked_candidates[0]`.

R1 intentionally adds **no producer, setter, automatic selection, LLM call or
Finalizer invocation**. `writer="python_finalizer"` is a provenance contract, not
a cryptographic capability. Provenance is validated against State on construction
and deserialization. The caller boundary remains trusted Python; the existing
HTTP creation request rejects `triage_result` as an extra input. No new write API
exists. An AST contract test guards against accidentally introducing a producer
in this batch. R2 must add one explicit Finalizer writer and its integration tests.

## 2. Complete typed schema

Framework-free frozen domain dataclasses are defined in
`core/yield_rca_core/triage_result.py`; Pydantic `TriageResultResponse` wraps that
same type rather than independently defining a second contract. Full machine
schema: [triage-result.schema.json](triage-result.schema.json).
Synthetic, non-benchmark examples: [examples.json](examples.json).

All object properties are checked strictly at the domain/API validation boundary:
extra fields, wrong scalar types, blank strings, duplicate tuple values and
unsupported literal values are rejected. JSON Schema documents the field types;
Python additionally enforces the cross-field and State provenance rules below.
JSON arrays deserialize to immutable tuples. Output JSON contains ordinary arrays
and objects; there is no mappingproxy or mutable nested contract collection.

```text
TriageResult
  schema_version: "triage/1.0"
  result_id: nonempty string
  source_finding_id: nonempty string (exact RCA Finding ID)
  authority: "hypothesis_triage"
  writer: "python_finalizer"
  execution_completed: true
  scientific_question_resolved: false
  engineering_sign_off: "not_recorded"
  primary: TriageHypothesis | null
  strongest_alternative: TriageHypothesis | null
  comparison: TriageComparison
  evidence_assessment: TriageEvidenceAssessment[]
  remaining_uncertainty: TriageUncertainty[] (nonempty)
  next_investigation_action: NextInvestigationAction | null
  no_next_action_reason: nonempty string | null
  terminal_reason: TriageTerminalReason

TriageHypothesis
  candidate_id: nonempty string
  engineering_statement: exact persisted model-authored candidate text
  causal_explanation: exact persisted model-authored explanation
  primary_mechanism: exact admitted semantic-profile mechanism
  supporting_evidence_ids: nonempty unique Evidence ID[]
  contradicting_evidence_ids: unique Evidence ID[]
  scope: TriageScope
  authority: "suspected_hypothesis"
  engineering_sign_off: "not_recorded"

TriageScope
  entity: string | null
  operation: string | null
  equipment: string | null
  chamber: string | null
  recipe: string | null
  recipe_version: string | null
  scope_kind: lane | recipe | chamber | equipment | operation | unresolved
  lane_ids: unique string[]
  time_window: [] | [timezone-aware ISO-8601 start, end]

TriageComparison
  preference_status: preference_established | preference_not_established |
                     tied | not_distinguishable
  ordering_basis: evidence_preference | stable_presentation_order | single | none
  primary_ordering_reason: nonempty string
  alternative_retention_reason: nonempty string | null
  alternative_disposition: retained | no_valid_alternative_identified |
                           alternative_not_investigated
  discriminating_evidence_ids: unique Evidence ID[]
  preferred_candidate_id: string | null
  causal_winner_established: false

TriageClaimAssessment
  status: supported | plausible | incomplete | conflicted | unavailable
  evidence_ids: unique Evidence ID[]
  explanation: nonempty string
  source_path: nonempty string (diagnostic provenance inside source Finding)
  observation_status: observed | not_observed | not_acquired | unavailable | unknown

TriageEvidenceAssessment
  candidate_id: string
  parameter_support: TriageClaimAssessment | null
  mechanism_support: TriageClaimAssessment | null
  entity_consistency: TriageClaimAssessment | null
  operation_consistency: TriageClaimAssessment | null
  recipe_version_consistency: TriageClaimAssessment | null
  scope_consistency: TriageClaimAssessment | null
  temporal_consistency: TriageClaimAssessment | null
  contradiction: TriageClaimAssessment | null
  causal_chain: TriageClaimAssessment | null
  alternative_status: TriageClaimAssessment | null

TriageUncertainty
  kind: not_observed | not_acquired | unavailable | conflicting |
        unresolved_alternative | unsupported_mechanism | insufficient_scope |
        insufficient_temporal_support
  description: nonempty string
  candidate_ids: unique published candidate ID[] (empty allowed for 0 candidates)
  evidence_ids: unique Evidence ID[]
  scope: TriageScope

DiscriminatingOutcome
  candidate_id: published candidate ID
  expected_observation: nonempty string, conditional future observation
  interpretation: nonempty string, what that observation would support

NextInvestigationAction
  action_id: nonempty string
  purpose: discriminate_hypotheses | acquire_missing_evidence
  target_candidate_ids: unique published candidate ID[]
  requested_observation: nonempty string
  scope: TriageScope (requested entity/scope/time, not an asserted observation)
  expected_discriminating_outcomes: DiscriminatingOutcome[]
  inconclusive_outcome: nonempty string
  executor: system_tool | human_engineer | external_process
  approval_required: boolean
  executability: executable | requires_approval | external_only | not_executable | unknown
  availability: available | unavailable | unknown
  availability_reason: nonempty string
  recommendation_only: true
```

## 3. Primary provenance and scope

A hypothesis references an admitted model candidate **by stable ID**, never by
position. Current source is the source Finding's `ranked_candidates` record
(`basis=llm_evidence_composition`) plus its matching `candidate_semantic_profiles`
and successful candidate generation provenance. Gate rejection reasons in that
record are **not** admission failure: a legally admitted but unconfirmed candidate
may be published as suspected. This contract does not rerun or relax any validator.
A missing/ambiguous candidate, failed admission, missing profile or unknown Evidence
ID is rejected.

`engineering_statement`, `causal_explanation`, supporting/contradicting citations
and declared mechanism/scope are exact projections. Python does not normalize,
rewrite, compose or invent engineering prose. "Exact" refers to the persisted
admitted model-authored text; no claim is made that it is a raw HTTP byte transcript.
Evidence roles cannot be silently dropped or swapped. The two citation sets cannot
overlap. All result Evidence IDs, including comparison, gaps and assessments, must
exist in State.

Unknown version/time/entity remain `null` / `[]`; query bounds, Evidence coverage
or recipe names cannot fill them. Explicit claims, when present in the source
semantic profile, are copied exactly. Current profiles do not consistently carry
version/time/entity claims, so R1 normally leaves these unknown. This is an honest
representation, not a claim that current profiles gained those fields. Evidence
assessment can report missing recipe/version or temporal support. No profile or
Candidate production schema was changed in this batch.

Version requires recipe; chamber requires equipment; time needs two ordered,
timezone-bearing endpoints. An empty time window is unknown, never "all time".
Claimed scope and Evidence coverage remain distinct. No scope expansion, component
or sub-step inference is introduced.

## 4. Alternative and comparison

An Alternative has a distinct candidate ID and admitted independent mechanism.
Dependent, nested, unknown-relation and shared-primary modifier profiles cannot
occupy an independent hypothesis slot. Recipe/scope modifiers remain diagnostics.
The two mechanism labels cannot be identical modulo case/outer whitespace; this
check supplements, **does not replace**, the existing admitted semantic-profile
validation. R1 introduces no semantic classifier or new equivalence mapping.

Both published hypotheses must have actual supporting Evidence. This follows the
existing admitted candidate contract. The comparison must separately explain why
the Alternative remains relevant. Null Alternative has exactly two meanings:

- `no_valid_alternative_identified`: investigation has not yielded a valid
  independent Alternative; **does not mean alternatives eliminated**.
- `alternative_not_investigated`: search was not performed or is incomplete.

With two hypotheses, evidence-backed preference uses `preference_established`,
`evidence_preference`, the Primary ID and nonempty discriminating Evidence IDs.
A tie, indistinguishability or unestablished preference requires
`stable_presentation_order` and `preferred_candidate_id=null`. The UI must show
that qualifier prominently; position #1 is not proof of superiority.
`causal_winner_established` is always false in triage, even with ranking preference.

No raw score, confidence percentage or calibrated probability is published.
Existing scores stay in original diagnostics. R1 does not create a
probable/plausible/weak overall grade. `plausible` only retains its existing
per-claim causal-matrix meaning.

## 5. Evidence assessment and honest uncertainty

Exactly one Evidence assessment per published hypothesis is required. Its ten
named dimensions prevent collapsing mechanism, scope, time and alternatives into
one Boolean. Each entry retains the existing five `CausalClaimStatus` values;
`null` means not assessed. R2 will project existing causal matrix/Confirmation
checks and preserve their diagnostic source path. R1 neither computes assessments
nor changes any check's meaning or threshold. Mapping source diagnostic rows to
these dimensions and verifying the free-text explanations is R2 writer work.

Unknown/not acquired/unavailable cannot be marked as a supported observation.
There is no generic conversion from unknown to negative. `not_observed` requires
an actual Evidence reference and means "not observed in the specified observation
scope", not absence from context. Source unavailable differs from a successful
scoped no-match retrieval. Neither can automatically establish universal exclusion.

Remaining uncertainty is nonempty because triage cannot certify scientific
resolution. Candidate references must belong to the published pair; omitted
hypotheses remain in Finding diagnostics/history rather than becoming fictional
resolved alternatives.

## 6. Next action is a recommendation

A discriminating action targets **exactly both published hypotheses**, with one
conditional expected observation per hypothesis. Identical observations cannot be
used to pretend an action discriminates. It also records an inconclusive outcome.
For zero or one candidate, `acquire_missing_evidence` supports zero/one target and
never fabricates a second hypothesis.

Executability and availability are separate from investigative value. External,
unavailable or currently non-executable recommendations are legal and retain their
requested observation. `executable` requires an available system tool and no pending
approval. `requires_approval` requires `approval_required=true`. Even `executable`
is still `recommendation_only=true`; it is not dispatch authority. This object is
not an `InvestigationAction` and no adapter to Planner or tool dispatch is added.

If no defensible recommendation exists, the action may be null only with a
nonempty `no_next_action_reason`. No new LLM call fills this field in R1.

## 7. Terminal semantics and candidate cardinality

Terminal reason vocabulary reuses existing stopping labels where suitable:
`budget_exhausted`, `no_high_value_action`, `data_unavailable`, `no_allowed_action`,
`critical_contradiction`; triage explanations add `hypotheses_tied`,
`engineer_evidence_required`, `single_candidate_unconfirmed`,
`insufficient_candidate_evidence`. These describe the result, **not workflow
transitions**. Existing GoalStatus, ConclusionLevel, StopReason and candidate
status enums are unchanged.

| Admitted usable hypotheses | Published result | Required honesty |
|---|---|---|
| 0 | Primary null, Alternative null | Evidence gaps; evidence-acquisition action or explicit inability to recommend; no fabricated hypothesis |
| 1 | Primary, Alternative null | No comparative preference and no "alternative eliminated" claim |
| 2+ | At most Primary + Strongest Alternative | Preserve other candidates/history; no extra generation call for Top-2 |

`execution_completed=true` says the triage publication is terminal, while
`scientific_question_resolved=false` preserves the unresolved engineering question.
This does not modify job execution status or the old confirmed authority. Human
sign-off is not modeled as an automatic transition; a future human workflow would
need its own explicit provenance.

## 8. Historical compatibility and persistence

`RCAState.triage_result` is optional and appended after existing dataclass fields,
preserving their positional constructor order. A missing/null value deserializes
as None. State serialization omits it when absent, matching the existing optional
authority-container convention. API omits only this absent field; all existing
null/default response behavior stays unchanged. The wrapper preserves the full
typed OpenAPI response schema.

No rank-based or historical backfill is permitted. Legacy Web/Report continues
using the old authority. Future Web work must branch on the explicit presence and
schema version of triage, label suspected authority, and never use Primary as a
confirmed fallback. R1 adds no frontend renderer and does not claim a future UI is
already implemented.

State is already persisted in JSONB. **No DB migration is needed**. Strict source
validation applies only when triage is explicitly present; historical states do
not need new admission/profile data. Unsupported triage schema versions fail
closed rather than silently converting to old confirmed fields.

## 9. Tests, parity and scope

[VALIDATION.md](VALIDATION.md) records final checks. Added tests cover authority
separation; source IDs/text/citations/scope; malformed types; 0/1/2+ cardinality;
null-alternative distinctions; tie presentation; unknown versus negative;
recommendation targets/approval/availability; immutable JSON round trips;
historical absent-field serialization; strict input rejection and typed OpenAPI.

Before any production edit, five existing synthetic deterministic workflow
scenarios were captured at the exact baseline commit with network forbidden.
`tests/fixtures/copilot_r1/pre_r1_payload_hashes.json` stores hashes of the full
State (including identity/telemetry), full typed API JSON and pre-existing workflow
captures. The baseline is not refreshed after edits. These cover controlled ReAct,
normal LLM-shaped fake execution, next-action failure, invalid intent and stop.
Existing challenge/fixed/controlled/runtime parity tests run in full regression.
No real-provider behavior is inferred from fake-workflow parity.

Only a new domain module and optional State/API serialization/validation were
added. No RCA investigation semantics, Prompt, Gate, Competition, Ranking,
Finalizer selection, repair/retry policy, scorer, GT or Benchmark artifacts changed.
No real model was called. Existing RCA outputs must remain byte-equivalent under
the deterministic JSON comparisons; the new contract is dormant.

## 10. Minimal R2 proposal (not implemented)

1. Add one explicit Python Finalizer triage writer, binding admitted candidates by
   stable ID and preserving their original text/citations/profile.
2. Project existing ranking/comparison into Primary + a valid independent
   Alternative, qualifying ties without introducing new generation or probabilities.
3. Project matrix/Confirmation diagnostics to per-dimension assessment and gaps;
   preserve all existing Confirmation authority and confirmed publication rules.
4. Select one recommendation from existing action/value/proposal data, with its
   conditional discrimination and tool/external availability, without dispatching it.

Evidence-flow correctness work (e.g. lost outcome/structural associations and
unknown-to-negative interpretation) must remain explicit, separately reviewed
changes; suspected publication does not justify concealing those defects.
API/Web presentation and Copilot benchmark changes remain later batches.
