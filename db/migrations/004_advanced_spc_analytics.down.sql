-- Roll back Step 20 advanced SPC analytics schema.

DO $$
BEGIN
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
        DELETE FROM schema_migrations WHERE version = '004_advanced_spc_analytics';
    END IF;
END
$$;

DROP INDEX IF EXISTS idx_ooc_event_trigger_lot;
DROP INDEX IF EXISTS idx_spc_excursion_lot_lot;
DROP INDEX IF EXISTS uq_spc_excursion_single_trigger;
DROP INDEX IF EXISTS idx_spc_excursion_scope_time;
DROP INDEX IF EXISTS idx_spc_baseline_lookup;
DROP TABLE IF EXISTS spc_excursion_lot;

ALTER TABLE IF EXISTS ooc_event
    DROP CONSTRAINT IF EXISTS ck_spc_ooc_trigger_context,
    DROP COLUMN IF EXISTS spc_rule_codes,
    DROP COLUMN IF EXISTS excursion_id,
    DROP COLUMN IF EXISTS trigger_hold_id,
    DROP COLUMN IF EXISTS trigger_wafer_id,
    DROP COLUMN IF EXISTS trigger_lot_id,
    DROP COLUMN IF EXISTS event_source,
    DROP COLUMN IF EXISTS event_key;

DROP TABLE IF EXISTS spc_excursion;
DROP TABLE IF EXISTS spc_baseline_profile;
