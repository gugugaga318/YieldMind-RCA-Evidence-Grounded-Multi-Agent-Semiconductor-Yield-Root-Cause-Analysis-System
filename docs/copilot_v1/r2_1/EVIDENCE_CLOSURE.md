# RCA Investigation Copilot v1 — R2.1 Evidence Closure

Date: 2026-10-14, Asia/Hong_Kong.
R1 commit: `e87e65474101ef7796c2a9ff611a03e063ee32d9`
(`feat: define copilot triage authority contract`).
R2.1 is a separate, uncommitted patch. No R2.2 writer or real provider call.

## Confirmed failures and minimal boundary

| Observed failure | Earliest confirmed cause | Patch boundary |
|---|---|---|
| Parameter coverage reached a second recipe lane while its cohort outcomes did not | Supervisor constructed a bounded source-Lot + selected-lane query; no second-lane outcome action occurred. Tool correctly returned requested rows. Synthesis exposed an empty outcome list without acquisition coverage | Preserve existing action/query scope; attach quality-tool query receipts and show per-lane outcome acquisition coverage |
| Defect and spatial facts could be paired incorrectly | `SummarizeDefectWatTool` selected dominant defect and dominant pattern independently across all rows; `location_region` was omitted from typed Evidence | Select pattern conditional on the emitted defect; preserve same-row spatial associations |
| Missing observations could be read as normal | No typed acquisition receipt distinguished query scope, returned records, and observed normal rows | Validated observation statuses/counts; prevent receipt-bearing negative Evidence without normal rows |
| Existing parameter summary looked like completed hypothesis discrimination | ActionValue matched existing parameter Evidence and suppressed the acquisition action without distinguishing analysis capability | Preserve non-repeat eligibility; explicitly record analysis not established / engineer evidence required |

Upstream: public repository rows and trusted tool query context.
Downstream: typed Evidence, State serialization, bounded synthesis, Candidate/Competition inputs,
existing causal matrix/Confirmation consumers, ActionValue diagnostics and API projection.
Touched invariants: INV-002/003/004/005/006/011/012/015/018/019/020.

No changes to Planner action construction, Candidate generation/repair, Challenge,
Competition requirements/termination, Ranking, Confirmation supported thresholds,
Finalizer selection/publication, Prompt text, provider retry, scorer or GT.

## Observation coverage contract

`outcome_observation_receipt`, version `outcome-observation/1.0`, is written only
from successful quality-tool acquisition. It includes request identity, queried
Lots, source availability, `time_filter_applied=false`, and exactly one record
per queried Lot × source table (`defect_summary`, `wat_result`, `metrology_result`).
Every record carries status, returned/normal/abnormal counts, and observed time bounds.
Repeated Lot IDs are deduplicated in the receipt; mixed WAT/metrology controls
retain independent per-domain observation status.
It is metadata on the original immutable Evidence and also tool output data;
it does not create Evidence IDs or add normal Lots to positive Evidence entities.

| Observation status | Meaning |
|---|---|
| `not_acquired` | No quality query receipt covers the compared Lot; surfaced in lane coverage, not forged as a tool result |
| `not_observed` | Source query succeeded but no matching row was returned; does not prove a normal outcome |
| `observed_normal` | Returned rows explicitly establish normal status, with normal count equal to record count |
| `observed_abnormal` | At least one explicit abnormal record exists; mixed passing controls remain visible via counts |
| `observed_unclassified` | Rows exist but do not establish normal/abnormal status |
| `unavailable` | Explicit unavailable source, distinct from successful no-match |

`not_observed` is a valid no-match acquisition result. It does not mean the source
failed. Existing legacy `DATA_MISSING` Evidence and Gate handling are unchanged;
the new receipt removes the ambiguity between missing matching records and source
availability. This patch does not invent a new provider/tool error recovery path.

Lane synthesis adds `outcome_observation_coverage`; mechanism bridge and Competition
brief receive bounded coverage summaries. Overall acquisition status is
`acquired`, `partial`, `not_acquired`, `unavailable`, or `unknown` (legacy receipts
absent, or comparison scope absent). Counts use the **full** exposed-Lot cohort,
not its truncated display prefix. Missing/unqueried Lots and projection omissions
are explicit. Receipts are deduplicated by request + query scope + observations,
not request ID alone. Repeated query observations remain traceable.

Coverage is an acquisition fact only: it does not certify recipe, process operation,
causal linkage, or temporal compatibility. A source-Lot row queried with L1 can
count as acquisition for that Lot in L2, but Evidence is never rebound to another
lane and no candidate citation is added. Explicit lane IDs remain authoritative.

## Actual FORMAL_005 public-data check

The audit replayed only historical quality-tool queries, without models or GT.
Both provider records contained source-Lot and source-Lot + L1-cohort acquisition.
L2 had partial acquisition: `LOT_6639775AE3` and `LOT_86EFEE41F9` were not queried.
The public L2 cohort contains 63 defect rows and 63 WAT rows, including E52 with
`location_region=STRIPE` and failed WAT `VT_SHIFT`. STRIPE is a location field;
it must not be substituted for anonymized `pattern_type` P-codes.

The new receipt/projection exposes this gap. It deliberately does not add a
hidden cross-lane tool call or expand budgets. Explicitly querying that other
cohort returns its existing outcomes, as the generic synthetic counterexample
proves. Both original query replays retained exactly the old Evidence ID sets.

