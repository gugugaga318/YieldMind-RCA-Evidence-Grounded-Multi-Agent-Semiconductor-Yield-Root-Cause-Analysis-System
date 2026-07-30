# Autonomous Qwen ReAct Specification

## Status

- Target branch: `feature/autonomous-qwen-react`
- Model provider: DashScope
- Model: `qwen-plus`
- Current implementation stage: Batch 20.9.4 Specialist Agent V2

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

## Planned Delivery Batches

1. 20.9.0: Git baseline. Complete.
2. 20.9.1: Decision, Question, and Evaluation contracts. Complete.
3. 20.9.2: Qwen intent Planner. Complete.
4. 20.9.3: Qwen next-action Planner and retry/fallback. Complete.
5. 20.9.4: Specialist Agent V2. Complete.
6. 20.9.5: Agent decision evaluation.
7. 20.9.6: Frontend Agent trace.
8. 20.9.7: Qwen smoke test and final evaluation.

Automated tests use a Fake Client. Real DashScope calls are optional smoke
tests and require `DASHSCOPE_API_KEY`.
