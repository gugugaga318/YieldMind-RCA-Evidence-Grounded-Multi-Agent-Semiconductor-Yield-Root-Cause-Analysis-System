# Evaluation V2 and Causal Scope Specification

## Status

- Status: Approved design; Long Task A implemented behind a default-off Feature Flag;
  Long Task B structurally implemented with human data review pending; Long Task C pending
- Target branch: `feature/autonomous-qwen-react`
- Proposed delivery: Batch 22
- Depends on: Batch 21 semantic gating and Long Tasks 1-4 governed Hybrid Retrieval
- Scope of this document: contracts, evaluation design, release gates, and execution plan

## Decision Summary

The following product decisions are approved:

- An observed Module identifies where a symptom was detected; it does not identify
  the causal Module.
- Root-cause investigation uses four bounded candidate lanes from the first search:
  same-step, upstream-route, shared-resource, and global-semantic.
- Observed Module, equipment, operation, product, and defect metadata are soft hints
  for RCA and historical matching unless the user explicitly restricts the search.
- Approval visibility, authorization, document type, time cutoff, and explicit user
  limits remain hard constraints.
- Qwen chooses investigative actions and explains candidates. Python owns hard Scope,
  candidate budgets, capability rules, Evidence applicability, and stop boundaries.
- Evaluation V2 separately evaluates Knowledge Retrieval and end-to-end RCA.
- V2 prioritizes causal difficulty and realistic distractors before raw row count.
- V1 artifacts remain versioned regression fixtures but may not be presented as
  production-fab quality evidence.

## Problem Statement

The V1 Synthetic Retrieval benchmark is useful as a deterministic regression test,
but it cannot support a production-quality claim:

- Query and document text are derived from the same Canonical Fact and share obvious
  symptom terms.
- Exact Module and equipment filters reduce an answerable Query to an average of 3.61
  candidate documents; some Queries have only one candidate.
- Every final V1 No-answer Query has an empty candidate pool after metadata filtering.
- Hard-negative accuracy only checks whether a named negative outranks the first
  relevant result.
- Calibration and test Query IDs are disjoint, but eight calibration RCA Queries have
  a translated Query for the same target incident in the test partition.
- Historical Overreach is a deterministic Hypothesis-gate invariant, not a model
  accuracy estimate.

The runtime Scope shares the same causal weakness. A symptom observed after Cu CMP can
originate from CMP, an upstream process, handling, metrology, or a shared resource.
Filtering all candidates to `module=Cu CMP` assumes the answer before the investigation.

## Goals

Batch 22 must:

1. Represent observation location separately from causal search Scope.
2. Preserve cross-Module candidates without permitting unbounded Agent or Tool loops.
3. Keep explicit user restrictions and governance boundaries authoritative.
4. Build a Retrieval V2 benchmark with independent wording, dense in-scope distractors,
   cross-Module relevance, and non-empty-pool No-answer cases.
5. Build an RCA V2 benchmark that starts from an observation and requires evidence-led
   replanning across MES, FDC/SPC, Defect/WAT, Knowledge, and RCA reasoning.
6. Compare algorithms under the same Scope and candidate corpus.
7. Separate data-quality, retrieval-quality, RCA-quality, and governance results.
8. Produce measured results without prewriting resume improvement numbers.

## Non-goals

Batch 22 will not:

- Claim validation against real confidential Fab data.
- Add an unsupported Material genealogy capability.
- Let an LLM create qrels, approval visibility, Evidence links, or release decisions.
- Treat a retrieved historical case as current-Lot causal proof.
- Remove fixed or controlled compatibility modes.
- Enable the Cross-Encoder merely because it exists.
- Use raw data volume as a substitute for causal difficulty.
- Replace existing Question-Action-Evidence and Hypothesis gates.

## Terminology

### Observation Scope

`ObservationScope` records what is known about where and how a signal was detected:

```text
source_lot_id
product_id
detected_module
detected_operation
detected_equipment_id
detected_at
symptom_types
known_measurements
known_defect_attributes
```

