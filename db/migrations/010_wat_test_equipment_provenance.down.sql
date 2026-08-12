DELETE FROM schema_migrations WHERE version = '010_wat_test_equipment_provenance';

DROP INDEX IF EXISTS idx_wat_result_test_equipment;

ALTER TABLE wat_result
    DROP COLUMN IF EXISTS test_equipment_id;
