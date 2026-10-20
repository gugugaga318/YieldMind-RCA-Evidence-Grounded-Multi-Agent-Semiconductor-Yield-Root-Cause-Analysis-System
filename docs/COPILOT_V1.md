# Copilot v1

## Product goal

Help engineers investigate semiconductor yield excursions through traceable data collection, hypothesis comparison and explicit next-investigation recommendations.

Manufacturing observations can establish parameter excursions, shared exposure, temporal association and product impact without uniquely identifying a causal mechanism. Investigation and confirmation therefore have separate authority.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| Planner / LLM | Propose an investigation action or hypothesis within the current context |
| Supervisor | Validate and dispatch actions; maintain budgets and stopping boundaries |
| Tools | Query repositories and return typed observations |
| RCAState | Store task facts, Evidence, candidates, decisions and action history |
| Candidate / Competition / Ranking | Compare legal candidates and preserve unresolved alternatives |
| Confirmation | Apply strict scope, exposure, parameter, outcome, temporal and causal-evidence requirements |
| Python Finalizer | Be the sole authority writer for the final structured result |
| API / Web / Report | Present the already written result |
| Human engineer | Confirm causality and approve consequential engineering actions |

Professional execution roles share RCAState. They do not imply independent persistent conversation histories or autonomous engineering authority.

## Result boundaries

`triage/1.0` can publish suspected causes, evidence assessment, uncertainty and recommended investigation. This does not grant confirmed-root-cause or confirmed-impact authority.

- Zero legal candidates: do not invent a primary.
- One legal candidate: do not invent an alternative.
- Unresolved tie: stable presentation order must not imply a proven winner.
- Engineering sign-off: remains `not_recorded` unless separately recorded.

Evidence retains source/provenance, entity and time/scope information. Each model stage receives a bounded projection. Candidate generation protects bounded recent, prior-candidate and challenge references; trimming the prompt does not delete original Evidence from State. Its character limits are not exact token accounting.

`not_acquired`, `not_observed`, `observed_normal`, `abnormal` and `unavailable` express different states. Absence of acquired evidence does not prove normal operation.

## Execution and validation

Per-job model settings reach the worker's provider adapter and actual model identity is recorded. Transport retry, structured-output repair and transient job recovery are separate operations. Model selection does not imply automatic cross-provider failover.

Software regression, deterministic/Fake-model acceptance, real-API functional smoke and semantic outcome evaluation answer different questions. Synthetic results are not production-Fab accuracy. The project does not publish an established Copilot Top-1/Top-2 accuracy claim.

## Public snapshot

Product implementation originates from frozen source revision `1d46d4cc967b6324238c698ced57563079493bd5`. Publication uses a clean snapshot on the existing public main history, avoiding upload of internal development histories.

Runtime code is preserved. Packaging relocates deterministic evaluation reference files into test fixtures and points configuration-contract checks at public configuration documentation. This is not a new RCA algorithm release.
