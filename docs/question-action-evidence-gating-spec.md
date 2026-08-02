# Question-Action-Evidence Semantic Gating Specification

## Status

- Status: Proposed
- Target branch: `feature/autonomous-qwen-react`
- Proposed delivery: Batch 21
- Depends on: Batch 20.9 autonomous Qwen ReAct and QuestionUpdate review
- Scope of this document: specification only; no runtime behavior changes yet

## Goal

Add the missing semantic boundary between an investigation Question, the Agent
Action selected by Qwen, and the Evidence used to answer that Question.

The resulting system must preserve Qwen's authority to choose the investigation
order while preventing it from:

- Calling an Agent that cannot contribute to the targeted Question.
- Executing an Action that cannot fill a current Evidence gap.
- Using real but unrelated Evidence to close a Question.
- Replacing an unavailable user-requested capability with a fabricated answer
  from another data source.
- Repeating a direction that produces no relevant Evidence gain.

This feature is a capability and evidence gate. It is not a new deterministic
Workflow and does not prescribe one fixed Agent sequence.

## Motivation

The Batch 20.9 live-Qwen reliability lane passed three consecutive Scratch + Cu
CMP runs on `llm_react`. The state machine correctly rejected non-terminal and
close-and-target QuestionUpdate claims without losing legal Agent Actions.

The same live results exposed a narrower semantic weakness: a model may reuse a
broad answer and the same existing Evidence IDs for several different Questions,
including Questions whose data source is not available. Existing validation
proves that an Evidence ID exists; it does not yet prove that the Evidence can
answer the referenced Question.

## Non-goals

Batch 21 will not:

- Add a new Agent or industrial data source.
- Implement a Material Agent or material genealogy Tool.
- Replace Qwen with a deterministic next-action policy.
- Allow an LLM judge to override hard Evidence rules.
- Change the existing Hypothesis Engine or approval-gated Memory write boundary.
- Remove `fixed` or `controlled_react` compatibility modes.
- Add a weighted evaluation score or a large set of new public metrics.
- Let Agents query Repositories directly or generate Evidence IDs.

## Authority Boundary

Qwen remains responsible for:

- Interpreting the user objective.
- Selecting supported Question kinds that serve the active Goal.
- Choosing one compatible next Action and its target Questions.
- Explaining the expected investigative value.
- Proposing Question updates, a stop, and a conclusion level.

Python remains responsible for:

- Declaring which capabilities and data sources exist.
- Validating Question kind, Action compatibility, scope, prerequisites, and
  expected Evidence gain.
- Executing Tools and owning first-class Evidence.
- Linking Evidence to Questions using deterministic rules.
- Reviewing Question closure and unavailability claims.
- Enforcing budgets, no-gain boundaries, conclusion gates, and fallbacks.

## User-requested Unsupported Capabilities

The system must distinguish a user request from a Question invented by Qwen.

### User-requested capability

When the user explicitly requests a capability that is not configured, the
system must return a typed capability notice instead of substituting another
Agent's Evidence.

Examples include material batch, supplier, or consumable genealogy while no
Material Tool is installed.

The notice contains:

```text
capability
supported=false
reason
available_alternatives
request_source=user
```

Admission behavior:

- If the complete request is unsupported, no investigation Agent or Tool runs.
- If the request mixes supported and unsupported objectives, supported work may
  continue and the unsupported part remains visible as a capability notice.
- The unsupported part must never be silently answered from MES, FDC,
  Defect/WAT, Knowledge, or RCA reasoning Evidence.

The exact HTTP adapter status is not part of the core contract. The API and
frontend must expose the same typed notice and plain-language explanation.

### Qwen-created capability

If Qwen creates a new Question whose kind is unsupported and the user did not
request it, the candidate decision is invalid. Python returns
`unsupported_question_kind` as validation feedback. The Question is not
committed to `RCAState`.

## Question Contract

### QuestionKind

`InvestigationQuestion` gains a required `question_kind` field for new output.
The initial enum is intentionally bounded:

```text
defect_signature
impact_scope
spc_signal
process_mechanism
product_outcome
historical_match
tool_history
recipe_history
metrology_correlation
material_trace
```

`material_trace` is a recognized user capability but is unsupported until a
Material Tool and corresponding Evidence types are configured. Recognition is
different from implementation support.

Question text remains natural language. Runtime routing must use
`question_kind`, never keyword matching over Question text.

### Backward compatibility

