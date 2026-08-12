from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from yield_rca_core.causal_scope import (  # noqa: E402
    CausalLane,
    CausalScopeMode,
    CausalSearchScope,
    ObservationScope,
    RepositoryCausalContextProvider,
    build_causal_search_scope,
    explicit_module_limit_requested,
)
from yield_rca_core.models import ModelValidationError  # noqa: E402
from yield_rca_core.repositories import CsvFabRepository  # noqa: E402

SEED_DIR = ROOT / "data" / "seeds" / "golden_case"


class CausalScopeContractTest(unittest.TestCase):
    def test_root_cause_keeps_observed_module_soft_and_round_trips(self) -> None:
        observation = ObservationScope(
            source_lot_id="LOT_A_001",
            product_id="40N_SOC",
            detected_module="Cu CMP",
            detected_operation="6400",
            symptom_types=("scratch",),
        )

        scope = build_causal_search_scope(
            question_kind="root_cause",
            observation=observation,
        )
        restored = CausalSearchScope.from_dict(scope.to_dict())

        self.assertEqual(restored, scope)
        self.assertEqual(scope.mode, CausalScopeMode.CAUSAL_WIDE.value)
        self.assertEqual(scope.hard_constraints.module, "")
        self.assertEqual(scope.soft_hints.module, "Cu CMP")
        self.assertEqual(
            {item.lane for item in scope.expansion_lanes},
            {item.value for item in CausalLane},
        )
        self.assertEqual(
            scope.available_lanes,
            (CausalLane.SAME_STEP.value, CausalLane.GLOBAL_SEMANTIC.value),
        )
        self.assertFalse(scope.lane(CausalLane.UPSTREAM_ROUTE.value).available)  # type: ignore[union-attr]
        self.assertFalse(scope.lane(CausalLane.SHARED_RESOURCE.value).available)  # type: ignore[union-attr]

    def test_only_language_or_explicit_flag_creates_hard_module_limit(self) -> None:
        observation = ObservationScope(detected_module="Cu CMP")

        self.assertFalse(
            explicit_module_limit_requested(
                "Investigate the scratch found in Cu CMP.",
                "Cu CMP",
            )
        )
        self.assertTrue(
            explicit_module_limit_requested(
                "Only investigate Cu CMP records.",
                "Cu CMP",
            )
        )
        self.assertTrue(
            explicit_module_limit_requested("只检查 Cu CMP 范围内", "Cu CMP")
        )
        scope = build_causal_search_scope(
            question_kind="historical_match",
            observation=observation,
            explicit_module_limit=True,
        )

        self.assertEqual(scope.mode, CausalScopeMode.EXPLICIT_HARD.value)
        self.assertEqual(scope.hard_constraints.module, "Cu CMP")
        self.assertEqual(scope.explicit_user_limits, ("module=Cu CMP",))

    def test_procedure_guidance_can_remain_operation_scoped(self) -> None:
        scope = build_causal_search_scope(
            question_kind="procedure_guidance",
            observation=ObservationScope(
                detected_module="Cu CMP",
                detected_operation="6400",
            ),
        )

        self.assertEqual(scope.mode, CausalScopeMode.EXPLICIT_HARD.value)
        self.assertEqual(scope.hard_constraints.module, "Cu CMP")
        self.assertEqual(scope.hard_constraints.operation, "6400")

    def test_detection_time_is_a_strict_hard_boundary(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "include a timezone"):
            ObservationScope(detected_at="2026-07-08T20:45:00")

        scope = build_causal_search_scope(
            question_kind="historical_match",
            observation=ObservationScope(
                detected_module="Cu CMP",
                detected_at="2026-07-08T20:45:00+00:00",
            ),
        )

        self.assertEqual(scope.time_boundary, "2026-07-08T20:45:00+00:00")

    def test_repository_resolves_route_and_configured_resource_lanes(self) -> None:
        provider = RepositoryCausalContextProvider(CsvFabRepository(SEED_DIR))
        scope = build_causal_search_scope(
            question_kind="root_cause",
            observation=ObservationScope(
                source_lot_id="LOT_A_001",
                detected_module="Cu CMP",
                detected_operation="6400",
                detected_equipment_id="CMP_CU03",
                detected_equipment_type="CMP",
            ),
            context_provider=provider,
        )

        upstream = scope.lane(CausalLane.UPSTREAM_ROUTE.value)
        shared = scope.lane(CausalLane.SHARED_RESOURCE.value)
        assert upstream is not None and shared is not None
        self.assertTrue(upstream.available)
        self.assertIn("Thin Film", upstream.modules)
        self.assertTrue(shared.available)
        self.assertIn("CMP", shared.equipment_types)
        self.assertEqual(
            set(shared.shared_resource_types),
            {"equipment", "chamber", "recipe"},
        )


if __name__ == "__main__":
    unittest.main()
