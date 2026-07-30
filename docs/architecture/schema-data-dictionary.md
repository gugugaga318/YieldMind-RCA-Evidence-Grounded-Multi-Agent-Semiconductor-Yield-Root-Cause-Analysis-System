# PostgreSQL Schema Data Dictionary

This document describes the MVP database schema introduced by
`db/migrations/001_initial_schema.up.sql`.

## Scope

The schema stores analysis-ready manufacturing data for the first golden RCA
case. It does not store raw FDC sensor streams.

## MES Tables

### `lot_master`

Lot-level production context.

Key fields:

- `lot_id`: primary key.
- `product_id`: product identifier, such as `40N_SOC`.
- `technology`: technology node.
- `route_id`: manufacturing route.
- `wafer_qty`: number of wafers in the lot.
- `status`: lot state.
- `current_operation_no`: current or last known operation.

### `wafer_master`

Wafer-level identity and slot tracking.

Key constraints:

- `(lot_id, wafer_no)` is unique.
- `(lot_id, slot)` is unique.

### `operation_master`

Canonical operation definitions.

This table lets the dataset represent realistic critical operations without
generating a full hundreds-step route.

### `process_route`

Product route definition.

Key constraints:

- `(route_id, operation_no)` is the route operation key.
- `(route_id, sequence_no)` is unique.

### `equipment_master`

Equipment identity and process category.

The schema keeps `equipment_type`, `module`, `process_area`, and `material` so
CMP equipment can be separated by process segment and material.

### `chamber_master`

Chamber or chamber-like unit under equipment.

For CMP, MVP may use chamber-like IDs to represent station/head/platen level.

### `equipment_capability`

Defines which equipment/chamber can run which operation and recipe family.

This table is mandatory for MVP because STI CMP, W CMP, and Cu CMP must not be
treated as interchangeable.

### `recipe_master`

Recipe identity and version metadata.

Primary key:

```text
(recipe_id, recipe_version)
```

### `recipe_history`

Lot/wafer recipe execution history.

This table records what recipe version was actually used on a lot at a specific
operation, equipment, and chamber.

### `process_history`

The manufacturing genealogy backbone.

It links:

```text
lot -> wafer -> route operation -> equipment -> chamber -> recipe version
```

### `hold_history`

MES hold records.

`hold_comment` is required because it is valuable unstructured engineering
evidence for RCA.

## FDC Tables

### `fdc_feature`

Feature-level FDC summary. It stores baseline and observed values, not raw
sensor traces.

Important fields:

- `parameter_name`
- `baseline_value`
- `observed_value`
- `delta_percent`
- `trend_slope`
- `ooc_flag`
- `severity`

### `ooc_event`

Out-of-control event summary linked optionally to `fdc_feature`.

## Defect / WAT Tables

### `defect_summary`

Structured defect summary for KLA-like inspection output.

### `wat_result`

Electrical test result and fail mode summary.

## Knowledge Tables

### `rca_case`

Structured metadata for historical RCA cases.

### `knowledge_document`

Text documents for MVP keyword/tag retrieval.

MVP uses metadata and keyword/tag retrieval. pgvector or Chroma is deferred to a
later phase.

### `memory_candidate`

Pending engineering memory derived from a supported RCA and its Improvement
finding. It stores the evidence IDs and recommendations but is not searchable
historical knowledge.

### `memory_approval`

Append-only engineer decisions for one candidate. `(candidate_id, engineer_id)`
is unique, which enforces independent approvers. Publication requires two
approvals and, when Recipe recommendations exist, a Process Engineer among the
two approvers.

Published rows are inserted into `rca_case` and `knowledge_document` with
`validation_status = CONFIRMED`. `source_candidate_id` links the historical case
back to its approval record.