Legacy snapshots without `question_kind` remain readable. A deterministic
legacy adapter infers known kinds from the stable Question ID suffixes used by
the existing Intent Planner. Unknown legacy Questions map to an internal
`unsupported` compatibility classification and must not be autonomously
replanned until explicitly migrated.

New serialization always writes `question_kind`. Qwen cannot change the kind of
an existing Question through `QuestionUpdate`.

## Capability Registry

A new Python-owned `QUESTION_CAPABILITY_REGISTRY` is the single authority for
Question-to-Action-to-Evidence relationships.

Each `QuestionCapabilityDefinition` declares:

```text
question_kind
supported
direct_actions
supporting_actions
accepted_evidence_types
closure_evidence_groups
action_contributions
unsupported_reason
available_alternatives
```

`action_contributions` maps each compatible Action to the Evidence groups it is
expected to fill. It is a pre-execution capability declaration, not proof that
the Action actually produced useful Evidence. The post-execution resolver must
confirm real gain.

For example:

```text
process_mechanism.action_contributions:
  inspect_defect_pattern -> product_signal
  validate_shared_defect_pattern -> product_signal
  find_shared_exposure -> shared_exposure
  inspect_fdc_spc -> process_anomaly
  validate_historical_case -> historical_context
  run_rca_reasoning -> hypothesis_synthesis
```

### Direct and supporting Actions

A direct Action can directly produce or synthesize information needed to answer
the Question. A supporting Action can fill one prerequisite Evidence group but
cannot close the Question by itself.

This distinction prevents an overly rigid one-Action-per-Question Workflow.

### Initial capability matrix

| Question kind | Direct Actions | Supporting Actions | Accepted Evidence types / groups |
| --- | --- | --- | --- |
| `defect_signature` | `inspect_defect_pattern`, `validate_shared_defect_pattern` | None | `defect_signal`, `electrical_failure`, `metrology_deviation`, explicit quality `negative_signal` |
| `impact_scope` | `find_shared_exposure` | `validate_shared_defect_pattern` | `impact_scope`, `lot_context`, `process_exposure`, `equipment_exposure`, `excursion_window` |
| `spc_signal` | `inspect_fdc_spc` | `find_shared_exposure` when process context is missing | `parameter_deviation`, `trend_deviation`, `spc_violation`, `ooc_event`, `excursion_window` |
| `process_mechanism` | `inspect_fdc_spc`, `run_rca_reasoning` | `inspect_defect_pattern`, `find_shared_exposure`, `validate_shared_defect_pattern`, `validate_historical_case` | process anomaly + product signal + shared exposure; historical Evidence is contextual only |
| `product_outcome` | `inspect_defect_pattern`, `validate_shared_defect_pattern` | `find_shared_exposure` | `electrical_failure`, `defect_signal`, `metrology_deviation`, explicit quality `negative_signal` |
| `historical_match` | `validate_historical_case` | Current MES/FDC/Defect context collection | `historical_case_match`; missing source data may prove unavailability |
| `tool_history` | `find_shared_exposure` | `inspect_fdc_spc` | `lot_context`, `process_exposure`, `equipment_exposure`, `recipe_change`, `hold_event` |
| `recipe_history` | `find_shared_exposure` | None in the initial LLM Action allowlist | `lot_context`, `recipe_change`; missing records may prove unavailability |
| `metrology_correlation` | `inspect_defect_pattern` | `validate_shared_defect_pattern` | explicit `metrology_deviation` or source-specific `negative_signal` |
| `material_trace` | None | None | Unsupported until a Material data source and Tool exist |

The registry must use actual `EvidenceType` values already produced by Tools.
An Evidence summary containing a convenient phrase does not change its type or
capability.

## Action Compatibility Gate

The hard Action gate runs after structured PlannerDecision parsing and before
Specialist dispatch.

For every `target_question_id`, Python performs the following checks in order:

1. The Question exists, belongs to the active Goal, and is open.
2. Its `question_kind` is supported.
3. The selected Action is listed as a direct or supporting Action.
4. The Action can contribute at least one currently missing Evidence group, or
   can resolve an active contradiction.
5. Action inputs and scope remain compatible with the Question scope and the
   protected source Lot.
6. Existing prerequisite, duplicate, registry, and budget checks still pass.

### Atomic multi-target rule

If one Action targets multiple Questions, it must be compatible with every
target. One incompatible target invalidates the complete PlannerDecision.
Python must not silently delete or rewrite a target selected by Qwen.

