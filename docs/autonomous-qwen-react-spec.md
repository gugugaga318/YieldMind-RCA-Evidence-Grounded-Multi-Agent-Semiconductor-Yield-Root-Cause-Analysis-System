# Autonomous Qwen ReAct Specification

## Status

- Target branch: `feature/autonomous-qwen-react`
- Model provider: DashScope
- Model: `qwen-plus`
- Current implementation stage: Batch 20.9.8 QuestionUpdate Review Reliability, stage 3 complete

## Goal

Add an autonomous, evidence-aware Planner that can choose the next specialist
action after every observation. The Planner should produce different action
chains for different user goals instead of always executing every specialist.

The existing fixed workflow and controlled ReAct workflow remain compatibility
modes and regression baselines.

## Runtime Modes

The final system will expose three explicit modes:

- `fixed`: execute the existing fixed task plan.
- `controlled_react`: use the existing deterministic next-action policy.
- `llm_react`: let Qwen choose the next registered action or stop after each
  observation.

`llm_react` is selectable in Fake and LLM Agent modes. Deterministic Agent mode
fails fast when configured with `llm_react`, because it has no Qwen client.

## Intent Planner

`QwenIntentPlanner` converts the user request into one `IntentPlan` containing:

- One immutable `InvestigationGoal`.
- Between one and five initial open `InvestigationQuestion` objects.

Python fixes the Goal ID, explicit Lot ID, maximum action budget, and Tool-call
budget. Qwen may interpret the requested intent and add facts stated directly
by the user, but it cannot assert a root cause, affected Lots, impact Lots, or
a hypothesis during intent planning.

Invalid structured output is sent back to Qwen once as validation feedback. A
second invalid output raises a typed error carrying
`fallback_mode=controlled_react`. The runtime catches that signal and starts
the deterministic policy from the initial Goal.

## Next-action Planner

`QwenNextActionPlanner` receives the current Goal, Questions, compact Findings
and Evidence, Hypotheses, Action History, and remaining budget. It returns one
strict `PlannerDecision` after every observation.

Only actions with a real Supervisor dispatcher are advertised. Batch 20.9.3
supports defect inspection, shared-defect validation, MES shared-exposure
analysis, FDC/SPC inspection, historical-case validation, and RCA reasoning.
In `llm_react`, Specialist actions are dispatched through Specialist V2. The
other runtime modes retain the existing deterministic Specialist paths.

Invalid output is returned to Qwen once with the validation error. If the
second output is still invalid, the current Findings, Evidence, Questions, and
Action History are retained and the deterministic policy resumes from that
state. The fallback does not restart the investigation.

The maximum action and Tool-call budgets are Python runtime boundaries.
Duplicate `Action + Scope` attempts, source-Lot replacement, missing Finding
prerequisites, and unregistered Agent/action combinations are rejected.

## Authority Boundary

Qwen is responsible for:

- Interpreting the user objective.
- Expressing missing evidence as engineering questions.
- Choosing one next registered Agent action.
- Explaining why that action is useful.
- Deciding when to stop.
- Proposing a conclusion level for later validation.

Python is responsible only for runtime safety:

- Strict structured-output validation.
- Action Registry validation.
- Duplicate `Action + Scope` rejection.
- A maximum of eight cross-domain actions.
- Specialist-local Tool limits.
- One Qwen parse retry.
- Explicit fallback to `controlled_react` after the retry fails.

The Evidence/Hypothesis Gate remains authoritative for the final conclusion
level. It may downgrade a Planner proposal, but it does not choose the next
action.

## Objective and Question Boundary

The user request creates an `InvestigationGoal`. Qwen may create new
`InvestigationQuestion` objects only when they directly support that same
`goal_id`.

An impact Lot is an investigation result, not a new objective. Discovering
impact Lots must not automatically create a full RCA sub-investigation for
every Lot. A new action is allowed only when it answers an open question for
the original goal and its `Action + Scope` has not already run.

Question status is intentionally small:

- `open`: evidence is still needed.
- `closed`: an answer and supporting Evidence IDs exist.
- `unavailable`: the data cannot be obtained and the reason is explicit.

Planner updates use a smaller `QuestionUpdate` delta rather than asking Qwen to
copy the complete Question. It contains only `question_id`, terminal `status`,
`answer`, `evidence_ids`, and `unavailable_reason`:

