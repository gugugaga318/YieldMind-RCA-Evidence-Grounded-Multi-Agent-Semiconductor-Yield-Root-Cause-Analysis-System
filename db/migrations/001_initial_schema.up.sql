-- Initial PostgreSQL schema for the Semiconductor Yield RCA MVP.
-- This migration stores analysis-ready structured data only.
-- Raw FDC sensor streams are intentionally out of scope for MVP.

CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lot_master (
    lot_id text PRIMARY KEY,
    product_id text NOT NULL,
    technology text NOT NULL,
    route_id text NOT NULL,
    wafer_qty integer NOT NULL CHECK (wafer_qty > 0 AND wafer_qty <= 25),
    lot_type text NOT NULL DEFAULT 'PRODUCTION',
    priority integer NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    status text NOT NULL CHECK (status IN ('WAITING', 'RUNNING', 'HOLD', 'COMPLETE', 'SCRAP')),
    current_operation_no text,
    created_at timestamptz NOT NULL,
    started_at timestamptz,
    finished_at timestamptz,
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE wafer_master (
    wafer_id text PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_no integer NOT NULL CHECK (wafer_no >= 1 AND wafer_no <= 25),
    slot integer NOT NULL CHECK (slot >= 1 AND slot <= 25),
    status text NOT NULL CHECK (status IN ('ACTIVE', 'HOLD', 'SCRAP', 'COMPLETE')),
    UNIQUE (lot_id, wafer_no),
    UNIQUE (lot_id, slot)
);

CREATE TABLE operation_master (
    operation_no text PRIMARY KEY,
    operation_name text NOT NULL,
    module text NOT NULL,
    process_area text NOT NULL CHECK (process_area IN ('FEOL', 'MOL', 'BEOL', 'TEST')),
    material text,
    canonical_equipment_type text NOT NULL,
    is_critical boolean NOT NULL DEFAULT false
);

CREATE TABLE process_route (
    route_id text NOT NULL,
    product_id text NOT NULL,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    sequence_no integer NOT NULL CHECK (sequence_no > 0),
    module text NOT NULL,
    operation_name text NOT NULL,
    is_critical boolean NOT NULL DEFAULT false,
    PRIMARY KEY (route_id, operation_no),
    UNIQUE (route_id, sequence_no)
);

CREATE TABLE equipment_master (
    equipment_id text PRIMARY KEY,
    equipment_type text NOT NULL,
    module text NOT NULL,
    process_area text NOT NULL CHECK (process_area IN ('FEOL', 'MOL', 'BEOL', 'TEST')),
    material text,
    vendor text,
    model text,
    location text,
    status text NOT NULL CHECK (status IN ('QUALIFIED', 'DOWN', 'MAINTENANCE', 'ENGINEERING')),
    installed_at date
);

CREATE TABLE chamber_master (
    chamber_id text PRIMARY KEY,
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id) ON DELETE CASCADE,
    chamber_name text NOT NULL,
    chamber_type text NOT NULL DEFAULT 'CHAMBER',
    status text NOT NULL CHECK (status IN ('QUALIFIED', 'DOWN', 'MAINTENANCE', 'ENGINEERING')),
    installed_at date,
    UNIQUE (equipment_id, chamber_id),
    UNIQUE (equipment_id, chamber_name)
);

CREATE TABLE equipment_capability (
    capability_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id) ON DELETE CASCADE,
    chamber_id text,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    module text NOT NULL,
    material text,
    recipe_family text NOT NULL,
    qualification_status text NOT NULL CHECK (
        qualification_status IN ('QUALIFIED', 'ENGINEERING', 'DISQUALIFIED')
    ),
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    UNIQUE (equipment_id, chamber_id, operation_no, recipe_family)
);

CREATE TABLE recipe_master (
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    recipe_name text NOT NULL,
    module text NOT NULL,
    recipe_family text NOT NULL,
    status text NOT NULL CHECK (status IN ('ACTIVE', 'ENGINEERING', 'RETIRED')),
    owner text,
    released_at timestamptz,
    PRIMARY KEY (recipe_id, recipe_version)
);

CREATE TABLE recipe_history (
    recipe_history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text,
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    executed_at timestamptz NOT NULL,
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    FOREIGN KEY (recipe_id, recipe_version)
        REFERENCES recipe_master(recipe_id, recipe_version)
);

CREATE TABLE process_history (
    history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    route_id text NOT NULL,
    operation_no text NOT NULL,
    operation_name text NOT NULL,
    module text NOT NULL,
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text,
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    operator_id text,
    process_result text NOT NULL CHECK (process_result IN ('PASS', 'FAIL', 'REWORK', 'ABORT')),
    FOREIGN KEY (route_id, operation_no)
        REFERENCES process_route(route_id, operation_no),
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    FOREIGN KEY (recipe_id, recipe_version)
        REFERENCES recipe_master(recipe_id, recipe_version),
    CHECK (ended_at >= started_at)
);

CREATE TABLE hold_history (
    hold_id text PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    hold_type text NOT NULL CHECK (
        hold_type IN ('ENGINEERING', 'QUALITY', 'PROCESS', 'EQUIPMENT', 'MATERIAL')
    ),
    hold_code text NOT NULL,
    hold_reason text NOT NULL,
    hold_comment text NOT NULL,
    created_by text,
    created_at timestamptz NOT NULL,
    released_by text,
    released_at timestamptz,
    release_comment text,
    CHECK (released_at IS NULL OR released_at >= created_at)
);