No Agent runs, no Tool runs, and no Evidence or Decision is committed after a
hard gate rejection. Qwen receives one bounded repair attempt through the
existing structured-output retry path.

### Stable rejection reasons

Batch 21 adds these core validation reasons:

```text
unsupported_question_kind
action_question_mismatch
action_scope_mismatch
no_expected_evidence_gain
```

These are core Decision errors, not ancillary QuestionUpdate review outcomes.
Two invalid core outputs retain the existing controlled compatibility handoff.

## Question-Evidence Link Contract

Evidence remains an immutable fact. Its usefulness to one Question is stored as
a separate relation because one Evidence item may support several Questions.

`QuestionEvidenceLink` contains:

```text
question_id
evidence_id
action_id
relation
matched_evidence_group
reason
```

Allowed relations are:

```text
supports
contradicts
context
unavailable
```

The link must reference an existing Question, Evidence item, and ActionRecord.
It must preserve source-Lot and scope boundaries.

Links are first-class, typed records in `RCAState`, API serialization, and the
frontend Agent trace.

## Deterministic Evidence Applicability

A Python `QuestionEvidenceResolver` creates links after a Specialist returns a
valid Finding and before the next Planner observation.

The resolver uses only:

```text
QuestionKind
ActionKind
EvidenceType
Evidence entities and scope
Question scope
Finding and ActionRecord references
```

It does not call an LLM. Qwen and Specialist analysis may explain why Evidence
is useful, but cannot create, remove, or override a hard link.

An Evidence item is applicable only when:

- Its type is accepted by the Question capability.
- The producing Action is a compatible direct or supporting Action.
- Its Lot, product, equipment, chamber, operation, or time scope does not
  contradict the Question scope.
- It contributes to a required Evidence group, records a contradiction, gives
  necessary context, or explicitly proves data unavailability.

All Evidence remains in `RCAState` for audit. Irrelevant Evidence simply has no
link to that Question.

## Question Closure Rules

Question status and conclusion confidence remain separate concepts.

- `closed` means the Question received an evidence-backed answer at the current
  conclusion level.
- `unavailable` means the required data cannot be obtained and that absence is
  explicit.
- `ConclusionLevel` continues to express `signal`, `candidate`, `supported`,
  `conflicted`, or `inconclusive` strength.

A candidate answer may close a Question while the overall conclusion remains
`candidate`. It must not be presented as supported.

### Closed review

A `closed` QuestionUpdate is accepted only when:

- Every cited Evidence ID exists.
- Every cited Evidence ID has an applicable link to that Question.
- The required closure Evidence groups are satisfied.
- Any critical contradiction is represented and handled.
- The answer does not claim a stronger conclusion than the existing
  Evidence/Hypothesis gate permits.

For `process_mechanism`, closure requires at least:

```text
one process-anomaly group
+ one product-signal group
+ one shared-exposure group
```

A `supported` root-cause conclusion additionally requires a supported
Hypothesis and no unresolved critical contradiction. FDC deviation alone may
produce a mechanism signal or candidate but cannot confirm the root cause.

### Unavailable review

An `unavailable` QuestionUpdate requires:

- A non-empty reason.
- An applicable `DATA_MISSING` Evidence item or a typed unsupported-capability
  notice for a user-requested capability.
- No fabricated substitute answer.

### New ancillary review reasons

```text
evidence_not_applicable
insufficient_evidence_coverage
unsupported_capability
missing_unavailability_evidence
```

A rejected ancillary update still preserves a legal core Action, consistent
with the existing QuestionUpdateReview boundary.

## Relevant Planner Context

The full state remains auditable, but the Next-action Planner receives a
Question-oriented projection instead of an undifferentiated Evidence list.

Each open Question packet contains:

```text
question_id and question_kind
scope
linked supporting, contradicting, contextual, and unavailable Evidence IDs
satisfied Evidence groups
missing Evidence groups
compatible direct and supporting Actions
prior attempted Actions for the same Question and scope
```

The Planner also receives a bounded global summary of critical contradictions,
Hypotheses, budgets, and completed Actions. It does not receive unrelated raw
Evidence merely because that Evidence exists elsewhere in the investigation.

This projection reduces context growth without deleting audit data.

## Evidence Gain and No-gain Boundary

The existing public metric set remains unchanged.