- `open` is never a legal update. Partial progress remains in Finding/Evidence
  and uses `question_updates=[]`.
- `closed` requires a non-empty answer and existing Evidence IDs.
- `unavailable` requires an explicit reason and cannot contain an answer.
- Evidence for the overall Goal cannot close an unrelated Question. If the
  requested records are explicitly missing or absent, the Question must be
  `unavailable`, not `closed` with a negative answer.
- An action cannot close and target the same Question in one decision.
- Qwen cannot copy or rewrite `goal_id`, Question text, rationale, or scope.

The state reader projects legacy full-Question updates into the compact delta so
old snapshots remain readable. New Qwen output is validated against only the
compact format, and serialization always writes the compact format.

`QuestionUpdateReview` separates update claims from the executable core
decision. The adapter accepts supported terminal deltas and rejects malformed,
non-terminal, duplicate, unknown, already-terminal, unsupported-Evidence, or
close-and-target claims. Rejection never changes the selected Agent, Action,
target, scope, or reason; it only prevents the Question status claim from being
committed. Each result has an accepted/rejected disposition and stable reason
code inside a typed `PlannerDecisionOutcome`.

The Supervisor uses `decide_with_review` for `llm_react`; the existing `decide`
method remains the strict compatibility path for callers that require all-or-
nothing parsing. Decision, accepted updates, Review records, Finding, and
ActionRecord cross the immutable state boundary atomically after Specialist
success. `RCAState`, the API response, and the frontend Agent Trace expose the
accepted/rejected audit. A reviewed `goal_satisfied` or `data_unavailable` stop
is still invalid when rejected updates leave Questions open. Core decision
errors continue to use the one-retry controlled fallback.

The paid reliability lane runs the golden Scratch + Cu CMP case three consecutive
times with a hard per-run LLM-call cap. Every run must stay on `llm_react`,
re-plan after its first observation, use compact accepted Question updates,
audit every accepted/rejected claim, and pass the existing Goal Success and Stop
Correct checks. A rejected ancillary claim is allowed only when its core Action
is committed and the run stays on `llm_react`; a core Planner validation failure
fails the lane. Reports contain bounded state summaries and stable reason-code
counts only, never prompts, raw model responses, or credentials.

## Decision Contract

Every `PlannerDecision` is exactly one of:

- `act`: contains one `InvestigationAction`, at least one target question, an
  execution reason, and `goal_status=in_progress`.
- `stop`: contains no action, has an explicit `StopReason`, and leaves the goal
  in a terminal status.

New questions embedded in a decision must:

- Start in `open` state.
- Reference the same `goal_id` as the decision.
- Not create a new user objective.

The action contains separate `inputs` and `scope`:

- `inputs` are the concrete arguments needed by the specialist.
- `scope` is the stable investigation boundary used for duplicate detection.

## Evaluation Contract

The evaluation system intentionally exposes only five metrics.

Per decision:

- `decision_valid`: the decision passed contract, registry, scope, and runtime
  checks.
- `evidence_gain`: the executed action added new Evidence IDs.
- `redundant`: the decision repeated an already covered action/scope or added
  no new investigative value because that scope was already covered.

Per run:

- `goal_success`: the final result answered the requested objective at an
  evidence-appropriate conclusion level.
- `stop_correct`: the Planner stopped at the right boundary instead of stopping
  too early or continuing without useful evidence gain.

Each metric is a boolean accompanied by a plain-language reason. There is no
additional weighted score system in this version.

### Deterministic Evaluation Runtime

The evaluator runs after final Tool and LLM usage metadata has been assembled.
It does not call Qwen or use an LLM judge. It derives all five metrics from the
committed `PlannerDecision`, `ActionRecord`, Finding, Evidence, Question,
Hypothesis, budget, and terminal-state contracts.

For each committed action, the evaluator requires the registered Agent,
completed ActionRecord, Finding references, Evidence references, stable scope,
and execution order to agree. Evidence gain contains only Evidence IDs first
introduced by that ActionRecord. An RCA reasoning action can therefore have no
new Evidence while still being useful and non-redundant. A repeated
`Action + Scope` is invalid and redundant.

