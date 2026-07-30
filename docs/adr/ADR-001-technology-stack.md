# ADR-001: Technology Stack

## Status

Accepted for MVP.

## Context

The project needs a stack that supports:

- structured industrial data
- Python-first analytics and agent orchestration
- tool-based RCA workflow
- eventual API and dashboard layers

## Decision

MVP technology stack:

```text
Python
PostgreSQL
FastAPI
React
TypeScript
ECharts
LangGraph or equivalent graph orchestration
```

Supporting libraries may include:

```text
Pydantic
SQLAlchemy or equivalent database layer
Pandas
Scikit-learn
pytest
```

MVP knowledge retrieval uses PostgreSQL metadata plus keyword/tag retrieval.

pgvector or Chroma is deferred to a later phase.

## Consequences

- Python remains the center of domain, data, Tool, and Agent logic.
- FastAPI is only an adapter around the core workflow.
- React is only a presentation layer.
- The initial system stays small enough to implement and demonstrate.