None of these fields is a confirmed causal attribution.

### Causal Search Scope

`CausalSearchScope` records how candidate evidence and knowledge may be discovered:

```text
hard_constraints
soft_hints
expansion_lanes
explicit_user_limits
time_boundary
candidate_budget
scope_reason
```

### Causal Lane

The initial lane enum is bounded:

```text
same_step
upstream_route
shared_resource
global_semantic
```

A lane describes candidate provenance. It is not evidence that the candidate is causal.

### Explicit User Limit

An explicit user limit is a natural-language restriction such as "only inspect Cu CMP"
or "only search records before 2025-06-01" that the Intent Planner extracts and Python
validates. An inferred observed Module is never converted into an explicit limit.

## Scope Authority

### Python-owned hard constraints

Python remains authoritative for:

- tenant, Fab, product-data, and user authorization;
- `CONFIRMED` Active Index visibility;
- requested Knowledge document type;
- investigation time cutoff and future-data prevention;
- protected source Lot and product identity;
- explicitly validated user limits;
- available route, equipment, chamber, and resource relationships;
- per-lane and total candidate budgets.

### Soft hints

The following are ranking inputs by default:

- detected Module and operation;
- detected and exposed equipment types;
- product, technology, recipe, defect, and measurement terms;
- process-route distance;
- shared equipment, chamber, handler, carrier, recipe, or utility relationships that
  exist in configured data;
- temporal proximity.

A soft mismatch cannot remove an otherwise visible candidate.

### Qwen authority

Qwen may:

- interpret the user's objective and Observation Scope;
- propose explicit user limits found in the input;
- choose one legal next investigative Action;
- choose which causal lane or Evidence gap deserves follow-up;
- compare and explain retrieved candidates;
- stop or downgrade when evidence is insufficient.

Qwen may not:

- promote a soft hint to a hard constraint;
- invent a route, resource relation, approval, or data source;
- remove the global lane from RCA because a same-step candidate appears plausible;
- make Knowledge Evidence sufficient for a supported root cause;
- exceed Python-owned Action, Tool-call, or candidate budgets.

## Intent-specific Scope Policy

| Intent or Question | Default Module behavior | Allowed hard restriction |
|---|---|---|
| `root_cause` | Soft hint | Explicit user limit only |
| `full_rca` | Soft hint | Explicit user limit only |
| `historical_match` inside RCA | Soft hint | Explicit user limit only |
| `impact_scope` | Derived from confirmed exposure, equipment, route, and time | Explicit user limit plus protected source scope |
| independent `historical_match` | Soft hint | Explicit user limit only |
| `procedure_guidance` | Hard when the user explicitly asks for one operation's SOP | Requested document type and explicit operation/module |
| `engineering_note_lookup` | Soft hint | Explicit user limit only |

If an existing caller cannot supply the new contracts, a legacy adapter preserves its
old behavior only in compatibility mode. New `llm_react` work must use explicit Scope.

## Runtime Candidate Architecture

### Lane 1: Same-step

Generate candidates associated with the detected operation, Module, equipment, recipe,
or material family. This lane is useful but receives no exclusive authority.

### Lane 2: Upstream-route

Use the source Lot's actual process route to identify earlier operations and exposures.
Route distance is a soft ranking feature. Future downstream operations cannot explain an
already observed signal unless the Question concerns detection or measurement behavior.

### Lane 3: Shared-resource

Use configured relationships such as shared equipment, chamber, handler, carrier, recipe,
chemical, or utility. An unavailable relationship remains unavailable; Batch 22 must not
fabricate Material or handler genealogy absent from the Repository.

### Lane 4: Global-semantic

Retrieve across all otherwise visible documents of the requested type. This lane provides
a bounded escape from an incorrect observed-Module assumption.

### Candidate generation and fusion

Each lane performs lexical and vector candidate generation under the same governance
constraints. The runtime then:

1. fuses lexical and vector rankings within each lane;
2. retains a minimum quota from every available lane;
3. merges lane rankings through deterministic weighted RRF;
4. aggregates Chunk hits to logical assets;
5. applies optional Cross-Encoder reranking only behind its Feature Flag;
6. returns bounded typed candidates with lane and Scope provenance.

The output records:

```text
candidate_lane
scope_reason
route_distance
shared_resource_types
lexical_score
vector_score
fusion_score
reranker_score
calibrated_relevance
source_confidence
```

These fields remain separate from RCA conclusion confidence.

### Candidate budgets and diversity

- Every available lane receives a configurable non-zero minimum quota.
- A total candidate cap bounds model, database, and prompt cost.
- Duplicate logical assets are merged before the Knowledge Agent sees them.
- Same-step candidates cannot consume the complete final list.
- Missing route or resource data reduces the available lanes and produces a typed
  diagnostic; it does not silently claim that only the observed Module matters.

Exact quotas are configuration and calibration values, not LLM output.

## Planner and Evidence Interaction

The Next-action Planner receives:

- the Observation Scope;
- hard constraints and explicit user limits;
- available causal lanes and missing data-source notices;
- lane-diverse candidate summaries;
- open Questions and applicable Evidence gaps;
- prior Actions and per-direction Evidence Gain;
- remaining budgets and critical contradictions.

The first retrieval does not require Qwen to discover cross-Module search as a special
fallback. Cross-Module visibility exists from the start. Qwen decides which candidate to
investigate with Tools.

An Action gains Evidence only when existing Batch 21 rules link new applicable Evidence
to a targeted Question or resolve a relevant contradiction. Two consecutive no-gain
directions remain bounded. A cross-Module candidate cannot raise conclusion strength
without current-Lot operational Evidence.

## Evaluation V2 Boundaries

Evaluation V2 contains two independent products.

### Retrieval V2

Retrieval V2 evaluates the ability to find an approved logical Knowledge Asset. It does
not claim to evaluate root-cause confirmation.

Versioned inputs include:

```text
data/knowledge/synthetic_v2/
data/evaluation/retrieval_ground_truth_v2.json
data/evaluation/retrieval_partitions_v2.json
data/evaluation/retrieval_qrel_review_v2.json
```

### RCA V2

RCA V2 evaluates the complete observation-to-decision path over a shared Synthetic Fab
world with distractor Lots, routes, equipment, signals, defects, and knowledge.

Versioned inputs include:

```text
data/seeds/causal_scope_v2/
data/evaluation/rca_scenarios_v2.json
data/evaluation/rca_scenario_review_v2.json
```

The existing `retrieval_ground_truth.json` and `scenarios.json` remain unchanged V1
regression fixtures.

## V2 Data Generation Isolation

### Hidden incident truth

Every incident family starts from structured hidden truth rather than one narrative seed:

```text
incident_family_id
observation_record
causal_record
process_route
resource_relations
supporting_evidence
contradicting_evidence
neutral_evidence
impact_lot_truth
knowledge_asset_links
```

### Document writer view

The document writer may see the approved historical incident or guidance needed to write
one Synthetic asset. It records generator provider, model, prompt version, revision, and
input hash.

### Query writer view

The Query writer sees only a redacted structured Observation view:

```text
detected stage and time
user-visible symptom categories
user-visible measurements
optional incomplete or noisy metadata
requested task
language and style
```

The Query writer must not see:

- target asset IDs;
- root cause or causal Module;
- corrective actions;
- final Evidence chain;
- document title, body, tags, or Chunk text;
- qrels or hard-negative IDs;
- another language version of the same Query.

Qwen may create surface wording only through an explicit opt-in paid path with a bounded
call cap. Deterministic generation remains available for tests, but deterministic Query
templates must not copy document or Canonical Fact sentences.

### Qrel ownership

Python derives provisional qrels from the hidden incident-to-asset graph. Qwen cannot
assign relevance. A versioned review file records human acceptance, rejection, or
adjudication for every graded relation. Evaluation labels require one dataset review;
they do not enter the two-engineer production Knowledge approval workflow.