Run success requires at least one Evidence-backed closed Question, no open
Question, no remaining Evidence gap, and an Evidence-gated conclusion
appropriate to the intent. An explicitly `unavailable` optional Question does
not fail an otherwise supported Goal, but an all-unavailable result cannot
claim success. In particular, an impact-scope or SPC investigation may succeed
at `signal`; an `inconclusive` or `conflicted` result never claims Goal success. Stop
correctness checks the declared stop reason against the actual evidence,
question, contradiction, action, and budget boundary. A structurally valid but
premature stop therefore remains `decision_valid=true` while
`stop_correct=false`.

`RunEvaluation` is a typed optional field on `RCAState` and is exposed through
the API and frontend type contract. It is generated only for a complete,
non-fallback `llm_react` run. Fixed and controlled compatibility modes, intent
fallback, and mid-loop fallback keep `run_evaluation=null`, because the current
contract cannot honestly attribute a controlled-policy stop to Qwen.

Only decisions committed after runtime validation are evaluated. Invalid raw
Qwen attempts that trigger retry or fallback remain visible through LLM usage
and fallback metadata; they are not converted into synthetic Decision IDs.

## Specialist V2 Boundary

Specialist V2 is enabled only for `llm_react`. The `fixed` and
`controlled_react` modes keep their existing Specialist implementations,
Tool sequences, and regression-baseline behavior.

For each Specialist action, Python creates an allowlisted set of same-domain
`SpecialistToolCandidate` objects. Each candidate contains a Python-bound Tool
name and immutable parameters. Qwen may select only a candidate ID or finish;
it cannot supply a Tool name, replace the source Lot, broaden the scope, edit
parameters, select an already-executed candidate, or call another Specialist.

One Specialist action may execute at most two Tool calls. Python enforces that
hard limit independently of Qwen and records each executed call as a
`SpecialistStepRecord` containing the execution reason, bound parameters,
output summary, status, and observed Evidence IDs.

Tool-selection output and engineering-analysis output each receive one
validation retry. If the second response is still invalid, only that local
Specialist stage uses its deterministic fallback; the cross-domain Planner
state is retained and the action is not replayed. The Finding records the
analysis source, fallback reason, retry count, Tool trace, and stop reason.

Qwen supplies the engineering summary and interpretation, but it does not own
Evidence. The analysis must reference the exact ordered closure of effective
Evidence IDs observed from the selected Tools: it cannot add, omit, or replace
an Evidence ID. Python assembles the compatible `AgentFinding`, preserves the
first-class Evidence payload, and caps Qwen confidence at the deterministic
Finding confidence. Existing Evidence/Hypothesis gates therefore remain the
authority for any root-cause conclusion.

If Advanced SPC is selected but reports no analyzable parameters, Python uses
the pre-bound Basic SPC candidate as the second and final Tool call. The
Advanced step remains visible in the audit trace as superseded, while the
effective Finding contains only the Basic SPC Evidence. This fallback never
exceeds the two-Tool limit.

For an `act` decision, the Supervisor first requires the Specialist action to
produce a valid Finding. It then commits the `PlannerDecision` together with
the corresponding `ActionRecord`; a failed Specialist action leaves neither
half recorded. This prevents a Planner decision from appearing as completed
without its observation.

Automated contract, unit, and integration tests use Fake Clients and cover:

- Strict round trips for candidate, decision, step-record, and analysis
  contracts.
- Legal same-domain Qwen Tool choices and rejection of cross-domain choices,
  parameter tampering, duplicate candidates, premature finish, and a third
  Tool call.
- One-retry behavior and local deterministic fallback without replaying an
  already successful Tool.
- Exact Evidence closure, bounded confidence, and Advanced-to-Basic SPC
  Evidence replacement.
- Two-step MES/FDC execution, including MES impact scope and derived
  commonality.
- Scratch + Cu CMP observation, Tool execution, analysis, and Planner
  replanning.
- Unchanged legacy Specialist paths in `fixed` and `controlled_react`.

These automated tests do not call the real DashScope service.

## Frontend Agent Trace Boundary

The investigation page renders an autonomous decision trace whenever
`llm_react` was requested and either a committed Planner decision exists or
the active execution mode is still `llm_react`. The trace shows the Goal,
budgets, known facts, final Question status, every committed ACT decision, its
ActionRecord observation, linked Findings and Evidence, Specialist V2 Tool
steps, the terminal STOP decision, and the five existing evaluation metrics.
Technical contracts remain collapsed behind native `details` elements so the
default view emphasizes the engineering story.

