# Batch 20: Controlled ReAct RCA Execution Plan

## Goal

Replace the fixed, all-agent RCA path with a bounded investigation loop that
selects the next approved action from current evidence. The default fixed
workflow remains the compatibility baseline until controlled ReAct has passed
the same regression and evaluation gates.

## Non-negotiable controls

- A Planner may select only registered action kinds, never arbitrary database or Tool calls.
- Policy, not an LLM, validates action preconditions, budgets, duplicate actions and stop rules.
- A conclusion can be supported only with traceable scope, mechanism and product-outcome evidence and no unresolved critical contradiction.
- Historical knowledge can validate a current hypothesis but cannot independently confirm it.
- A machine-stop recommendation is advisory and must remain approval-gated.

## Delivery sequence

### 20.1 Contracts and compatibility foundation

Add typed investigation goals, bounded actions, action records, budgets and stop reasons. Add the `fixed | controlled_react` orchestration-mode contract. No existing job changes behaviour in this step.

### 20.2 Deterministic policy and registry

Implement an Action Registry and `InvestigationPolicy.next_action(state)`. The policy chooses the smallest legal next action based on evidence gaps, blocks duplicate work, and terminates on satisfied goal, contradiction, unavailable data or budget exhaustion.

### 20.3 Controlled loop in Supervisor

Keep the fixed Supervisor path intact. Add a feature-flagged loop: plan initial goal, execute one action, record observation, re-evaluate policy, and stop with an explicit reason. Persist action history in `RCAState` and execution metadata.

### 20.4 Scratch / Cu CMP vertical slice

Add targeted specialist actions: defect pattern, shared exposure, impact scope, FDC/SPC for a selected chamber/window, and RCA reasoning. Implement the sequence Defect -> MES -> FDC -> RCA, with optional historical validation.

### 20.5 Product surface and release gate

Expose action history, evidence gaps and stop reason through API, report and frontend timeline. Add contract, integration and evaluation scenarios. Promote controlled ReAct only after fixed and controlled modes both pass the existing regression suite.

## Completion rules

| Goal | Required evidence before stopping successfully |
| --- | --- |
| Impact scope | Operation, equipment/chamber, exposure window, derived impact population |
| SPC check | Parameter, baseline/rule, observed violation, matching time window |
| Supported RCA | Shared exposure + process mechanism + matching product outcome + no unresolved critical contradiction |
| Inconclusive RCA | Required evidence remains unavailable after permitted acquisition actions, no legal next action, or a budget is exhausted |

## Initial controlled actions

`inspect_defect_pattern`, `find_shared_exposure`, `assess_impact_scope`,
`inspect_fdc_spc`, `inspect_recipe_change`, `validate_historical_case`,
`run_rca_reasoning`, and `conclude_inconclusive`.
