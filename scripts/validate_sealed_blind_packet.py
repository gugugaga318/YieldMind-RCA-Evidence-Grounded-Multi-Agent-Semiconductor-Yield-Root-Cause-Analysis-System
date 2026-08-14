"""Validate a sealed formal-blind public packet without reading Ground Truth.

The validator is intentionally limited to the ``public/`` directory.  It
checks the externally supplied governance declaration and the frozen public
file hashes, but it has no Ground Truth argument and never walks the packet's
parent directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

MANIFEST_NAME = "sealed_blind_manifest.json"
FORMAL_V2_ROLE = "sealed_blind"
REQUIRED_GROUND_TRUTH_CUSTODIAN = "external_agent"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def public_files(public_dir: Path) -> list[dict[str, Any]]:
    """Return the canonical public snapshot, excluding its own manifest."""

    return [
        {
            "path": str(path.relative_to(public_dir)).replace("\\", "/"),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(public_dir.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    ]


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def validate_sealed_public_packet(public_dir: Path) -> dict[str, Any]:
    """Validate and return the sealed declaration for one public packet."""

    resolved = public_dir.resolve()
    if resolved.name != "public":
        raise ValueError("the sealed packet path must end in public/")
    if not (resolved / "fab_data").is_dir() or not (resolved / "cases.json").is_file():
        raise ValueError("the sealed public packet requires cases.json and fab_data/")
    if any(part.casefold() == "hidden" for path in resolved.rglob("*") for part in path.parts):
        raise ValueError("the public packet must not contain a hidden/ path")

    manifest_path = resolved / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"sealed evaluation requires {MANIFEST_NAME}")
    manifest = _load_object(manifest_path)
    required = {
        "schema_version",
        "dataset_id",
        "evaluation_role",
        "dataset_generation_independent",
        "ground_truth_custodian",
        "ground_truth_sha256_commitment",
        "development_agent_ground_truth_access",
        "sealed_before_execution",
        "public_files",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"sealed manifest is missing fields: {missing}")
    if manifest["schema_version"] != "1.0":
        raise ValueError("sealed manifest schema_version must be 1.0")
    if manifest["evaluation_role"] != FORMAL_V2_ROLE:
        raise ValueError("sealed manifest evaluation_role must be sealed_blind")
    if manifest["dataset_generation_independent"] is not True:
        raise ValueError("sealed dataset must declare independent generation")
    if manifest["ground_truth_custodian"] != REQUIRED_GROUND_TRUTH_CUSTODIAN:
        raise ValueError("Ground Truth must be held by an external_agent")
    if not isinstance(manifest["ground_truth_sha256_commitment"], str) or not re.fullmatch(
        r"[A-F0-9]{64}", manifest["ground_truth_sha256_commitment"]
    ):
        raise ValueError("Ground Truth SHA-256 commitment must contain 64 hex characters")
    if manifest["development_agent_ground_truth_access"] is not False:
        raise ValueError("the development agent must not have Ground Truth access")
    if manifest["sealed_before_execution"] is not True:
        raise ValueError("the public packet must be sealed before execution")

    cases = _load_object(resolved / "cases.json")
    if manifest["dataset_id"] != cases.get("dataset_id"):
        raise ValueError("sealed manifest and cases.json dataset_id values differ")
    expected_files = public_files(resolved)
    if manifest["public_files"] != expected_files:
        raise ValueError("sealed public file snapshot or SHA-256 values do not match")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = validate_sealed_public_packet(args.public_dir)
    print(
        "Sealed blind packet: VALID; "
        f"dataset={manifest['dataset_id']}; "
        f"public_files={len(manifest['public_files'])}; ground_truth_loaded=False"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