Frontend associations use typed IDs rather than array position:

- `PlannerDecision.decision_id` joins `DecisionEvaluation.decision_id`.
- `PlannerDecision.next_action.action_id` joins `ActionRecord.action.action_id`.
- `ActionRecord.produced_finding_ids` joins `AgentFinding.finding_id`.
- `ActionRecord.produced_evidence_ids` joins `Evidence.evidence_id`.
- Specialist steps join Tool latency by the exact action-scoped
  `tool_request_id`.

Missing, duplicate, or malformed references are surfaced as trace-integrity
warnings instead of being silently paired with another record. A superseded
Advanced SPC step remains visible as audit-only history, while only the
effective Basic SPC Evidence is shown as part of the Finding. RCA reasoning
may correctly show `Evidence Gain: No` together with `Redundant: No`, because
it can add analytical value without inventing Evidence.

An immediate Qwen STOP remains a visible terminal decision even when no
ActionRecord exists. A mid-loop Qwen failure preserves the autonomous decision
prefix, appends the controlled compatibility tail, and reports evaluation as
not attributed after the handoff rather than as a failed run. Intent-planning
fallback with no committed Qwen decision continues to use the controlled
timeline. Native `controlled_react` and `fixed` execution retain their
existing compatibility views.

## Batch 20.9.7 Evaluation Boundary

Batch 20.9.7 closes the autonomous Planner delivery with three deliberately
separate verification lanes. A deterministic Fake Client evaluation proves
the architecture and runtime contracts without network variability. The
existing fixed-workflow evaluation remains the compatibility baseline. A real
DashScope Qwen call is an optional, paid smoke test and is never silently
treated as equivalent to the deterministic acceptance suite.

### Verification Lanes and Status

| Lane | Purpose | Required for deterministic acceptance | Current status |
| --- | --- | --- | --- |
| Autonomous Fake final evaluation | Exercise `llm_react`, Specialist V2, Evidence/Hypothesis gates, and decision evaluation end to end with repeatable outputs | Yes | **PASS** — 10/10 autonomous and fallback scenarios passed |
| Fixed-workflow baseline | Protect the existing fixed workflow, RCA accuracy, abstention, citation integrity, and latency regression baseline | Yes | **PASS** — 10/10 established scenarios passed |
| Real Qwen smoke | Verify that the current prompts and strict JSON contracts can complete one bounded live DashScope investigation | No; explicitly opt-in | **SKIPPED** — `DASHSCOPE_API_KEY` and `RUN_REAL_QWEN_TEST=1` are not configured |

The recorded Fake-Qwen run produced 28/28 valid decisions, 18/20 ACT
decisions with new Evidence, 0/20 redundant ACT decisions, 6/6 successful
positive investigation goals, and 6/6 correct positive-run stops. The two ACT
decisions without Evidence gain are RCA-reasoning steps that reused existing
Evidence without being redundant.

The three statuses are reported independently:

- `PASS` means that the lane was executed and all of its assertions passed.
- `FAIL` means that the lane was executed and at least one required assertion
  failed.
- `SKIPPED` is allowed only for the optional real Qwen smoke when the API key
  or explicit paid-test opt-in is absent. A skipped live call must never be
  reported as a pass.

The required deterministic delivery passes only when both the Autonomous Fake
final evaluation and fixed-workflow baseline pass. The optional live smoke may
remain skipped. If the live smoke is explicitly enabled, the separate optional
test command reports its actual pass or fail result without changing the
deterministic results. The deterministic runner never initiates a paid call,
so its own artifact continues to label that separate lane as skipped.

### Metric Boundary

The final autonomous evaluation reuses exactly the five metrics defined by the
Evaluation Contract:

- Per decision: `decision_valid`, `evidence_gain`, and `redundant`.
- Per run: `goal_success` and `stop_correct`.

Each boolean retains its existing plain-language reason. Batch 20.9.7 does not
introduce a weighted score, combined score, LLM-as-judge score, or additional
public metric. Scenario counts and lane statuses are reporting facts, not new
quality scores.

### Acceptance Matrix