### Text-overlap audit

Dataset validation must reject:

- exact Query/document sentence reuse;
- causal phrases leaking into Queries;
- target IDs or solution terms in Query text;
- normalized near-duplicate Query variants;
- a calibration/test pair generated from the same incident family or primary target.

The report also publishes lexical-overlap distributions instead of claiming complete
linguistic independence.

## Dataset Difficulty Contracts

### Answerable retrieval Queries

- Hard governance constraints leave a non-trivial candidate pool.
- Every RCA Query has at least three plausible hard negatives.
- Hard negatives share document type and relevant process or symptom attributes.
- Some hard negatives share the observed Module while the relevant asset is cross-Module.
- Some Queries omit Module or equipment metadata.
- Some Queries contain incomplete or incorrect soft hints.

### No-answer Queries

- The candidate pool after hard constraints must be non-empty.
- At least several candidates must share document type and relevant domain terminology.
- Returning zero because an unknown Module was hard-filtered is an invalid V2 test.
- A calibrated abstention or explicit no-supported-hit decision is required.

### End-to-end RCA scenarios

The V2 set must include:

- same-step causes;
- upstream-route causes;
- shared-resource causes;
- observation or metrology artifacts;
- cross-Module causes with plausible same-step distractors;
- conflicting Evidence;
- insufficient Evidence requiring an inconclusive result;
- impact-Lot truth requiring equipment, time, and route intersection;
- unsupported-data notices where a causal lane cannot be queried.

At least 30% of supported RCA scenarios must have a causal Module different from the
detected Module. Scenario count alone is not sufficient; each scenario must contain
multiple plausible causal candidates.

## Partition Policy

Partitions are assigned by `incident_family_id` and primary target asset, never by Query
ID alone.

- No incident family may appear in both calibration and test.
- No translated or paraphrased Query for one primary target may cross partitions.
- Test Queries may grade already-reviewed Calibration assets as secondary relevant
  knowledge; Calibration Queries must never reference Test assets.
- Calibration may fit thresholds or score calibration only.
- Test data remains untouched until the release evaluation command runs.
- Dataset generation fails closed when family or target overlap is detected.

## Primary Metrics

The public metric set remains intentionally small.

### Retrieval headline metrics

- Recall@5
- nDCG@10
- Hard-negative Pairwise Win Rate
- In-scope No-answer Accuracy

MRR and per-lane diagnostics may remain in detailed artifacts but are not required as
headline resume metrics.

Hard-negative Pairwise Win Rate requires every relevant primary asset to outrank each
declared hard negative. A negative merely appearing after rank 1 is not automatically
treated as harmless; Top-K negative presence is reported separately.

### End-to-end RCA headline metrics

- Root Cause Correctness
- Evidence Completeness
- Impact Lot Precision and Recall
- Correct Abstention Rate

Agent execution still retains the existing decision-validity, Evidence Gain, redundancy,
goal-success, and stop-correct diagnostics. They are optimization diagnostics rather than
additional public quality claims.

### Diagnostic slices

The report breaks the primary metrics down by:

- same-Module versus cross-Module cause;
- complete, missing, and noisy soft metadata;
- same-step, upstream, shared-resource, and global discovery lane;
- answerable versus in-scope No-answer;
- language.

Slices explain failures; they do not create a weighted composite score.

## Fair Baselines

All algorithm comparisons must use the same approved corpus, hard constraints, Scope
policy, candidate budgets, qrels, and final K:

```text
Chunk Keyword
BM25-only
Vector-only
Hybrid-RRF
Hybrid-RRF + optional Cross-Encoder
```

The Legacy Case-only Keyword Retriever remains a compatibility row and cannot be used to
claim Hybrid uplift because it lacks SOP, Engineering Note, and cross-language parity.

Causal Scope is evaluated separately through an ablation:

```text
legacy observed-Module hard filter
versus
four-lane causal wide recall
```

The comparison must report same-Module retention and cross-Module recovery together.

