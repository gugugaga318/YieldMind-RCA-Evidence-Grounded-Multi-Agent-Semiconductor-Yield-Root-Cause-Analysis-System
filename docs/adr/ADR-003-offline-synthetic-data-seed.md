# ADR-003: Offline Synthetic Data Seed

## Status

Accepted.

## Context

The project has no real Fab data during MVP development. Synthetic data is required so Tools and Agents have a stable world to query.

There is a design risk: if synthetic Fab data is generated inside the FastAPI runtime, the system becomes unrealistic and hard to test.

## Decision

Synthetic Fab data generation is an offline seed workflow.

Allowed:

```text
scripts/generate_synthetic_fab_data.py
scripts/seed_database.py
data/seeds/golden_case/*.csv
data/seeds/golden_case/*.json
```

Not allowed:

```text
FastAPI startup generates synthetic Fab data
API request dynamically generates Fab data
Agent runtime asks backend to create Fab data
```

Runtime RCA must query existing data:

```text
Agent -> Tool -> PostgreSQL
```

## Consequences

- Golden datasets are reproducible.
- Ground truth can be versioned with seed files.
- Tool and Agent tests are stable.
- Future real Fab integration can replace seed import with ETL without changing Agent architecture.

