from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_formal_blind_rca import (  # noqa: E402
    DEVELOPMENT_REGRESSION_ROLE,
    _validate_sealed_run_selection,
    build_parser,
)
from validate_sealed_blind_packet import (  # noqa: E402
    FORMAL_V2_ROLE,
    MANIFEST_NAME,
    public_files,
    validate_sealed_public_packet,
)


def _packet(tmp_path: Path) -> Path:
    public_dir = tmp_path / "formal_v2" / "public"
    (public_dir / "fab_data").mkdir(parents=True)
    (public_dir / "fab_data" / "lot.csv").write_text(
        "lot_id\nLOT_TEST\n", encoding="utf-8"
    )
    (public_dir / "cases.json").write_text(
        json.dumps(
            {
                "dataset_id": "formal-v2-test",
                "case_count": 1,
                "cases": [
                    {
                        "case_id": "FORMAL_V2_001",
                        "source_lot_id": "LOT_TEST",
                        "query": "Investigate the observed defect.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "dataset_id": "formal-v2-test",
        "evaluation_role": FORMAL_V2_ROLE,
        "dataset_generation_independent": True,
        "ground_truth_custodian": "external_agent",
        "ground_truth_sha256_commitment": "A" * 64,
        "development_agent_ground_truth_access": False,
        "sealed_before_execution": True,
        "public_files": public_files(public_dir),
    }
    (public_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return public_dir


def test_valid_sealed_public_packet_is_accepted() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        manifest = validate_sealed_public_packet(_packet(Path(temporary)))

    assert manifest["evaluation_role"] == FORMAL_V2_ROLE
    assert manifest["development_agent_ground_truth_access"] is False


def test_public_file_change_breaks_sealed_hash() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        public_dir = _packet(Path(temporary))
        (public_dir / "fab_data" / "lot.csv").write_text(
            "lot_id\nLOT_CHANGED\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="SHA-256"):
            validate_sealed_public_packet(public_dir)


def test_sealed_role_cannot_be_self_declared_by_runner_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.evaluation_role == DEVELOPMENT_REGRESSION_ROLE


def test_sealed_run_rejects_case_selection_and_overwrite() -> None:
    with pytest.raises(ValueError, match="complete case catalogue"):
        _validate_sealed_run_selection(case_ids=["FORMAL_V2_001"], overwrite=False)
    with pytest.raises(ValueError, match="cannot overwrite"):
        _validate_sealed_run_selection(case_ids=[], overwrite=True)