CREATE TABLE fdc_feature (
    feature_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text,
    recipe_id text NOT NULL,
    recipe_version text NOT NULL,
    parameter_name text NOT NULL,
    baseline_value numeric,
    observed_value numeric NOT NULL,
    delta_percent numeric,
    unit text NOT NULL,
    trend_slope numeric,
    ooc_flag boolean NOT NULL DEFAULT false,
    severity text NOT NULL CHECK (severity IN ('NORMAL', 'LOW', 'MEDIUM', 'HIGH')),
    measured_at timestamptz NOT NULL,
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id),
    FOREIGN KEY (recipe_id, recipe_version)
        REFERENCES recipe_master(recipe_id, recipe_version)
);

CREATE TABLE ooc_event (
    ooc_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_id bigint REFERENCES fdc_feature(feature_id) ON DELETE SET NULL,
    equipment_id text NOT NULL REFERENCES equipment_master(equipment_id),
    chamber_id text,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    parameter_name text NOT NULL,
    alarm_type text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    triggered_at timestamptz NOT NULL,
    description text NOT NULL,
    FOREIGN KEY (equipment_id, chamber_id)
        REFERENCES chamber_master(equipment_id, chamber_id)
);

CREATE TABLE defect_summary (
    defect_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    inspection_operation_no text NOT NULL REFERENCES operation_master(operation_no),
    defect_type text NOT NULL,
    defect_count integer NOT NULL CHECK (defect_count >= 0),
    defect_density numeric CHECK (defect_density IS NULL OR defect_density >= 0),
    pattern_type text NOT NULL,
    location_region text,
    inspected_at timestamptz NOT NULL
);

CREATE TABLE metrology_result (
    metrology_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text NOT NULL REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    operation_no text NOT NULL REFERENCES operation_master(operation_no),
    measurement_stage text NOT NULL CHECK (
        measurement_stage IN ('PRE_PROCESS', 'POST_PROCESS', 'PRE_CMP', 'POST_CMP')
    ),
    metric_name text NOT NULL,
    measured_value numeric NOT NULL,
    unit text NOT NULL,
    spec_low numeric,
    spec_high numeric,
    pass_fail boolean NOT NULL,
    metrology_tool text NOT NULL,
    measured_at timestamptz NOT NULL,
    CHECK (spec_low IS NULL OR spec_high IS NULL OR spec_high >= spec_low)
);

CREATE TABLE wat_result (
    wat_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id text NOT NULL REFERENCES lot_master(lot_id) ON DELETE CASCADE,
    wafer_id text REFERENCES wafer_master(wafer_id) ON DELETE CASCADE,
    test_item text NOT NULL,
    parameter_name text NOT NULL,
    measured_value numeric,
    spec_low numeric,
    spec_high numeric,
    pass_fail boolean NOT NULL,
    fail_mode text,
    tested_at timestamptz NOT NULL,
    CHECK (spec_low IS NULL OR spec_high IS NULL OR spec_high >= spec_low)
);

CREATE TABLE rca_case (
    case_id text PRIMARY KEY,
    title text NOT NULL,
    technology text,
    module text NOT NULL,
    equipment_type text,
    symptom text NOT NULL,
    root_cause text NOT NULL,
    solution text NOT NULL,
    confidence numeric CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_document (
    document_id text PRIMARY KEY,
    case_id text REFERENCES rca_case(case_id) ON DELETE SET NULL,
    document_type text NOT NULL CHECK (document_type IN ('RCA_CASE', 'SOP', 'ENGINEERING_NOTE')),
    title text NOT NULL,
    content text NOT NULL,
    tags text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_lot_master_product_time ON lot_master(product_id, created_at);
CREATE INDEX idx_wafer_master_lot ON wafer_master(lot_id);
CREATE INDEX idx_process_route_product_sequence ON process_route(product_id, route_id, sequence_no);
CREATE INDEX idx_process_history_lot_operation ON process_history(lot_id, operation_no);
CREATE INDEX idx_process_history_equipment_chamber ON process_history(equipment_id, chamber_id);
CREATE INDEX idx_process_history_recipe ON process_history(recipe_id, recipe_version);
CREATE INDEX idx_recipe_history_lot_operation ON recipe_history(lot_id, operation_no);
CREATE INDEX idx_hold_history_lot_created ON hold_history(lot_id, created_at);
CREATE INDEX idx_fdc_feature_equipment_parameter ON fdc_feature(equipment_id, chamber_id, parameter_name);
CREATE INDEX idx_fdc_feature_lot_operation ON fdc_feature(lot_id, operation_no);
CREATE INDEX idx_ooc_event_equipment_time ON ooc_event(equipment_id, chamber_id, triggered_at);
CREATE INDEX idx_defect_summary_lot_type ON defect_summary(lot_id, defect_type);
CREATE INDEX idx_metrology_result_lot_metric ON metrology_result(lot_id, metric_name);
CREATE INDEX idx_metrology_result_wafer_operation ON metrology_result(wafer_id, operation_no);
CREATE INDEX idx_wat_result_lot_fail_mode ON wat_result(lot_id, fail_mode);
CREATE INDEX idx_rca_case_module_symptom ON rca_case(module, equipment_type);
CREATE INDEX idx_knowledge_document_case ON knowledge_document(case_id);
CREATE INDEX idx_knowledge_document_tags ON knowledge_document USING gin(tags);

INSERT INTO schema_migrations(version) VALUES ('001_initial_schema');