## Release Gates

The final report must not expose one ambiguous `PASS`. It publishes four independent
decisions.

### Data-quality gate

- no calibration/test incident-family or target overlap;
- no forbidden causal or solution leakage into Query text;
- every qrel has an accepted review record;
- every No-answer Query has a non-empty in-scope candidate pool;
- required hard negatives and causal scenario categories are present.

### Governance gate

- unapproved Knowledge leakage is zero;
- historical-only Evidence causes zero root-cause promotions;
- unsupported data sources remain explicit;
- source and time Scope boundaries hold.

### Retrieval-quality gate

- Causal wide recall improves cross-Module Recall@5 over the legacy hard filter;
- same-Module Recall@5 does not regress;
- Hybrid-RRF does not regress against the fair Chunk Keyword baseline on Recall@5,
  Hard-negative Pairwise Win Rate, or In-scope No-answer Accuracy;
- optional Reranker promotion still requires a strict nDCG improvement and no primary
  metric regression.

If an algorithm fails its promotion gate, it remains implemented behind a Feature Flag
and is not selected as the default.

### RCA-quality gate

- supported conclusions match hidden root-cause truth and satisfy existing Evidence and
  Hypothesis gates;
- cross-Module scenarios cannot be solved by Knowledge Evidence alone;
- impact-Lot precision and recall are evaluated against hidden lineage truth;
- insufficient and conflicting scenarios stop at the correct downgraded level;
- fixed and controlled compatibility regressions remain available.

No absolute resume number is prewritten. Reports include counts, denominators, failures,
and paired comparisons. Confidence intervals may be included as diagnostics when the
sample size supports them.

## Reporting and Product Surface

Evaluation artifacts must state:

- Synthetic benchmark version and limitations;
- candidate pool size distribution;
- metadata policy used for each run;
- same- versus cross-Module results;
- fair baseline deltas;
- every failed Query and scenario;
- independent data, governance, retrieval, and RCA gate status;
- selected production Feature Flags and their measured reasons.

The Agent Trace adds compact Scope provenance:

```text
Observed at
Search lanes considered
Hard constraints
Soft hints
Selected candidate lane
Scope expansion or unavailable-lane reason
```

The UI must not present retrieval relevance as root-cause confidence.

## Backward Compatibility

- V1 data and reports remain readable and reproducible.
- V1 metric values are labeled `synthetic_v1_regression`, not production accuracy.
- Existing `fixed` and `controlled_react` modes retain historical behavior.
- New Scope fields use strict serialization with deterministic legacy readers.
- `llm_react` cutover remains feature-flagged until all four V2 gates are reported.
- Existing Knowledge approvals, Memory approvals, Evidence contracts, and Hypothesis
  gates do not weaken.

## Security and Data Handling

- No API keys, raw provider prompts, raw Qwen responses, or confidential Fab data enter
  versioned evaluation artifacts.
- Synthetic data is visibly labeled.
- User-ingested Knowledge retains two-engineer publication approval.
- Evaluation qrel review is separate from production Knowledge approval.
- Queries and reports do not expose hidden causal truth before evaluation completes.

## Execution Plan

Implementation is grouped into three reviewable long tasks. Each long task ends with a
separate commit and user-operated push.

### Long Task A - Causal Scope Contracts and Four-lane Runtime

Deliver:

- `ObservationScope`, `CausalSearchScope`, hard/soft constraint, and lane contracts;
- strict serialization and legacy readers;
- intent-specific Scope policy registry;
- process-route and configured shared-resource candidate providers;
- four-lane lexical/vector candidate generation, quotas, fusion, and provenance;
- Planner observation updates without expanding Qwen's authority;
- Agent Trace/API fields for Scope provenance;
- feature flag preserving the current observed-Module behavior;
- contract, unit, integration, API, and frontend tests.

Acceptance:

- an RCA Query observed at Cu CMP retains at least one candidate from every available
  causal lane;
