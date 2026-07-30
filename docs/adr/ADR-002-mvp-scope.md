# ADR-002: MVP Scope

## Status

Accepted.

## Context

The full design includes realistic semiconductor data concepts and future extensions, but the project must first prove one complete RCA workflow.

## Decision

MVP includes:

- one golden synthetic RCA case
- PostgreSQL schema
- offline seed workflow
- Tool Layer
- MES Agent
- FDC Agent
- Defect/WAT Agent
- Knowledge Agent
- Planner Agent
- Supervisor Agent
- RCA Reasoning Agent
- Report Generator
- pure Python end-to-end workflow
- basic FastAPI wrapper
- basic React dashboard

MVP excludes:

- raw FDC stream processing
- Vision Agent
- real-time Fab integration
- complete SPC platform
- large-scale synthetic dataset
- strict batch RCA accuracy metrics
- production-grade security and observability

## Consequences

The project can be implemented in a disciplined sequence:

```text
data -> tools -> agents -> workflow -> API -> UI
```

Effect metrics and scale tests are intentionally postponed until after the golden path works.

