-- Preserve the tester provenance required to distinguish process failures
-- from WAT equipment artifacts and to audit independent retests.

ALTER TABLE wat_result
    ADD COLUMN test_equipment_id text
        REFERENCES equipment_master(equipment_id) ON DELETE SET NULL;

CREATE INDEX idx_wat_result_test_equipment
    ON wat_result(test_equipment_id, tested_at);

INSERT INTO schema_migrations(version) VALUES ('010_wat_test_equipment_provenance');
