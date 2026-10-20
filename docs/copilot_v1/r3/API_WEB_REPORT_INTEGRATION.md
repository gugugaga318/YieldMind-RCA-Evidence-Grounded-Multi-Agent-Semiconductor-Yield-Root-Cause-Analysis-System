# R3 — API / Web / Report Integration

Date: 2026-10-14, Asia/Hong_Kong.
R2.2 independently committed as `60045be536752c882cc55c6d80c4b4538d655b66`
(`feat: publish copilot triage results from finalizer`); exactly its 13-file approved
manifest was committed before any R3 work. R3 remains uncommitted, awaiting review.
Frozen domain contract and schema remain `triage/1.0`.

## Authoritative source and API

All new result presentation reads `state.triage_result`. It does not read ranked candidates
for role selection, invoke the Finalizer, build a new triage value, query tools, or call a
model. `triage_result.primary` is a suspected hypothesis and is not confirmed root cause.
Old `authoritative_rca_result`, `root_cause`, `confirmed_impact_lots` and Confirmation
status are projected with their existing meaning. No State/DB write occurs on read.

Existing Job/detail route `GET /rca/jobs/{job_id}` returns the full typed value in its
State envelope. Existing result/report route `GET /rca/jobs/{job_id}/report` now additionally
returns optional typed `triage_result`. There is no invented new `/result` route. Both
omit the optional field for historical jobs without triage. The frozen RootModel JSON
schema remains identical as parsed structured JSON to the R1 reference, including its
description. The new report response is an API serialization envelope, not an authority
writer; its exact constructor is allowed by the writer-invariant test.

`report_for_view` handles already-saved R2.2 reports: when both a stored report and triage
exist, it renders the current presentation from the existing State, preserving stored
report ID and creation timestamp. Job/detail and report routes use that same pure view.
It never rewrites the stored report/State or upgrades a missing report; the existing 409
for a report not yet available remains. Without triage, stored historical report bytes
and titles remain unchanged.

## Web result structure and wording

The Investigation view puts the Copilot panel before diagnostics. It displays:

1. Investigation Triage / Primary Suspected Cause and Strongest Alternative.
2. Explicit comparison and equal visual weight for both hypothesis cards.
3. Original engineering statement, causal explanation, declared scope/time, cited
   supporting Evidence IDs plus observations/summaries and contradictory citations.
4. Remaining Uncertainty, grouped by its existing category, retaining every individual
   explanation, candidate association, evidence reference and scope.
5. Recommended Next Investigation, conditional outcomes and execution/approval context.
6. Per-candidate Evidence Assessment and separate internal Confirmation status.

`tied` displays “Tied — not distinguishable with current evidence.” The other statuses
have explicit evidence-preference, not-distinguishable or preference-not-established
labels. Rank position, card position or raw score never becomes confidence percentage.
All ten assessment dimensions display their existing status and observation state;
null means Not assessed. Unknown coverage is not a negative observation.
`not_acquired` displays “Evidence not yet acquired”. All eight uncertainty categories have
human-readable labels; no probable/plausible/weak overall grade is introduced.

An absent Alternative displays either “No valid alternative identified” or
“Alternative not investigated”. It never declares exclusion. No Primary displays
“No admitted primary hypothesis” while retaining uncertainty and the recorded action or
no-action reason. Two roles are rendered only if the authoritative object contains them.

Action recommendations show targets, requested data, scope/time, one conditional outcome
per target, executor, approval, executability and availability. Human/external-only actions
say they are recommendations for an engineer to perform. Even system-executable actions
are recommendation-only and have no execute/dispatch button. Null action always displays
`no_next_action_reason`.

Internal RCA root cause is displayed only from the old authoritative result, in a separate
Confirmation section. Internally supported does not become human engineering sign-off.
Every Copilot presentation retains “Engineering sign-off: Not recorded”. Historical
no-triage jobs retain the existing UI, including their original confirmed result view.
Model identity, workflow, raw ranking, raw Evidence and diagnostic scores are subordinate
collapsed detail areas for Copilot jobs. They do not establish a causal winner.

## Markdown / exported Report

The report begins with Investigation Triage: original roles, supporting and contradictory
Evidence, comparison, all per-dimension assessments, all uncertainties and the same
recommended investigation. Old confirmation is a separate section. Remaining investigation
records are labeled diagnostic context. For triage jobs, legacy candidate recommendations
and the old confidence/root-cause main sections are not presented as the Copilot handoff.
No original hypothesis text is re-authored; Markdown markup is escaped for display so
model prose cannot create an authority heading or executable HTML. Web performs equivalent
HTML escaping through React. Report references include the actual cited role/assessment/
uncertainty/discriminator Evidence IDs; no citations or domain observations are invented.

## FORMAL_005 offline fixtures

`tests/fixtures/copilot_r3/formal005_views.json` uses the two original Pilot State files and
R2.2's already-saved, validated triage projection. No fresh role selection/model/tool/GT
execution was used. It is a presentation fixture, not a new canonical RCA State/run. Source
artifact SHA-256 values are saved; original files remain unchanged. The fixture retains
original Evidence IDs and human-visible observation text, with runtime trace omitted.

| Saved result | Web/API/Report roles | Comparison | Old confirmed root cause |
|---|---|---|---|
| DeepSeek | Existing beam Primary + existing chuck Alternative | tied / not distinguishable | null |
| Qwen | Existing single composite hypothesis + null Alternative | preference not established | null |

Both show unsupported mechanism and unresolved alternative. DeepSeek shows the existing
engineer-only recommendation and conditional predictions. Qwen shows the original no-next-
action reason. No case ID, recipe, equipment, parameter or model identity is hard-coded in
production rendering logic.

## Scope, files and compatibility

API: `app.py`, optional report envelope in `schemas.py`, new `presentation.py`.
Core presentation: `report_generator.py`, new `triage_report.py`.
Web: `App.tsx`, `types.ts`, `styles.css`, new `TriagePanel.tsx` and `triagePresentation.ts`.
Tests: new frontend/API/report cases and fixtures; exact R3 presentation hashes, with
previous R1/R2.1/R2.2 fixtures preserved. R1's no-production-writer assertion now recognizes
only the typed report response serializer, and its formerly byte-identical triage report
assertion is updated to require old authority/confirmation content plus the R3 presentation.
The historical no-triage report byte check remains.

No Candidate, Competition, Ranking, Confirmation, Finalizer writer/selection, Evidence
Flow, Prompt, Planner, repair/retry, Provider/model, scorer or GT change. No dependency/
lock-file change. No DB migration: the existing JSONB already stores optional triage.
Validation, protected-content hashes and delivery scope are documented separately.

## R4 minimum recommendation (not implemented)

Freeze a separate Copilot Benchmark/version and scoring contract, preserving
`development-rca/1.1` as the original autonomous-confirmation baseline. Measure Primary
Hypothesis Hit, Top-2 Recall, Evidence Grounding, Unsupported Claim Rate, Alternative
Quality, Next-Action Discrimination Quality and Overclaim Rate. Keep terminal categories
and calibration separate; root-cause confirmation is an independently reported legacy
metric. Define unknown-expression/engineering-action adjudication before any new paid
execution, reuse existing telemetry/cost records, and begin with offline saved-state
scoring/fixtures. No repeats, new providers or paid runs are authorized by R3.
