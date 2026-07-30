# Agent Architecture

## Overview

The project uses a planner-driven Multi-Agent architecture:

```text
User
  |
  v
Planner Agent
  |
  v
Supervisor Agent
  |
  +-----------------------------+
  |             |               |
  v             v               v
MES Agent     FDC Agent     Defect/WAT Agent
  |             |               |
  +-------------+---------------+
                |
                v
        Knowledge Agent
                |
                v
        RCA Reasoning Agent
                |
                v
        Improvement Agent
                |
                v
        Report Generator
```

Step 16 uses a Hybrid Agent execution model. In `fake` and `llm` modes, one
shared LLM Gateway invokes versioned prompts for Planner, Specialist
interpretation, RCA candidate ranking, and engineering improvement synthesis.
In `llm` mode the configured model is DashScope `qwen-plus`.

The LLM cannot access a Repository or database, invoke unregistered Tools,
introduce evidence IDs, invent root-cause candidates, or bypass deterministic
evidence and physical-conflict gates.

## Planner Agent

Responsibilities:

- Interpret the user objective.
- Produce a structured `TaskPlan`.
- Select registered agents required for the task.

Planner must not:

- Query databases.
- Call Tools directly.
- Produce RCA conclusions.
- Generate final reports.

## Supervisor Agent

Responsibilities:

- Execute the `TaskPlan`.
- Maintain `RCAState`.
- Route work to specialist agents.
- Handle missing data, conflicting evidence, retries, and degraded paths.
- Invoke RCA reasoning and report generation after evidence collection.

## Specialist Agents

### MES Analysis Agent

Uses MES Tools to analyze:

- affected lots
- requested abnormal Lot context
- impact Lots sharing an OOC exposure window
- lot and wafer genealogy
- operation commonality
- equipment and chamber concentration
- recipe version changes
- hold comments

For a Lot-driven task, the MES Agent calls `get_lot_context`,
`find_impact_lots`, and `analyze_lot_genealogy`. The Agent does not query a
Repository directly. `find_impact_lots` returns the source exposure, the OOC
window, selection criteria, `impact_lots`, and traceable evidence IDs.

### FDC Analysis Agent

Uses FDC Tools to analyze:

- equipment/chamber health
- parameter drift
- OOC events
- temporal relationship between process abnormality and yield loss
- Minimal SPC center lines, 3-sigma limits, and bounded rule violations

MVP uses FDC feature summary only. It does not process raw sensor traces.

### Defect/WAT Analysis Agent

Uses structured Defect/WAT Tools to analyze:

- defect type
- defect count and density
- pattern type
- WAT fail mode
- physical/electrical consistency

MVP does not use a Vision Agent.

### Knowledge Agent

Uses Knowledge Tools to retrieve:

- historical RCA cases
- SOP snippets
- engineering notes

MVP uses PostgreSQL metadata plus keyword/tag retrieval. Vector search is a later enhancement.

## RCA Reasoning Agent

Combines structured findings into hypotheses and a final RCA conclusion.

Scoring factors:

- MES commonality strength
- FDC abnormality strength
- Defect/WAT consistency
- historical case similarity
- temporal causality
- missing data penalty
- conflicting evidence penalty

MVP uses evidence-based scoring, not a full Bayesian model.

## Improvement Agent

Runs only after RCA Reasoning. It converts a validated RCA into evidence-backed
containment, corrective, Recipe, preventive, and Fab/system recommendations.
Fab-level conclusions require cross-Lot evidence or a relevant confirmed
historical case. Inconclusive RCA results cannot produce root-cause-specific or
Fab-level recommendations.

The Agent does not access Tools or Repositories and does not write long-term
memory. It stores its result in `RCAState`. The Step 19 FastAPI application
service creates a candidate after workflow completion, applies dual-engineer
approval, and publishes only confirmed records. This preserves the boundary
between AI synthesis and authorized knowledge mutation.

## Report Generator

Creates a Markdown RCA report from `RCAState`.

The report must include:

- problem summary
- affected lots
- investigated Lot and impact scope for Lot-driven jobs
- evidence chain
- root cause
- confidence
- recommended actions
- layered engineering improvement recommendations
- memory publication status
- warnings
- referenced records

The report must not invent missing data.

## Agent Boundary Rule

Agents must not import database or repository modules.

Agents only call Tools with structured inputs and receive structured outputs with `evidence_ids`.
