# System Overview

## Purpose

The system is a semiconductor Yield RCA Multi-Agent platform. It uses a realistic but constrained synthetic Fab data model to let agents investigate a yield excursion through structured tools and evidence-backed reasoning.

The design prioritizes a working MVP loop:

```text
Offline Synthetic Fab Dataset
        |
        v
PostgreSQL Analysis Database
        |
        v
Tool Layer
        |
        v
Planner / Supervisor / Specialist Agents
        |
        v
RCA Reasoning Agent
        |
        v
Engineering RCA Report
```

## Runtime Architecture

```text
User / React Dashboard
        |
        v
FastAPI Backend
        |
        v
Core Python RCA Workflow
        |
        v
Planner + Supervisor + Agents
        |
        v
Tool Layer
        |
        v
PostgreSQL / Knowledge Store
```

The core Python workflow is the product center. FastAPI and React are wrappers around a workflow that must first work without HTTP or browser dependencies.

## Local Container Topology

Step 15 packages the proven runtime without changing its ownership boundaries:

```text
Browser
  -> Nginx / React container
  -> FastAPI container
  -> PostgreSQL container
```

The default Compose topology contains only runtime services. Migration and
seed import use an explicitly invoked `tools` profile. Synthetic generators
remain host-side offline tools and are not copied into the Backend runtime
image.

## Data Flow

Synthetic data flow:

```text
scripts/generate_synthetic_fab_data.py
        |
        v
data/seeds/golden_case/*.csv / *.json
        |
        v
scripts/seed_database.py
        |
        v
PostgreSQL
```

RCA runtime flow:

```text
User query
        |
        v
Planner creates TaskPlan
        |
        v
Supervisor executes tasks
        |
        v
Specialist agents call Tools
        |
        v
RCA Agent fuses evidence
        |
        v
Report Generator produces RCA report
```

The runtime supports two entry modes:

```text
Product/time-window mode             Lot-driven mode
product + time window                abnormal lot_id
        |                                  |
find WAT-failed population           resolve Lot context
        |                                  |
MES process commonality              derive OOC exposure window
        |                                  |
        +----------- shared Specialist / RCA / report flow --------+
```

Lot-driven impact scope is calculated in the Tool Layer. A Lot is included when
its `process_history` record has the same operation, equipment, and chamber as
the source exposure and its process interval overlaps the matching OOC excursion
window. `impact_lots` excludes the investigated Lot; `affected_lots` represents
the complete exposed population used by downstream Specialists.

## MVP Golden Case

The first golden case is:

```text
Product: 40N_SOC
Problem: July yield drop
Root Cause: CMP_CU03_CH02 slurry delivery degradation
```

Required evidence:

- Affected lots concentrate on operation `6400 Cu CMP`.
- Affected lots concentrate on `CMP_CU03_CH02`.
- FDC feature summary shows `slurry_flow` decrease.
- FDC feature summary shows `endpoint_time` increase.
- OOC events increase in the relevant window.
- Defect summary shows scratch increase.
- WAT result shows leakage fail increase.
- Hold comment contains relevant CMP/scratch/leakage signal.
- Historical RCA case matches slurry delivery degradation.

## Explicit Non-Goals for MVP

- Raw FDC sensor ingestion.
- Vision Agent or wafer map image analysis.
- Real Fab MES/FDC/KLA/WAT integration.
- Full SPC platform.
- Production-grade observability and security.
- Batch effect metrics such as Top-1 accuracy or Brier score.

These are valid future extensions after the core loop is proven.
