# Improvement Agent

## Scope

Step 18 adds an Improvement Agent after RCA Reasoning and before Report
Generator. The Agent consumes existing Agent findings only. It does not access
Repositories, databases, or Tools, and it does not publish long-term memory.

```text
Specialist findings
  -> RCA Reasoning
  -> Improvement Agent
  -> Markdown Report
```

Step 19 adds candidate persistence, two-engineer approval, audit records, and
controlled publication outside the Agent boundary.

## Output

Improvement Agent returns a traceable `AgentFinding` containing:

```text
incident_summary
engineering_summary
scope_assessment
containment_actions
corrective_actions
recipe_optimization
preventive_actions
fab_system_optimization
memory_status = candidate_ready_for_step_19_persistence
requires_two_engineer_approval = true
```

Every recommendation includes `recommendation_id`, `action`, `rationale`, and
existing `evidence_ids`. The Agent does not create new evidence records.

## Fab-Level Gate

A Fab-level recommendation requires a supported RCA plus at least one of:

```text
cross-Lot evidence
a relevant imported confirmed historical RCA case
```

Historical similarity alone is insufficient. The historical title, symptom,
or root cause must also align with the current root-cause terms. An inconclusive
RCA always remains event-level and withholds corrective, Recipe, preventive,
and Fab/system recommendations.

## Recommendation Layers

1. Containment Actions
2. Corrective Actions
3. Recipe Optimization Recommendations
4. Preventive Actions
5. Fab/System Optimization

Recipe recommendations are proposals for controlled DOE, split-Lot, or
qualification work. They do not modify production Recipe values and explicitly
require Process Engineer approval.

## LLM Boundary

In `llm` mode, Qwen creates the engineering summary. Deterministic code defines
the available recommendation IDs and evidence IDs. The response is rejected if
the model adds or removes either set. Deterministic and fake modes support the
same output contract.

## Report Integration

Report Generator includes the engineering summary, all five recommendation
categories, citations, and a Memory Status section. The FastAPI application
creates the actual candidate after the workflow completes. See
`docs/memory-approval.md` for persistence and approval rules.
