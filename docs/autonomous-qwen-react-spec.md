# Autonomous Qwen ReAct Specification

## Status

- Target branch: `feature/autonomous-qwen-react`
- Model provider: DashScope
- Model: `qwen-plus`
- Current implementation stage: Batch 20.9.1 contracts

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

`llm_react` is not enabled by Batch 20.9.1. It becomes selectable only after
its runtime path and fallback behavior are implemented.

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

Each specialist may select Tools only from its own domain and may execute at
most two Tool steps for one specialist action. A specialist cannot call another
specialist.

## Planned Delivery Batches

1. 20.9.0: Git baseline.
2. 20.9.1: Decision, Question, and Evaluation contracts.
3. 20.9.2: Qwen intent Planner.
4. 20.9.3: Qwen next-action Planner and retry/fallback.
5. 20.9.4: Specialist Agent V2.
6. 20.9.5: Agent decision evaluation.
7. 20.9.6: Frontend Agent trace.
8. 20.9.7: Qwen smoke test and final evaluation.

Automated tests use a Fake Client. Real DashScope calls are optional smoke
tests and require `DASHSCOPE_API_KEY`.
