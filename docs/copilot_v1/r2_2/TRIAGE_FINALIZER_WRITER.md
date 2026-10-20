# R2.2 — Triage Finalizer Writer

Date: 2026-10-14 (Asia/Hong_Kong).
Baseline and independently committed R2.1: `411f6227ed566dc7f59d3bab1a08ecb41cb910b7`
(`fix: close copilot evidence coverage semantics`). R2.2 is a separate, uncommitted change.
Contract: frozen `triage/1.0`; no schema/version migration.

## Authority and lifecycle

`investigation_finalizer._attach_authority_results` first performs the existing RCA and
impact-publication construction, then attaches the independently validated triage value.
`triage_projection` is a pure reader/projector; it has no model, tool, database, Planner,
GT or scorer dependency. API, Report and Web do not select candidates or write triage.

Publication needs the explicit authoritative RCA finding, a matching old-authority
source pointer, the persisted model-candidate generation contract, and a safe terminal
state. Allowed Competition authorities are `not_required`, `complete_confirmed`,
`complete_rejected`, `exhausted`, `resolved`, `blocked_by_missing_data` and
`budget_exhausted`. A pending/active/failed trace or rejected investigation decision
withholds triage. Failed/cancelled/pending/retry-wait jobs do not publish. The existing
running-to-completed budget Finalizer path is admitted only with its explicit
`budget_exhausted` stop reason; no in-progress investigation is generally published.

Incompatible optional historical projection data withhold triage and record
`execution_metadata.triage_projection_status=withheld_invalid_projection` plus the
exception type. The already-generated old RCA/impact authority remains intact.

The existing `finalize_investigation` exception for non-terminal Competition remains.
This feature does not convert an execution-path failure into a completed investigation.
Native/controlled findings without the model admission contract remain unchanged.
Historical State/API loading performs no backfill.

## Candidate projection

1. Iterate the **persisted ranked set in its existing order**, never rerank.
2. Require unique candidate ID and semantic profile, admission not invalid, model-authored
   basis, exact nonempty engineering statement/explanation and supporting citations.
3. Exclude rejected or Competition-contradicted proposals, unknown/duplicate references,
   dependent/modifier/unknown mechanism relationships, and invalid frozen-schema values.
4. Project original text, mechanism, Evidence roles and declared scope verbatim. Do not
   infer a chamber, recipe version, time window or physical scope from Evidence coverage.
5. Primary is the first eligible proposal. Alternative is the next eligible independent
   mechanism with supporting Evidence. Casefold-equal mechanisms do not occupy both slots.
6. Preserve every original ranked row/profile/history entry; publish at most two roles.

Zero candidates: null roles, uncertainty and explicit no-action reason. One candidate:
Primary only, with unestablished comparative preference. Multiple candidates: up to two.
Absent Alternative distinguishes an incomplete/unrecorded search from an investigated
search that yielded no eligible independent projection. Neither disposition claims
causal exclusion. A conflicted but not rejected/contradicted candidate may remain visible,
with the conflict copied into assessment and uncertainty.

## Ranking and comparison

Stable persisted order is a display order. Equal uniquely matched persisted Python matrix
scores produce `tied`; otherwise the published pair conservatively uses
`not_distinguishable`. No raw heuristic score or probability is published. The writer
currently makes **no new evidence-preference claim**: `preferred_candidate_id=null`,
`causal_winner_established=false`. An absent Alternative is never a comparative victory.

## Evidence assessment and uncertainty

Parameter, mechanism, operation, scope, temporal and contradiction assessments copy the
existing per-candidate Matrix checks and Evidence IDs. Entity consistency preserves the
weakest check across explicitly declared equipment/chamber; it never adds an undeclared
chamber. Recipe/version consistency requires a persisted explicit check, otherwise null.
No recipe ID is upgraded to proof of version consistency.

`causal_chain.complete` is a completeness label, not mechanism confirmation. Where explicit
stages are all supported/plausible, their weakest existing status is projected. Other
existing supported/plausible/incomplete/conflicted/unavailable claim statuses are retained.
Alternative-search lifecycle diagnostics project as incomplete, not proof of exclusion.

Frozen-schema limitation: an old `contradiction=supported` check with **zero cited Evidence**
cannot meet triage/1.0's observed-support constraint. Its triage assessment is null with an
explicit unrepresentable/unassessed uncertainty; the old Matrix and Gate remain untouched.
Missing checks are not silently assigned supported. No citation is invented to fit schema.

