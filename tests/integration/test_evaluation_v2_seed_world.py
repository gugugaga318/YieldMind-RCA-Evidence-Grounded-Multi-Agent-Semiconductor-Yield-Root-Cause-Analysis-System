from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_scope import (  # noqa: E402
    CausalLane,
    ObservationScope,
    RepositoryCausalContextProvider,
)
from yield_rca_core.repositories import SUPPORTED_TABLES, CsvFabRepository  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "causal_scope_v2"
SCENARIOS_PATH = ROOT / "data" / "evaluation" / "rca_scenarios_v2.json"


class EvaluationV2SeedWorldIntegrationTest(unittest.TestCase):
    def test_shared_seed_world_satisfies_repository_contract(self) -> None:
        repository = CsvFabRepository(SEED_DIR)
        for table_name in sorted(SUPPORTED_TABLES):
            rows = repository.rows(table_name)
            if table_name not in {
                "spc_baseline_profile",
                "spc_excursion",
                "spc_excursion_lot",
            }:
                self.assertTrue(rows, table_name)

    def test_every_scenario_source_lot_has_bounded_route_and_scope_lanes(self) -> None:
        repository = CsvFabRepository(SEED_DIR)
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        lots = {row["lot_id"]: row for row in repository.rows("lot_master")}
        equipment = {row["equipment_id"]: row for row in repository.rows("equipment_master")}
        provider = RepositoryCausalContextProvider(repository)

        for scenario in scenarios:
            observation = scenario["observation_scope"]
            source_lot_id = scenario["source_lot_id"]
            self.assertIn(source_lot_id, lots)
            equipment_row = equipment[observation["detected_equipment_id"]]
            scope = ObservationScope(
                source_lot_id=source_lot_id,
                product_id=observation["product_id"],
                detected_module=observation["detected_module"],
                detected_operation=observation["detected_operation"],
                detected_equipment_id=observation["detected_equipment_id"],
                detected_equipment_type=equipment_row["equipment_type"],
                detected_at=observation["detected_at"],
                symptom_types=tuple(observation["symptom_types"]),
            )
            contexts = {item.lane: item for item in provider.lane_contexts(scope)}
            self.assertTrue(contexts[CausalLane.SAME_STEP.value].available)
            self.assertTrue(contexts[CausalLane.UPSTREAM_ROUTE.value].available)
            self.assertTrue(contexts[CausalLane.SHARED_RESOURCE.value].available)
            self.assertTrue(contexts[CausalLane.GLOBAL_SEMANTIC.value].available)

    def test_impact_truth_matches_causal_equipment_and_product_exposure(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        ground_truth = json.loads((SEED_DIR / "ground_truth.json").read_text(encoding="utf-8"))
        truth_by_family = {
            item["incident_family_id"]: item for item in ground_truth["incident_families"]
        }
        repository = CsvFabRepository(SEED_DIR)
        histories = repository.rows("process_history")
        lot_products = {row["lot_id"]: row["product_id"] for row in repository.rows("lot_master")}

        for scenario in scenarios:
            truth = truth_by_family[scenario["incident_family_id"]]
            self.assertEqual(scenario["expected_impact_lots"], truth["impact_lots"])
            source_product = lot_products[scenario["source_lot_id"]]
            for impact_lot in scenario["expected_impact_lots"]:
                self.assertEqual(lot_products[impact_lot], source_product)
                self.assertTrue(any(row["lot_id"] == impact_lot for row in histories))


if __name__ == "__main__":
    unittest.main()
