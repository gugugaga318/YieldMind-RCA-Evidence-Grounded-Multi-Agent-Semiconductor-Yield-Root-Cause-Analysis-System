-- Step 20 deterministic SPC evidence, OOC trigger ownership, and excursion scope.

CREATE TABLE spc_baseline_profile (
    baseline_id text PRIMARY KEY,
    source_table text NOT NULL CHECK (source_table IN ('fdc_feature', 'metrology_result', 'wat_result')),
    chart_type text NOT NULL CHECK (chart_type IN ('I_MR', 'XBAR_S', 'XBAR_R', 'P')),
    product_id text NOT NULL,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text NOT NULL,
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    parameter_name text NOT NULL,
    unit text NOT NULL,
    baseline_start timestamptz NOT NULL,
    baseline_end timestamptz NOT NULL,
    minimum_sample_count integer NOT NULL CHECK (minimum_sample_count >= 2),
    spec_lower numeric,
    spec_upper numeric,
    status text NOT NULL DEFAULT 'REFERENCE' CHECK (status IN ('REFERENCE', 'RETIRED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    FOREIGN KEY (recipe_id, recipe_version)
        REFERENCES recipe_master(recipe_id, recipe_version),
    CHECK (baseline_end >= baseline_start),
    CHECK (spec_lower IS NULL OR spec_upper IS NULL OR spec_upper >= spec_lower)
);

CREATE TABLE spc_excursion (
    excursion_id text PRIMARY KEY,
    baseline_id text REFERENCES spc_baseline_profile(baseline_id) ON DELETE SET NULL,
    product_id text NOT NULL,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text NOT NULL,
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    parameter_name text NOT NULL,
    excursion_start timestamptz NOT NULL,
    triggered_at timestamptz NOT NULL,
    excursion_end timestamptz,
    description text NOT NULL,
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    FOREIGN KEY (recipe_id, recipe_version)
        REFERENCES recipe_master(recipe_id, recipe_version),
    CHECK (triggered_at >= excursion_start),
    CHECK (excursion_end IS NULL OR excursion_end >= triggered_at)
);

ALTER TABLE ooc_event
    ADD COLUMN event_key text UNIQUE,
    ADD COLUMN event_source text NOT NULL DEFAULT 'FDC'
        CHECK (event_source IN ('FDC', 'SPC')),
    ADD COLUMN trigger_lot_id text REFERENCES lot_master(lot_id),
    ADD COLUMN trigger_wafer_id text REFERENCES wafer_master(wafer_id),
    ADD COLUMN trigger_hold_id text REFERENCES hold_history(hold_id),
    ADD COLUMN excursion_id text REFERENCES spc_excursion(excursion_id),
    ADD COLUMN spc_rule_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    ADD CONSTRAINT ck_spc_ooc_trigger_context CHECK (
        event_source <> 'SPC'
        OR (
            event_key IS NOT NULL
            AND trigger_lot_id IS NOT NULL
            AND trigger_hold_id IS NOT NULL
            AND excursion_id IS NOT NULL
            AND cardinality(spc_rule_codes) > 0
        )
    );

CREATE TABLE spc_excursion_lot (
    excursion_id text NOT NULL REFERENCES spc_excursion(excursion_id) ON DELETE CASCADE,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    scope_role text NOT NULL CHECK (scope_role IN ('TRIGGER', 'IMPACT')),
    hold_id text NOT NULL REFERENCES hold_history(hold_id),
    selection_reason text NOT NULL,
    linked_at timestamptz NOT NULL,
    PRIMARY KEY (excursion_id, lot_id),
    UNIQUE (excursion_id, hold_id)
);

CREATE INDEX idx_spc_baseline_lookup ON spc_baseline_profile(
    product_id, operation_no, equipment_id, chamber_id,
    recipe_id, recipe_version, parameter_name, status
);
CREATE INDEX idx_spc_excursion_scope_time ON spc_excursion(
    equipment_id, chamber_id, operation_no, excursion_start, triggered_at
);
CREATE INDEX idx_spc_excursion_lot_lot ON spc_excursion_lot(lot_id, excursion_id);
CREATE UNIQUE INDEX uq_spc_excursion_single_trigger
    ON spc_excursion_lot(excursion_id)
    WHERE scope_role = 'TRIGGER';
CREATE INDEX idx_ooc_event_trigger_lot ON ooc_event(trigger_lot_id, triggered_at);

INSERT INTO schema_migrations(version) VALUES ('004_advanced_spc_analytics');
