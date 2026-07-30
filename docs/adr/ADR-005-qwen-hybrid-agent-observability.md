# ADR-005: Qwen Hybrid Agent and Observability Foundation

## Status

Accepted for Step 16.

## Context

Steps 0-15 proved the data, Tool, workflow, API, and dashboard boundaries with
deterministic Agent implementations. That baseline is valuable for regression,
but it does not exercise an LLM and therefore is not sufficient as the final
AI Agent architecture.

Industrial RCA also cannot allow a model to invent database facts, cite records
that Tools did not return, or override explicit physical-conflict and
evidence-sufficiency gates.

## Decision

Use DashScope's OpenAI-compatible API with `qwen-plus` behind one LLM Gateway.
Support three explicit runtime modes:

- `deterministic`: no model calls; fixed regression baseline.
- `fake`: complete structured model path with deterministic no-cost responses.
- `llm`: real `qwen-plus` calls; requires `DASHSCOPE_API_KEY`.

Planner, Specialist interpretation, and RCA candidate ranking may use Qwen.
Repositories, Tools, evidence validation, Supervisor execution, conflict gates,
and final support thresholds remain deterministic.

The model may only:

- return a valid acyclic plan containing registered Agents;
- interpret a Specialist finding while preserving its exact Tool evidence IDs;
- rank root-cause candidates already built from deterministic evidence.

There is no silent fallback from `llm` to `deterministic`. Invalid model output
fails the job rather than producing an untraceable conclusion.

Each run records Agent mode, model, prompt version, token counts, LLM latency,
Tool calls, and Tool latency in `RCAState.execution_metadata`. FastAPI exposes
health, readiness, and Prometheus metrics and writes best-effort lifecycle and
LLM usage audit events. Telemetry failure must not change RCA output.

## Consequences

- Golden-case regression remains reproducible and free in deterministic mode.
- Fake mode tests the complete model boundary in CI without API cost.
- Real-Qwen evaluation must be reported separately from deterministic results.
- API keys remain runtime environment variables and are never logged.
- Prompt changes require versioned prompt files and visible prompt metadata.
