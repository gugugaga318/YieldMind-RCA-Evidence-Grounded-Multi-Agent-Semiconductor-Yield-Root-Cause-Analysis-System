# Data Architecture

## Core Principle

Fab data is the world that agents investigate. It must exist before runtime RCA execution.

Synthetic Fab data generation is an offline seed process:

```text
generate_synthetic_fab_data.py
        |
        v
seed files
        |
        v
seed_database.py
        |
        v
PostgreSQL
```

FastAPI must not generate synthetic Fab data at request time or application startup.

## MVP Database Schemas

### MES

Required MVP tables:

```text
lot_master
wafer_master
process_route
operation_master
process_history
equipment_master
equipment_capability
chamber_master
recipe_master
recipe_history
hold_history
```

Important modeling rules:

- `process_history` is the manufacturing genealogy backbone.
- `equipment_capability` is required to prevent invalid equipment assignment.
- STI CMP, ILD CMP, IMD CMP, W CMP, and Cu CMP cannot be treated as interchangeable equipment.
- `recipe_master` constrains recipe identity and version.
- `recipe_history` records actual recipe execution by lot/operation/equipment/chamber.
- `hold_history.hold_comment` is a key non-structured engineering signal.

### FDC

Required MVP tables:

```text
fdc_feature
ooc_event
```

Step 20 adds RCA-oriented SPC evidence tables:

```text
spc_baseline_profile
spc_excursion
spc_excursion_lot
```

One SPC OOC owns exactly one Trigger Lot and one Trigger Hold. Other exposed
Lots belong to the wider excursion as Impact Lots and reference independent
containment Hold records. The RCA application reads these records but never
executes MES Hold or Release.

MVP stores feature summaries, not raw sensor streams.

Recommended FDC feature fields:

```text
feature_id
lot_id
wafer_id
operation_no
equipment_id
chamber_id
recipe_id
recipe_version
parameter_name
baseline_value
observed_value
delta_percent
unit
trend_slope
ooc_flag
severity
timestamp
```

### Defect / WAT

Required MVP tables:

```text
defect_summary
wat_result
```

MVP uses structured defect and WAT data.

Vision/image analysis is reserved for future work.

### Knowledge

Required MVP tables:

```text
rca_case
knowledge_document
```

Step 19 adds controlled knowledge publication tables:

```text
memory_candidate
memory_approval
```

Only an RCA case published after two distinct engineer approvals has
`validation_status = CONFIRMED`. Knowledge Agent excludes unconfirmed records.

MVP retrieval:

```text
PostgreSQL metadata + keyword/tag retrieval
```

Future retrieval:

```text
pgvector or Chroma semantic index
```

## Golden Dataset Minimum Contents

The first golden dataset must contain:

```text
normal lots
affected lots
lot_master
wafer_master
process_route
operation_master
process_history
equipment_master
equipment_capability
chamber_master
recipe_master
recipe_history
hold_history with hold_comment
fdc_feature
ooc_event
defect_summary
wat_result
rca_case
knowledge_document or equivalent case text
ground_truth.json
```

## PostgreSQL Execution Environment

Step 3 may use a minimal PostgreSQL environment:

- local PostgreSQL
- testcontainers
- temporary single-container postgres

This is allowed for schema and migration testing.

It is not the same as building the full application Docker Compose, which is intentionally delayed until after the pure Python workflow works.