`evidence_gain` is refined for `llm_react` Actions: a collecting Action gains
Evidence only when it creates at least one new applicable link for a targeted
Question, or resolves a relevant contradiction. A new but irrelevant Evidence
ID does not count as investigative gain.

RCA reasoning may still report `evidence_gain=false` and `redundant=false` when
it adds a valid synthesis without inventing Evidence.

No-gain behavior is bounded:

1. The first no-gain Action is recorded and Qwen may replan.
2. A second consecutive no-gain proposal may not continue the same Question,
   Action family, and scope.
3. Qwen must switch to another compatible gap or stop.
4. If no compatible useful Action remains, the run stops with
   `no_allowed_action`, `data_unavailable`, or `budget_exhausted` and the
   conclusion is downgraded appropriately.

The existing `decision_valid`, `evidence_gain`, `redundant`, `goal_success`, and
`stop_correct` metrics remain the only headline metrics. Question-Evidence
consistency becomes a required condition inside `decision_valid` and
`goal_success`, not a sixth score.

## Supervisor Commit Boundary

The `llm_react` commit order becomes:

```text
validate PlannerDecision
validate Question-Action compatibility
dispatch Specialist
validate Finding and Evidence closure
record Finding and ActionRecord projection
resolve QuestionEvidenceLinks
review QuestionUpdates against links and closure rules
atomically commit Decision, ActionRecord, Finding, Evidence, links, and reviews
```

Any failure before the atomic commit exposes the prior immutable state and must
not leave a dangling Decision, ActionRecord, link, or Question update.

## Compatibility Boundary

- `fixed` remains the historical regression baseline.
- `controlled_react` retains its deterministic selection policy.
- Semantic links may be generated additively for those modes, but Batch 21 hard
  Action compatibility affects only `llm_react` until release acceptance passes.
- Existing action prerequisites, budgets, Evidence/Hypothesis gates, RAG,
  Memory approval, and provider fallback rules remain unchanged.
- A mid-loop Qwen fallback preserves all already committed links and continues
  from the current state.
- Legacy snapshots remain readable. New autonomous replanning requires migrated
  Question kinds and links or an explicit compatibility handoff.

## API, Report, and Frontend Surface

The product surface adds:

- Capability notices for unsupported user requests.
- `question_kind` on each Question.
- QuestionEvidenceLinks grouped under each Question.
- Hard-gate rejection reason in Planner fallback diagnostics.
- Closure-review reasons for rejected QuestionUpdates.
- Satisfied and missing Evidence groups in the Agent trace.

The default UI remains an engineering story. Detailed link contracts and
registry diagnostics stay collapsed behind technical details.

The report must state unsupported requested capabilities and unavailable data
without inventing a substitute answer.

## Security and Data Handling

- No prompt, raw Qwen response, API key, or provider credential is stored in a
  capability notice or QuestionEvidenceLink.
- Reasons are bounded plain text produced by Python templates.
- Qwen cannot write registry definitions, capability support, links, or
  Evidence groups.
- Agents remain isolated from Repositories and databases behind registered
  Tools.

## Acceptance Criteria

### Contract and compatibility

- New Question kinds and QuestionEvidenceLinks strictly round-trip.
- Legacy Questions without a kind remain readable through deterministic
  migration.
- Existing fixed and controlled regression baselines remain unchanged.

### Unsupported capability

- A material-only user request returns a clear unsupported-capability notice
  and calls no investigation Agent or Tool.
- A mixed root-cause plus material request completes supported work and exposes
  the unsupported part.
- A Qwen-created unsupported Question is rejected and never committed.

### Action gate

- `inspect_fdc_spc` targeting `material_trace` is rejected before Tool dispatch.
- A compatible FDC Action targeting `spc_signal` or `process_mechanism` remains
  legal when it can fill a missing process-anomaly group.
- One incompatible target in a multi-target Decision rejects the complete
  Decision.
- Scope mismatch and no-expected-gain failures produce stable diagnostics and
  no state mutation.

### Evidence and closure

- Existing but unrelated Evidence cannot close a Question.
- FDC Evidence cannot close `material_trace`.
- Missing material capability cannot be converted into a fabricated closed
  answer.
- A mechanism candidate may close the Question at candidate level, but cannot
  produce a supported conclusion without a supported Hypothesis.
- A supported Scratch + Cu CMP root cause requires process anomaly, product
  signal, shared exposure, and the existing Hypothesis gate.

### Replanning and stopping