| Persisted fact | Uncertainty type |
|---|---|
| Scoped no-observation claim with citations | `not_observed` |
| Explicit unacquired claim / R2.1 lane acquisition coverage | `not_acquired` |
| Unavailable or missing/unrepresentable assessment; legacy unknown coverage | `unavailable` (explanation retains unknown) |
| Conflicted check | `conflicting` |
| Unresolved/unfinished alternative search or retained unresolved pair | `unresolved_alternative` |
| Plausible/incomplete mechanism or causal bridge | `unsupported_mechanism` |
| Incomplete scope or explicit recipe/version check | `insufficient_scope` |
| Incomplete temporal check | `insufficient_temporal_support` |

Coverage checks include declared comparison lanes; each coverage uncertainty carries that
lane's persisted scope without widening the hypothesis's own physical scope. R2.1 receipts are read only when already persisted. Historical Pilot data
are **not enriched with post-Pilot tool replay data**; missing acquisition metadata remain
unknown. `unknown` and `not_acquired` never become negative.

## Next investigation action

Use the existing selected-gap order, then original gap order. Reuse matching model-authored
conditional predictions by discriminator kind and compatible lane. Require a prediction
for every published target; a pair requires distinct observations and overlapping prediction
scope. Gap action scope/time is validated as written, never repaired or widened. If a gap has
no explicit target scope, the unchanged Primary declared scope is used as handoff context.
The request
uses existing gap explanation and requested Evidence types. This does not claim that the
proposed experiment is causally decisive; the engineer reviews its feasibility/value.

Only an eligible high-value matching ActionValue option, available source, allowed action,
sufficient recorded tool budget and no engineer-only discrimination flag may be described
as system-executable. This is a recommendation, never a dispatch. LLM reasoning refresh is
not represented as a data-acquisition tool. All other compatible proposals are handed to
a human engineer with approval required and honest source availability.

The frozen schema's spelling is `executor=human_engineer`, `executability=external_only`,
with `terminal_reason=engineer_evidence_required` where applicable. R2.2 does not add a new
executability enum. Budget/data-unavailable terminal reasons are preserved. No suitable
persisted conditional proposal means null action plus `no_next_action_reason`, not an
invented engineering test. In particular, a single composite candidate is not split into
two hypotheses to manufacture a discrimination action.

## Confirmed / suspected separation

Old `authoritative_rca_result`, confirmed root cause, impact publication, Confirmation
status and source findings are unchanged. An inconclusive old result can coexist with
non-null triage. Internally supported RCA can also have triage; all triage roles retain
`engineering_sign_off=not_recorded`, `scientific_question_resolved=false`. No triage text
is assigned to confirmed fields. API only serializes the already-written optional value.
No DB migration or data rewrite is required.

## FORMAL_005 saved-state replay

The replay reads the two saved Pilot states, invokes the new projection offline, and
preserves the original files and Benchmark records. It is not a new RCA/Benchmark run.

| Saved state | Published roles | Comparison | Mechanism | Confirmed root cause |
|---|---|---|---|---|
| DeepSeek | Existing beam Primary; existing chuck Alternative | tied (persisted 16/16) | plausible | null |
| Qwen | Original composite beam/scan/rotation Primary; no fabricated Alternative | preference_not_established | plausible | null |

Both retain unsupported mechanism and unresolved-alternative uncertainty. DeepSeek's
existing parameter/outcome conditional predictions support an engineer handoff. Qwen has
no scope-compatible complete action proposal meeting the frozen contract, so emits an
explicit no-action reason. Historical acquisition coverage is unknown where receipts were
not recorded. No equipment, parameter, recipe, case or model name is special-cased in code.

## Change boundary and next Batch

Production changes: one new `triage_projection.py` and the Finalizer attachment. No
Evidence acquisition, Prompt, Candidate generation, Competition/Ranking algorithm,
Confirmation, repair/retry, Provider, benchmark/scorer, GT or database schema change.
Tests add boundary, provenance, round-trip, offline replay, old-authority and exact parity
coverage. Earlier immutable baselines remain; a separate R2.2 fixture freezes additive
State/API deltas. Full validation and protected-content results are in `VALIDATION.md`.

R3 minimum proposal (not implemented): display the serialized triage object as a distinct
suspected-investigation panel in API/Web/Report, show ties and gaps literally, render the
recommendation as a handoff requiring approval, and preserve the old confirmed-result UI
for historical records without triage. Never reconstruct from ranked_candidates[0].