Machine-readable audit:
`outputs/copilot_r21_validation/formal005_public_tool_replay.json`.
Old Pilot run records and Scorecard remain untouched; this is not a new Pilot run.

## Spatial association

For every source row of the emitted dominant-defect Evidence, metadata now keeps:
`defect_type`, `pattern_type`, `location_region`, Lot, Wafer,
`inspection_operation_no`, `inspected_at`, defect count/density,
`source_table`, and SHA-256 of the complete source row.
Missing operation/location remains null; inspection operation is not inferred to
be the causal process operation. No equipment/chamber/recipe is invented.

`dominant_pattern` is calculated only among rows for that defect. Existing global
`defect_counts`/`pattern_counts` are marginal histograms, not proof of conditional
association. Non-dominant defects are not turned into new positive Evidence IDs.
This is a bounded aggregation correction, not a replacement of Evidence Synthesis.

Full associations remain in State. Model-facing cards retain up to three whole
source observations plus total/omitted counts. Query receipts are projected as
small status counts by source with provenance; lane coverage includes at most four
observation samples. Existing Lane/ID/payload caps remain intact. Samples are not
complete source coverage, and omission counts make this explicit.

## Unknown cannot become negative authority

Receipt validation enforces status/count/source consistency and complete query
coverage. Receipt-bearing `negative_signal` Evidence must have an actual normal
row for each bound Lot in the relevant source table. Empty observations cannot be
retyped as normal by changing a label or prose. Old Evidence without receipts
continues to deserialize without backfill.

Specialist free text remains interpretation. An adversarial fake-client test says
“All outcomes are normal” while tool results contain no rows: authoritative typed
Evidence remains `data_missing` with `not_observed` receipt records. This patch
cannot fully police engineering prose; it prevents prose from overwriting the
structured observation authority. Candidate/Gate validation rules are unchanged.

## Acquisition versus discrimination

ActionValue retains `already_available_evidence_ids` and adds optional:

- `discriminative_analysis_status = not_established`
- `discriminative_analysis_executability = not_assessed | engineer_evidence_required`

For mechanism/parameter-anomaly requests with existing FDC summary Evidence,
`inspect_fdc_spc` still cannot execute a paired hypothesis-prediction/outcome
analysis. It is ineligible for that purpose, with
`rejection_reason=discriminative_analysis_not_executable` and
`engineer_evidence_required`, instead of treating available summaries as finished
analysis. Ordinary tool execution/exact retries remain governed by existing rules.
No new high-value option, budget consumption, Competition completion or terminal
status is synthesized. Other acquisition observations retain their factual
“Evidence available” classification while analysis remains `not_established`.

There is no trusted paired-analysis result producer in R2.1, so callers cannot
serialize `performed` merely from model prose. A future capability would need
explicit hypotheses/predictions, outcome linkage, scope/time, provenance and a
comparison result. This batch does not implement that capability or a new planner.
API keeps these optional fields via its existing dictionary projection; old
assessments omit them and remain round-trip compatible.

## Files and validation

Production changes:

- `core/yield_rca_core/outcome_observation.py` (new typed observations, receipts/projections)
- `core/yield_rca_core/tool_layer.py` (same-row spatial association, tool-owned receipts)
- `core/yield_rca_core/evidence_models.py` (receipt/negative-state validation)
- `core/yield_rca_core/evidence_synthesis.py` (bounded coverage/association projection)
- `core/yield_rca_core/causal_investigation_models.py` (optional action diagnostics)
- `core/yield_rca_core/investigation_decision.py` (honest discrimination capability reason)

Tests/fixtures:

- `tests/contract/test_copilot_r21_evidence_closure.py`
- `tests/contract/test_patch4_mechanism_identity_contracts.py`
- `tests/copilot_evidence_closure_support.py`
- `tests/integration/test_copilot_r1_parity.py`
- `tests/integration/test_provider_workflow_baseline.py`
- `tests/fixtures/copilot_r21/evidence_closure_payload_hashes.json`

See [VALIDATION.md](VALIDATION.md), [CHANGE_IMPACT_REVIEW.md](CHANGE_IMPACT_REVIEW.md),
and [protected_sources.json](protected_sources.json).

No hardcoded case ID, recipe ID, lot ID, beam/chuck parameter name, E52 or VT_SHIFT
appears in the implementation or synthetic regression fixtures. FORMAL_005 names
above describe evidence from the public-data audit, not production branches.
The contracts apply to every quality-tool query and compared recipe/lane cohort.

## R2.2 minimum recommendation — not implemented

Implement one Python Finalizer triage writer against accepted `triage/1.0` only:
select up to two admitted, materially distinct candidates using existing ranking;
preserve authored hypothesis text, grounded citations, comparison uncertainty and
remaining gaps. Project an evidence request for an engineer when discrimination
is not executable. Keep `authoritative_rca_result`/confirmed impact unchanged;
never backfill suspected Primary into confirmed root cause. Test 0/1/2 candidates,
ties, missing alternative, and historical compatibility before any UI work.

This patch requires no DB migration. `triage_result` remains absent on current
workflow paths. Stop here; R2.2 needs a separate reviewed change.