- The Planner receives relevant Evidence and compatible Actions per open
  Question.
- Different intents still produce different Action chains.
- The first relevant observation triggers replanning.
- Two consecutive no-gain directions cannot loop indefinitely.
- Stop correctness remains evidence-, contradiction-, availability-, and
  budget-aware.

### Live Qwen release lane

- The existing three-run Scratch + Cu CMP reliability lane remains 3/3 on
  `llm_react`, within the per-run call cap, or any failure is attributed by the
  existing diagnostics.
- A new semantic acceptance scenario proves that unrelated broad Evidence is
  rejected for `material_trace` and cannot raise the final conclusion level.

## Proposed Delivery Sequence

Each segment must be a separate reviewable commit. The user performs the push.

### Batch 21.0 - Question Capability + Action Gate

- Add QuestionKind, capability notice, and deterministic legacy readers.
- Add the Python-owned Question Capability Registry.
- Return a clear capability notice for unsupported user requests.
- Reject unsupported Questions created by Qwen.
- Filter advertised Actions per open Question.
- Enforce atomic Question-Action and scope compatibility before Specialist or
  Tool dispatch.
- Reject a multi-target Decision when any target is incompatible.
- Add strict contract, compatibility, and pre-dispatch state-mutation tests.

This segment proves that an incompatible Action cannot execute, call a Tool, or
change Evidence. Expected Evidence gain based on actual QuestionEvidenceLinks
is deferred to Batch 21.1.

### Batch 21.1 - Question-Evidence Gate + Bounded Replanning

- Add and persist QuestionEvidenceLink contracts in RCAState.
- Generate deterministic links after Specialist output.
- Enforce applicable-Evidence and closure-group rules in QuestionUpdate review.
- Preserve the existing legal-Action/ancillary-rejection boundary.
- Build Question-oriented Planner context containing linked Evidence, satisfied
  groups, missing groups, and compatible Actions.
- Enforce `no_expected_evidence_gain` using the actual missing groups.
- Refine `evidence_gain` to relevant link gain.
- Enforce the two-step no-gain boundary and correct downgraded stops.
- Integrate semantic consistency into the existing five evaluation metrics.

This segment proves that real but unrelated Evidence remains auditable without
closing the wrong Question or steering the next Planner context.

### Batch 21.2 - Product Surface + Final Semantic Evaluation

- Expose capability notices, Question kinds, links, satisfied/missing groups,
  and review diagnostics through the API.
- Render the same relationships in Agent Trace and reports.
- Preserve the existing default engineering view and compatibility timelines.
- Run full Python/frontend regressions, deterministic autonomous evaluation,
  fixed and controlled baselines, semantic negative cases, and the explicitly
  approved live-Qwen reliability lane.
- Prove that `material_trace` cannot be closed with FDC Evidence and that the
  supported Scratch + Cu CMP path still passes its Evidence/Hypothesis gates.

## Expected File Areas

Likely production changes:

```text
core/yield_rca_core/investigation_models.py
core/yield_rca_core/models.py
core/yield_rca_core/intent_planner.py
core/yield_rca_core/investigation_policy.py
core/yield_rca_core/next_action_planner.py
core/yield_rca_core/question_update_review.py
core/yield_rca_core/supervisor.py
core/yield_rca_core/decision_evaluation.py
core/yield_rca_core/prompts/intent_planner_v1.md
core/yield_rca_core/prompts/next_action_planner_v1.md
backend/yield_rca_api/*
frontend/src/*
```

New core modules may include:

```text
core/yield_rca_core/question_capability.py
core/yield_rca_core/question_evidence.py
```

Tests must cover contract, unit, integration, API, frontend, deterministic
evaluation, compatibility, and opt-in live-Qwen lanes.

## Completion Definition

Batch 21 is complete only when the system can demonstrate all of the following:

- Qwen still chooses observation-driven Agent paths instead of running every
  Agent.
- Unsupported user capabilities are stated honestly and never answered through
  unrelated data.
- An incompatible Question-Action pair cannot execute or change Evidence.
- A real but unrelated Evidence ID cannot close a Question.
- A legal Action with an invalid ancillary update still preserves its useful
  Finding and Evidence.
- Root-cause strength remains controlled by the existing Evidence/Hypothesis
  gate.
- No-gain replanning is bounded.
- Fixed and controlled compatibility baselines remain available.
- Every accepted or rejected semantic claim remains visible in state, API,
  report, frontend trace, and executable tests.
