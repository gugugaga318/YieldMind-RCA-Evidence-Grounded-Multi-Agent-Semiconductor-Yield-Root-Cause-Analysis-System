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

    def test_metrology_artifact_is_separated_from_real_process_impact(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_004"
        )
        repository = CsvFabRepository(SEED_DIR)
        source_lot = scenario["source_lot_id"]

        self.assertEqual(scenario["expected_impact_lots"], [])
        self.assertEqual(
            scenario["secondary_relevant_asset_ids"],
            ["RCA_V2_007", "RCA_V2_003"],
        )

        metrology = [
            row for row in repository.rows("metrology_result") if row["lot_id"] == source_lot
        ]
        self.assertTrue(
            any(
                row["metrology_tool"] == "MET_FILM_03" and row["pass_fail"] == "false"
                for row in metrology
            )
        )
        self.assertTrue(
            any(
                row["metrology_tool"] == "MET_FILM_04"
                and row["measurement_stage"] == "INDEPENDENT_REMEASURE"
                and row["pass_fail"] == "true"
                for row in metrology
            )
        )

        process_fdc = [
            row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] == source_lot
            and row["parameter_name"] in {"removal_rate_proxy", "deposition_rate_proxy"}
        ]
        self.assertEqual(len(process_fdc), 2)
        self.assertTrue(all(row["ooc_flag"] == "false" for row in process_fdc))

        wat = [row for row in repository.rows("wat_result") if row["lot_id"] == source_lot]
        self.assertEqual(len(wat), 1)
        self.assertEqual(wat[0]["pass_fail"], "true")

    def test_real_cvd_thinning_remains_a_strong_alternative_hypothesis(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_007"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_07_CTRL_01", "LOT_V2_07_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertEqual(scenario["expected_causal_module"], "CVD")
        self.assertIn("chamber-temperature drift", scenario["expected_root_cause"])
        self.assertEqual(len(scenario["expected_impact_lots"]), 2)
        self.assertEqual(scenario["secondary_relevant_asset_ids"], ["RCA_V2_004"])
        self.assertIn("EV_V2_07_IMPACT_CORRELATION", scenario["required_evidence_ids"])
        self.assertIn("EV_V2_07_RECOVERY_CONTROLS", scenario["required_evidence_ids"])

        cvd_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "5000"
        ]
        self.assertEqual({row["lot_id"] for row in cvd_history}, comparison_lots)
        self.assertTrue(all(row["equipment_id"] == "CVD_FILM_07" for row in cvd_history))

        fdc = {
            row["lot_id"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] in comparison_lots
            and row["parameter_name"] == "chamber_temperature_bias"
        }
        self.assertTrue(all(fdc[lot_id]["ooc_flag"] == "true" for lot_id in affected_lots))
        self.assertTrue(all(fdc[lot_id]["ooc_flag"] == "false" for lot_id in control_lots))

        metrology = {
            row["lot_id"]: row
            for row in repository.rows("metrology_result")
            if row["lot_id"] in comparison_lots
            and row["metric_name"] == "deposited film thickness loss"
        }
        self.assertEqual(
            {lot_id: float(metrology[lot_id]["measured_value"]) for lot_id in affected_lots},
            {
                "LOT_V2_07_SRC": 18.0,
                "LOT_V2_07_IMP_01": 16.0,
                "LOT_V2_07_IMP_02": 14.0,
            },
        )
        self.assertTrue(
            all(metrology[lot_id]["pass_fail"] == "false" for lot_id in affected_lots)
        )
        self.assertTrue(
            all(metrology[lot_id]["pass_fail"] == "true" for lot_id in control_lots)
        )

        wat = {
            row["lot_id"]: row
            for row in repository.rows("wat_result")
            if row["lot_id"] in comparison_lots
        }
        self.assertTrue(all(wat[lot_id]["pass_fail"] == "false" for lot_id in affected_lots))
        self.assertTrue(all(wat[lot_id]["pass_fail"] == "true" for lot_id in control_lots))

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_007_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_007"], 3)
        self.assertEqual(judgments["RCA_V2_004"], 2)
        self.assertEqual(judgments["RCA_V2_010"], 1)

    def test_conflicting_cmp_signals_remain_inconclusive_without_impact_scope(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_005"
        )
        repository = CsvFabRepository(SEED_DIR)
        source_lot = scenario["source_lot_id"]
        fdc_by_name = {
            row["parameter_name"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] == source_lot
        }

        self.assertEqual(scenario["expected_status"], "inconclusive")
        self.assertEqual(scenario["expected_impact_lots"], [])
        self.assertIn("WARN_RCA_CONFLICTING_EVIDENCE", scenario["required_warning_ids"])
        self.assertEqual(
            fdc_by_name["slurry_pump_pressure_drop_index"]["ooc_flag"], "true"
        )
        self.assertEqual(
            fdc_by_name["independent_slurry_flow_meter"]["ooc_flag"], "false"
        )
        self.assertEqual(fdc_by_name["endpoint_duration_proxy"]["ooc_flag"], "false")

        ooc = [
            row
            for row in repository.rows("ooc_event")
            if row["feature_id"] == "OOC_IF_V2_005"
        ]
        self.assertEqual(len(ooc), 1)
        self.assertIn("independent evidence is required", ooc[0]["description"])

    def test_missing_dry_etch_fdc_blocks_attribution_despite_normal_controls(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_006"
        )
        repository = CsvFabRepository(SEED_DIR)
        source_lot = scenario["source_lot_id"]
        control_lots = {"LOT_V2_06_CTRL_01", "LOT_V2_06_CTRL_02"}
        comparison_lots = {source_lot, *control_lots}

        self.assertEqual(scenario["expected_status"], "inconclusive")
        self.assertEqual(scenario["expected_causal_module"], "Unresolved")
        self.assertEqual(scenario["expected_impact_lots"], [])
        self.assertEqual(
            scenario["unavailable_data_sources"],
            ["Dry Etch FDC feature history"],
        )
        self.assertIn("WARN_UNSUPPORTED_DATA_SOURCE", scenario["required_warning_ids"])

        dry_etch_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "2000"
        ]
        self.assertEqual({row["lot_id"] for row in dry_etch_history}, comparison_lots)
        self.assertTrue(
            all(row["equipment_id"] == "ETCH_METAL_08" for row in dry_etch_history)
        )
        self.assertFalse(
            any(row["lot_id"] == source_lot for row in repository.rows("fdc_feature"))
        )

        defects = {
            row["lot_id"]: int(row["defect_count"])
            for row in repository.rows("defect_summary")
            if row["lot_id"] in comparison_lots
        }
        self.assertGreater(defects[source_lot], max(defects[lot_id] for lot_id in control_lots))

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_006_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_006"], 3)
        self.assertEqual(judgments["RCA_V2_004"], 1)

    def test_upstream_plating_cause_is_discovered_after_cmp_observation(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_008"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_08_CTRL_01", "LOT_V2_08_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertNotIn("incoming copper", scenario["query"].lower())
        self.assertEqual(scenario["expected_causal_module"], "Electroplating")
        self.assertEqual(scenario["expected_discovery_lane"], "upstream_route")
        self.assertEqual(
            scenario["secondary_relevant_asset_ids"],
            ["RCA_V2_002", "RCA_V2_003"],
        )
        self.assertIn("EV_V2_08_PRE_CMP_PROFILE", scenario["required_evidence_ids"])
        self.assertIn("EV_V2_08_DETECTED_STEP_NORMAL", scenario["required_evidence_ids"])

        plating_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "6000"
        ]
        self.assertEqual({row["lot_id"] for row in plating_history}, comparison_lots)
        self.assertTrue(all(row["equipment_id"] == "PLATE_CU_09" for row in plating_history))

        plating_fdc = {
            row["lot_id"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] in comparison_lots
            and row["parameter_name"] == "electrolyte_agitation_speed"
        }
        self.assertTrue(
            all(plating_fdc[lot_id]["ooc_flag"] == "true" for lot_id in affected_lots)
        )
        self.assertTrue(
            all(plating_fdc[lot_id]["ooc_flag"] == "false" for lot_id in control_lots)
        )
        cmp_fdc = [
            row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] == scenario["source_lot_id"]
            and row["equipment_id"] == "CMP_CU_21"
        ]
        self.assertEqual(
            {row["parameter_name"] for row in cmp_fdc},
            {"removal_rate_proxy", "endpoint_duration_proxy"},
        )
        self.assertTrue(all(row["ooc_flag"] == "false" for row in cmp_fdc))

        pre_cmp = {
            row["lot_id"]: row
            for row in repository.rows("metrology_result")
            if row["lot_id"] in comparison_lots and row["measurement_stage"] == "PRE_CMP"
        }
        self.assertTrue(
            all(float(pre_cmp[lot_id]["measured_value"]) == 33.0 for lot_id in affected_lots)
        )
        self.assertTrue(
            all(float(pre_cmp[lot_id]["measured_value"]) == 2.0 for lot_id in control_lots)
        )

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_008_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_008"], 3)
        self.assertEqual(judgments["RCA_V2_002"], 2)
        self.assertEqual(judgments["RCA_V2_003"], 2)
        self.assertEqual(judgments["RCA_V2_001"], 1)

    def test_shared_rinse_chamber_scopes_arc_scratch_impact_lots(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_009"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_09_CTRL_01", "LOT_V2_09_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertNotIn("rinse", scenario["query"].lower())
        self.assertEqual(scenario["expected_discovery_lane"], "shared_resource")
        self.assertEqual(
            scenario["secondary_relevant_asset_ids"],
            ["RCA_V2_001", "RCA_V2_003"],
        )
        self.assertIn("EV_V2_09_IMPACT_CORRELATION", scenario["required_evidence_ids"])
        self.assertIn("EV_V2_09_RECOVERY_CONTROLS", scenario["required_evidence_ids"])

        cmp_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "6400"
        ]
        self.assertEqual({row["lot_id"] for row in cmp_history}, comparison_lots)
        self.assertTrue(all(row["equipment_id"] == "CMP_CU_22" for row in cmp_history))
        self.assertTrue(all(row["chamber_id"] == "CMP_CU_22_CH01" for row in cmp_history))

        particle_fdc = {
            row["lot_id"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] in comparison_lots
            and row["parameter_name"] == "rinse_nozzle_particle_index"
        }
        self.assertTrue(
            all(particle_fdc[lot_id]["ooc_flag"] == "true" for lot_id in affected_lots)
        )
        self.assertTrue(
            all(particle_fdc[lot_id]["ooc_flag"] == "false" for lot_id in control_lots)
        )
        alternative_fdc = [
            row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] == scenario["source_lot_id"]
            and row["parameter_name"]
            in {"carrier_head_orbit_runout", "conditioner_motor_torque"}
        ]
        self.assertEqual(len(alternative_fdc), 2)
        self.assertTrue(all(row["ooc_flag"] == "false" for row in alternative_fdc))

        defects = {
            row["lot_id"]: int(row["defect_count"])
            for row in repository.rows("defect_summary")
            if row["lot_id"] in comparison_lots
        }
        self.assertEqual(
            {lot_id: defects[lot_id] for lot_id in affected_lots},
            {
                "LOT_V2_09_SRC": 14,
                "LOT_V2_09_IMP_01": 13,
                "LOT_V2_09_IMP_02": 12,
                "LOT_V2_09_IMP_03": 11,
            },
        )
        self.assertTrue(all(defects[lot_id] == 2 for lot_id in control_lots))

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_009_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_009"], 3)
        self.assertEqual(judgments["RCA_V2_001"], 2)
        self.assertEqual(judgments["RCA_V2_003"], 2)
        self.assertEqual(judgments["RCA_V2_002"], 1)

    def test_wat_vt_shift_traces_back_to_implant_and_reproduces_on_retest(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_010"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_10_CTRL_01", "LOT_V2_10_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertEqual(scenario["expected_causal_module"], "Ion Implant")
        self.assertEqual(scenario["secondary_relevant_asset_ids"], ["RCA_V2_011"])
        self.assertIn(
            "EV_V2_10_INDEPENDENT_WAT_RETEST", scenario["required_evidence_ids"]
        )
        self.assertIn(
            "EV_V2_10_ELECTRICAL_CONTROLS_NORMAL", scenario["required_evidence_ids"]
        )

        implant_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "3000"
        ]
        self.assertEqual({row["lot_id"] for row in implant_history}, comparison_lots)
        self.assertTrue(all(row["equipment_id"] == "IMP_WELL_03" for row in implant_history))

        implant_fdc = {
            row["lot_id"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] in comparison_lots
            and row["parameter_name"] == "beam_dose_integrator"
        }
        self.assertTrue(
            all(implant_fdc[lot_id]["ooc_flag"] == "true" for lot_id in affected_lots)
        )
        self.assertTrue(
            all(implant_fdc[lot_id]["ooc_flag"] == "false" for lot_id in control_lots)
        )

        wat_rows = [
            row for row in repository.rows("wat_result") if row["lot_id"] in comparison_lots
        ]
        primary = {
            row["lot_id"]: row
            for row in wat_rows
            if row["test_item"] == "V2_PRIMARY_ELECTRICAL"
        }
        self.assertEqual(
            {lot_id: float(primary[lot_id]["measured_value"]) for lot_id in affected_lots},
            {
                "LOT_V2_10_SRC": 47.0,
                "LOT_V2_10_IMP_01": 43.0,
                "LOT_V2_10_IMP_02": 39.0,
            },
        )
        self.assertTrue(all(primary[lot_id]["pass_fail"] == "false" for lot_id in affected_lots))
        self.assertTrue(all(primary[lot_id]["pass_fail"] == "true" for lot_id in control_lots))

        retests = [row for row in wat_rows if row["test_item"] == "V2_INDEPENDENT_RETEST"]
        self.assertEqual({row["lot_id"] for row in retests}, affected_lots)
        self.assertTrue(all(row["test_equipment_id"] == "WAT_CELL_09" for row in retests))
        self.assertTrue(all(row["pass_fail"] == "false" for row in retests))
        for parameter_name in {
            "metal_line_resistance",
            "reticle_field_periodicity_index",
        }:
            controls = [row for row in wat_rows if row["parameter_name"] == parameter_name]
            self.assertEqual({row["lot_id"] for row in controls}, comparison_lots)
            self.assertTrue(all(row["pass_fail"] == "true" for row in controls))

        self.assertFalse(
            any(
                row["lot_id"] in comparison_lots
                for row in repository.rows("metrology_result")
            )
        )
        self.assertTrue(
            all(
                row["pass_fail"] in {"true", "false"}
                for table_name in ("wat_result", "metrology_result")
                for row in repository.rows(table_name)
            )
        )

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_010_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_010"], 3)
        self.assertEqual(judgments["RCA_V2_011"], 2)
        self.assertEqual(judgments["RCA_V2_014"], 1)

    def test_wat_spatial_leakage_traces_back_to_lithography_reticle_haze(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_011"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_11_CTRL_01", "LOT_V2_11_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertEqual(scenario["expected_causal_module"], "Lithography")
        self.assertEqual(scenario["secondary_relevant_asset_ids"], ["RCA_V2_014"])
        self.assertIn(
            "EV_V2_11_INDEPENDENT_WAT_RETEST", scenario["required_evidence_ids"]
        )
        self.assertIn(
            "EV_V2_11_ELECTRICAL_CONTROLS_NORMAL", scenario["required_evidence_ids"]
        )

        lithography_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "1000"
        ]
        self.assertEqual({row["lot_id"] for row in lithography_history}, comparison_lots)
        self.assertTrue(
            all(row["equipment_id"] == "LITHO_SCN_06" for row in lithography_history)
        )

        reticle_fdc = {
            row["lot_id"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] in comparison_lots
            and row["parameter_name"] == "reticle_scatter_index"
        }
        self.assertTrue(
            all(reticle_fdc[lot_id]["ooc_flag"] == "true" for lot_id in affected_lots)
        )
        self.assertTrue(
            all(reticle_fdc[lot_id]["ooc_flag"] == "false" for lot_id in control_lots)
        )

        wat_rows = [
            row for row in repository.rows("wat_result") if row["lot_id"] in comparison_lots
        ]
        primary = {
            row["lot_id"]: row
            for row in wat_rows
            if row["test_item"] == "V2_PRIMARY_ELECTRICAL"
        }
        self.assertEqual(
            {lot_id: float(primary[lot_id]["measured_value"]) for lot_id in affected_lots},
            {
                "LOT_V2_11_SRC": 6.4,
                "LOT_V2_11_IMP_01": 5.8,
                "LOT_V2_11_IMP_02": 5.1,
            },
        )
        self.assertTrue(all(primary[lot_id]["spec_high"] == "1.0" for lot_id in comparison_lots))
        self.assertTrue(all(primary[lot_id]["pass_fail"] == "false" for lot_id in affected_lots))
        self.assertTrue(all(primary[lot_id]["pass_fail"] == "true" for lot_id in control_lots))

        retests = [row for row in wat_rows if row["test_item"] == "V2_INDEPENDENT_RETEST"]
        self.assertEqual({row["lot_id"] for row in retests}, affected_lots)
        self.assertTrue(all(row["test_equipment_id"] == "WAT_CELL_10" for row in retests))
        self.assertTrue(all(row["pass_fail"] == "false" for row in retests))
        for parameter_name in {
            "Vt p99 shift",
            "probe_contact_resistance",
            "test_sequence_correlation_index",
        }:
            controls = [row for row in wat_rows if row["parameter_name"] == parameter_name]
            self.assertEqual({row["lot_id"] for row in controls}, comparison_lots)
            self.assertTrue(all(row["pass_fail"] == "true" for row in controls))

        self.assertFalse(
            any(
                row["lot_id"] in comparison_lots
                for row in repository.rows("metrology_result")
            )
        )
        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_011_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_011"], 3)
        self.assertEqual(judgments["RCA_V2_014"], 2)
        self.assertEqual(judgments["RCA_V2_010"], 1)

    def test_odd_slot_cvd_pattern_rolls_wafer_evidence_up_to_impact_lots(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_012"
        )
        repository = CsvFabRepository(SEED_DIR)
        affected_lots = {
            scenario["source_lot_id"],
            *scenario["expected_impact_lots"],
        }
        control_lots = {"LOT_V2_12_CTRL_01", "LOT_V2_12_CTRL_02"}
        comparison_lots = affected_lots | control_lots

        self.assertEqual(scenario["expected_causal_module"], "CVD")
        self.assertEqual(
            scenario["secondary_relevant_asset_ids"],
            ["RCA_V2_007", "RCA_V2_004"],
        )
        self.assertIn(
            "EV_V2_12_INDEPENDENT_METROLOGY", scenario["required_evidence_ids"]
        )

        lots = {
            row["lot_id"]: row
            for row in repository.rows("lot_master")
            if row["lot_id"] in comparison_lots
        }
        self.assertTrue(all(row["wafer_qty"] == "6" for row in lots.values()))
        wafers = [
            row for row in repository.rows("wafer_master") if row["lot_id"] in comparison_lots
        ]
        self.assertEqual(len(wafers), 36)
        self.assertTrue(
            all(
                len([row for row in wafers if row["lot_id"] == lot_id]) == 6
                for lot_id in comparison_lots
            )
        )

        cvd_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "5000"
        ]
        self.assertEqual(len(cvd_history), 36)
        self.assertTrue(all(row["equipment_id"] == "CVD_ILD_17" for row in cvd_history))

        metrology = [
            row
            for row in repository.rows("metrology_result")
            if row["lot_id"] in comparison_lots
            and row["metric_name"] == "film thickness delta"
        ]
        for lot_id in affected_lots:
            primary = {
                int(row["wafer_id"].rsplit("W", 1)[-1]): float(row["measured_value"])
                for row in metrology
                if row["lot_id"] == lot_id and row["measurement_stage"] == "POST_PROCESS"
            }
            independent = {
                int(row["wafer_id"].rsplit("W", 1)[-1]): float(row["measured_value"])
                for row in metrology
                if row["lot_id"] == lot_id
                and row["measurement_stage"] == "INDEPENDENT_CONFIRMATION"
            }
            self.assertEqual(primary, {1: -28.0, 2: 0.0, 3: -26.0, 4: 0.0, 5: -24.0, 6: 0.0})
            self.assertEqual(independent, primary)
        self.assertTrue(
            all(
                float(row["measured_value"]) == 0.0 and row["pass_fail"] == "true"
                for row in metrology
                if row["lot_id"] in control_lots
            )
        )

        wat_rows = [
            row for row in repository.rows("wat_result") if row["lot_id"] in comparison_lots
        ]
        for lot_id in affected_lots:
            fail_slots = {
                int(row["wafer_id"].rsplit("W", 1)[-1])
                for row in wat_rows
                if row["lot_id"] == lot_id and row["pass_fail"] == "false"
            }
            self.assertEqual(fail_slots, {1, 3, 5})
        self.assertTrue(
            all(row["pass_fail"] == "true" for row in wat_rows if row["lot_id"] in control_lots)
        )

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_012_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_012"], 3)
        self.assertEqual(judgments["RCA_V2_007"], 2)
        self.assertEqual(judgments["RCA_V2_004"], 2)

    def test_cmp_pad_checks_precede_unsupported_chemical_genealogy_stop(self) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_013"
        )
        repository = CsvFabRepository(SEED_DIR)
        source_lot = scenario["source_lot_id"]
        control_lots = {"LOT_V2_13_CTRL_01", "LOT_V2_13_CTRL_02"}
        comparison_lots = {source_lot, *control_lots}

        self.assertEqual(scenario["expected_status"], "inconclusive")
        self.assertEqual(scenario["expected_causal_module"], "Unresolved")
        self.assertEqual(scenario["expected_impact_lots"], [])
        self.assertEqual(
            scenario["unavailable_data_sources"],
            ["chemical batch genealogy"],
        )
        self.assertIn("WARN_UNSUPPORTED_DATA_SOURCE", scenario["required_warning_ids"])
        self.assertEqual(
            scenario["secondary_relevant_asset_ids"],
            ["RCA_V2_001", "RCA_V2_005", "RCA_V2_006"],
        )

        cmp_history = [
            row
            for row in repository.rows("process_history")
            if row["lot_id"] in comparison_lots and row["operation_no"] == "6400"
        ]
        self.assertEqual({row["lot_id"] for row in cmp_history}, comparison_lots)
        self.assertTrue(all(row["equipment_id"] == "CMP_CU_23" for row in cmp_history))

        fdc_rows = [
            row for row in repository.rows("fdc_feature") if row["lot_id"] in comparison_lots
        ]
        source_fdc = {
            row["parameter_name"]: row for row in fdc_rows if row["lot_id"] == source_lot
        }
        self.assertEqual(source_fdc["endpoint_extension"]["ooc_flag"], "true")
        self.assertEqual(source_fdc["removal_rate_drop_index"]["ooc_flag"], "true")
        for parameter_name in {
            "conditioner_motor_torque",
            "pad_life_used_percent",
            "slurry_flow",
            "slurry_delivery_pressure",
            "carrier_pressure",
            "platen_speed",
        }:
            self.assertEqual(source_fdc[parameter_name]["ooc_flag"], "false")
        self.assertEqual(source_fdc["pad_life_used_percent"]["observed_value"], "62.0")
        for lot_id in control_lots:
            control_fdc = {
                row["parameter_name"]: row for row in fdc_rows if row["lot_id"] == lot_id
            }
            self.assertEqual(control_fdc["endpoint_extension"]["ooc_flag"], "false")
            self.assertEqual(control_fdc["removal_rate_drop_index"]["ooc_flag"], "false")

        metrology = [
            row
            for row in repository.rows("metrology_result")
            if row["lot_id"] in comparison_lots
        ]
        self.assertFalse(any(row["metric_name"] == "endpoint extension" for row in metrology))
        source_confirmation = next(
            row
            for row in metrology
            if row["lot_id"] == source_lot
            and row["measurement_stage"] == "POST_CMP_CONFIRMATION"
        )
        self.assertEqual(source_confirmation["measured_value"], "12.0")
        self.assertEqual(source_confirmation["pass_fail"], "false")
        source_pre_cmp = next(
            row
            for row in metrology
            if row["lot_id"] == source_lot and row["measurement_stage"] == "PRE_CMP"
        )
        self.assertEqual(source_pre_cmp["pass_fail"], "true")

        observed_ooc = [
            row
            for row in repository.rows("ooc_event")
            if row["feature_id"].startswith("OOC_IF_V2_013_DETECTED")
        ]
        self.assertEqual(len(observed_ooc), 2)
        self.assertTrue(
            all("not causal attribution" in row["description"] for row in observed_ooc)
        )

        retrieval = json.loads(
            (ROOT / "data/evaluation/retrieval_ground_truth_v2.json").read_text(
                encoding="utf-8"
            )
        )
        judgments = {
            item["asset_id"]: item["relevance"]
            for item in retrieval["qrels"]["Q_V2_IF_V2_013_RCA"]
        }
        self.assertEqual(judgments["RCA_V2_013"], 3)
        self.assertEqual(judgments["RCA_V2_001"], 2)
        self.assertEqual(judgments["RCA_V2_005"], 2)
        self.assertEqual(judgments["RCA_V2_006"], 2)
        self.assertEqual(judgments["RCA_V2_004"], 1)

    def test_wat_probe_card_contamination_requires_cross_tool_and_recovery_evidence(
        self,
    ) -> None:
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"]
        scenario = next(
            item for item in scenarios if item["incident_family_id"] == "IF_V2_014"
        )
        repository = CsvFabRepository(SEED_DIR)
        source_lot = scenario["source_lot_id"]
        control_lots = {"LOT_V2_14_CTRL_01", "LOT_V2_14_CTRL_02"}

        self.assertEqual(scenario["expected_status"], "supported")
        self.assertEqual(scenario["expected_causal_module"], "WAT")
        self.assertEqual(scenario["expected_impact_lots"], [])
        for evidence_id in {
            "EV_V2_14_INDEPENDENT_WAT_RETEST",
            "EV_V2_14_EQUIPMENT_INSPECTION",
            "EV_V2_14_POST_CLEAN_RECOVERY",
            "EV_V2_14_IMPACT_SCOPE_AUDIT",
        }:
            self.assertIn(evidence_id, scenario["required_evidence_ids"])

        source_fdc = {
            row["parameter_name"]: row
            for row in repository.rows("fdc_feature")
            if row["lot_id"] == source_lot
        }
        self.assertEqual(source_fdc["probe_contact_repeatability"]["ooc_flag"], "true")
        self.assertEqual(source_fdc["probe_card_contamination_index"]["ooc_flag"], "true")
        self.assertEqual(
            source_fdc["post_clean_probe_contact_repeatability"]["ooc_flag"],
            "false",
        )

        source_wat = [
            row for row in repository.rows("wat_result") if row["lot_id"] == source_lot
        ]
        primary = next(row for row in source_wat if row["test_item"] == "V2_PRIMARY_ELECTRICAL")
        cross_tool = next(
            row for row in source_wat if row["test_item"] == "V2_INDEPENDENT_RETEST"
        )
        post_clean = next(
            row
            for row in source_wat
            if row["test_item"] == "V2_POST_CLEAN_QUALIFICATION_RETEST"
        )
        self.assertEqual(
            (primary["test_equipment_id"], primary["pass_fail"]),
            ("WAT_CELL_12", "false"),
        )
        self.assertEqual(
            (cross_tool["test_equipment_id"], cross_tool["pass_fail"]),
            ("WAT_CELL_13", "true"),
        )
        self.assertEqual(
            (post_clean["test_equipment_id"], post_clean["pass_fail"]),
            ("WAT_CELL_12", "true"),
        )

        control_wat = [
            row for row in repository.rows("wat_result") if row["lot_id"] in control_lots
        ]
        self.assertEqual({row["lot_id"] for row in control_wat}, control_lots)
        self.assertTrue(all(row["test_equipment_id"] == "WAT_CELL_12" for row in control_wat))
        self.assertTrue(all(row["pass_fail"] == "true" for row in control_wat))

        disposition = next(
            row
            for row in repository.rows("hold_history")
            if row["hold_id"] == "HOLD_IF_V2_014_EQUIPMENT"
        )
        self.assertIn("optical inspection", disposition["hold_reason"])
        self.assertIn("no additional production Lots", disposition["release_comment"])

        diagnostic_ooc = [
            row
            for row in repository.rows("ooc_event")
            if row["feature_id"].startswith("OOC_IF_V2_014_DIAGNOSTIC")
        ]
        self.assertEqual(len(diagnostic_ooc), 1)
        self.assertEqual(
            diagnostic_ooc[0]["parameter_name"], "probe_card_contamination_index"
        )


if __name__ == "__main__":
    unittest.main()