| Acceptance case | Required observation |
| --- | --- |
| Intent-sensitive planning | An impact-scope request and a root-cause request produce different bounded action chains instead of both running every Agent |
| Scratch + Cu CMP replanning | The trace shows observation-driven replanning across Defect/WAT, MES commonality, shared-defect validation, FDC/SPC, RCA reasoning, and terminal STOP |
| Decision trace integrity | Every committed ACT joins by typed ID to exactly one ActionRecord and only its produced Finding and Evidence records |
| Evidence gain semantics | Evidence-collecting actions report gain; RCA reasoning may report `evidence_gain=false` and `redundant=false` because it adds analysis without inventing Evidence |
| Evidence-gated conclusion | A supported claim with zero Evidence is downgraded to inconclusive, while a supported claim with only partial defect Evidence and no supported Hypothesis remains a signal |
| Correct stop boundary | A successful run closes the original questions and reports `goal_success=true` and `stop_correct=true`; a premature stop remains visibly unsuccessful |
| Specialist V2 boundary | Qwen can choose only pre-bound same-domain Tool candidates, the local Tool budget is enforced, and effective Evidence closure remains exact |
| Compatibility handoff | Intent or mid-loop Planner fallback preserves completed work, resumes the controlled path, and leaves `run_evaluation=null` instead of attributing the controlled stop to Qwen |
| Fixed compatibility baseline | The established fixed evaluation retains its RCA, abstention, traceability, and citation-integrity acceptance results |
| Typed delivery surface | API state and the frontend Agent trace preserve the five metrics and do not infer joins from array position |

### Reproducible Commands

Run the autonomous deterministic final evaluation:

```powershell
.\.venv\Scripts\python.exe scripts\run_autonomous_qwen_evaluation.py
```

The runner is expected to write stable, secret-free artifacts to:

```text
outputs/autonomous_qwen_react_evaluation/results.json
outputs/autonomous_qwen_react_evaluation/report.md
```

Run the focused autonomous regression tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_autonomous_evaluation_suite.py tests/integration/test_llm_react_workflow.py tests/integration/test_specialist_v2_workflow.py tests/unit/test_decision_evaluation.py -q
```

Run the fixed-workflow evaluation baseline:

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py
```

The optional Qwen smoke test is skipped by default:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_qwen_optional.py -q
```

To opt in to the paid live call, set the key only in the local process and
enable the explicit test flag:

```powershell
$previousApiKey = [Environment]::GetEnvironmentVariable("DASHSCOPE_API_KEY", "Process")
$previousRunFlag = [Environment]::GetEnvironmentVariable("RUN_REAL_QWEN_TEST", "Process")
$qwenSecret = Read-Host "DashScope API key" -AsSecureString
try {
    $env:DASHSCOPE_API_KEY = [System.Net.NetworkCredential]::new("", $qwenSecret).Password
    $env:RUN_REAL_QWEN_TEST = "1"
    .\.venv\Scripts\python.exe -m pytest tests/integration/test_qwen_optional.py -q
} finally {
    if ($null -eq $previousApiKey) {
        Remove-Item Env:DASHSCOPE_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:DASHSCOPE_API_KEY = $previousApiKey
    }
    if ($null -eq $previousRunFlag) {
        Remove-Item Env:RUN_REAL_QWEN_TEST -ErrorAction SilentlyContinue
    } else {
        $env:RUN_REAL_QWEN_TEST = $previousRunFlag
    }
    Remove-Variable qwenSecret, previousApiKey, previousRunFlag -ErrorAction SilentlyContinue
}
```

The API key must not be committed, copied into an evaluation artifact, printed
in a report, or exposed through API/frontend state. The live smoke should use a
small impact-scope investigation to bound cost, while still requiring the real
Intent Planner, Next-action Planner, Specialist V2, terminal decision, and
typed evaluation path. A compatibility fallback during that live test is a
smoke-test failure, not a successful autonomous Qwen run.

## Planned Delivery Batches

1. 20.9.0: Git baseline. Complete.
2. 20.9.1: Decision, Question, and Evaluation contracts. Complete.
3. 20.9.2: Qwen intent Planner. Complete.
4. 20.9.3: Qwen next-action Planner and retry/fallback. Complete.
5. 20.9.4: Specialist Agent V2. Complete.
6. 20.9.5: Agent decision evaluation. Complete.
7. 20.9.6: Frontend Agent trace. Complete.
8. 20.9.7: Qwen smoke test and final evaluation. Complete.

Automated tests use a Fake Client. Real DashScope calls are optional smoke
tests and require `DASHSCOPE_API_KEY`.