- an explicit "only Cu CMP" request hard-restricts Scope and records that restriction;
- a procedure-guidance Query can remain operation-scoped;
- missing route or resource data is explicit;
- budgets and no-gain boundaries remain effective;
- fixed and controlled baselines do not regress.

### Long Task B - Independent Retrieval and RCA V2 Data

Implementation checkpoint (2026-08-10):

- the deterministic Synthetic V2 baseline contains 18 Incident Families, 28 governed
  Knowledge assets, 32 Retrieval Queries, and 14 RCA scenarios;
- 11 RCA scenarios are supported and 5 of those 11 require cross-Module attribution;
- the smallest post-governance Retrieval candidate pool contains seven assets;
- Query Writer isolation, qrel/partition Python ownership, overlap/leakage checks,
  hard-negative density, No-answer density, shared Fab seed consistency, and V1 fixture
  preservation pass automated validation;
- all 144 graded qrel records and all 14 RCA scenario records have versioned review
  entries accepted by reviewer `ybt` after case-by-case review;
- the human-review checkpoint is complete, so Long Task C may begin after Long Task B
  regression validation and commit.

Deliver:

- structured hidden incident-family schema;
- separate document-writer and redacted Query-writer payloads;
- optional bounded Qwen wording generation;
- independently versioned Retrieval V2 corpus, qrels, partitions, and review artifact;
- shared Synthetic Fab world and RCA V2 scenarios;
- same-step, cross-Module, upstream, shared-resource, metrology-artifact, conflict,
  inconclusive, and impact-Lot families;
- overlap, leakage, partition, candidate-density, hard-negative, and No-answer validators;
- a human review checkpoint before final evaluation.

Acceptance:

- no incident family or primary target crosses calibration/test partitions;
- Query generation cannot access hidden cause or Knowledge text;
- every No-answer Query retains plausible in-scope candidates;
- every RCA Query has required same-scope or causally plausible hard negatives;
- at least 30% of supported RCA scenarios are cross-Module;
- V1 fixtures remain unchanged.

### Long Task C - V2 Evaluation, Release Decision, and Documentation

Deliver:

- fair Retrieval ablation under identical Scope and candidate budgets;
- legacy-hard-filter versus causal-wide Scope ablation;
- Hard-negative Pairwise and in-scope No-answer evaluation;
- end-to-end RCA V2 evaluation over `llm_react`, plus compatibility regressions;
- independent data, governance, retrieval, and RCA gate outputs;
- failed-case artifacts and Scope-path audit;
- Feature Flag release decision for causal-wide retrieval and Reranker;
- README, technical study guide, deployment guide, and interview-safe measured claims;
- full backend/frontend tests and production build.

Acceptance:

- all four gates report explicit status and denominators;
- cross-Module recovery is measured without hiding same-Module regression;
- no unsupported or historical-only Evidence confirms a root cause;
- no unapproved Knowledge enters candidates or Agent context;
- result claims are limited to the V2 Synthetic benchmark;
- the selected runtime strategy exactly matches the measured release decision.

## Commit and Push Boundary

The user performs every push. At the end of each long task, Codex must:

1. list only files belonging to that long task;
2. protect unrelated Qwen/Intent working-tree changes;
3. provide explicit path-scoped `git add --` commands;
4. provide one commit command;
5. remind the user to push `feature/autonomous-qwen-react` with the configured proxy.

## Completion Definition

Batch 22 is complete only when the project can demonstrate:

- an observation at Cu CMP does not assume a Cu CMP cause;
- same-step, upstream, shared-resource, and global candidates are visible within bounds;
- Qwen selects investigative actions without owning hard Scope or Evidence truth;
- Retrieval V2 contains independent, difficult, reviewed Queries and qrels;
- RCA V2 requires observation, action, new Evidence, and replanning;
- cross-Module causes and impact Lots are solved from operational Evidence;
- in-scope No-answer and inconclusive cases are handled without fabricated certainty;
- fair baselines and failed cases are published;
- governance remains fail-closed;
- resume and interview claims use only measured V2 results with Synthetic limitations.
