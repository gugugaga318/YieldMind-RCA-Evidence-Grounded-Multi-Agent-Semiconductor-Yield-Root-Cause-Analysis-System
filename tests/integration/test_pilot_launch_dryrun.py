"""Real launch path, public data and real runtime, stopped before any HTTP transport.

Uses explicit public fixture directory; no provider credentials are read. Run with
BENCHMARK_PUBLIC_DIR pointing at the frozen r2 public snapshot.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from benchmark_observer import sha
from benchmark_record import BenchmarkRecord
from pilot_execution_binding import LAUNCH_FILES, MANIFEST_PATH, file_hash, read_json


@pytest.mark.skipif(
    not os.getenv("BENCHMARK_PUBLIC_DIR"), reason="explicit public snapshot required"
)
def test_complete_real_launch_zero_http(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    folder = repo / "docs/model_benchmark/execution_infrastructure"
    manifest = read_json(repo / MANIFEST_PATH)
    before = sha(manifest)
    # Review-only hash receipt for this exact test build; never enables paid calls.
    approval = read_json(folder / "infrastructure_review.json")
    approval["launch_file_hashes"] = {p: file_hash(repo / p) for p in LAUNCH_FILES}
    (tmp_path / "review.json").write_text(json.dumps(approval), encoding="utf-8")
    output = tmp_path / "dry"
    ledger = tmp_path / "paid-ledger"
    code = """
import os, socket, sys
from pathlib import Path
from pilot_launcher import launch
from pilot_execution_binding import MANIFEST_PATH
# Independent transport sentinel, below launcher interception. Any escaped network
# call is a hard test failure. No API keys are loaded or required.
def no_connect(*a, **kw): raise AssertionError("network transport reached")
socket.socket.connect=no_connect
environment_before=dict(os.environ)
repo=Path.cwd(); folder=repo/'docs/model_benchmark/execution_infrastructure'
print(launch(repo=repo,public=Path(os.environ['BENCHMARK_PUBLIC_DIR']),
 manifest_file=repo/MANIFEST_PATH,authorization_file=folder/'authorization.json',
 approval_file=Path(sys.argv[1]),reference_file=repo/'.blind_evaluation/benchmark_scoring_v1_1/rubric.json',
 output=Path(sys.argv[2]),ledger=Path(sys.argv[3]),dry_run=True))
assert "benchmark_scoring" not in sys.modules
assert dict(os.environ)==environment_before
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "review.json"), str(output), str(ledger)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "'provider_http_sent': 0" in result.stdout
    status = read_json(output / "dispatch_status.json")
    assert status == {
        "http_send_boundaries": 1,
        "provider_http_sent": 0,
        "gt_read_blocked": True,
        "stopped_before_provider_http": True,
    }
    row = BenchmarkRecord.model_validate(read_json(output / "dispatch_envelope.json"))
    assert row.execution_kind == "offline_fixture" and row.run_id.startswith("dryrun_")
    assert (
        row.source_commit
        == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    )
    raw = read_json(output / "raw/run_manifest.json")
    assert raw["code_commit"] == row.source_commit
    assert raw["selected_case_ids"] == [manifest["runs"][0]["case_id"]]
    assert row.actual_provider is None and row.actual_model is None
    assert not ledger.exists() and sha(read_json(repo / MANIFEST_PATH)) == before
    assert not (output / "raw/run_results.json").exists()
    assert all(r["scheduled_status"] == "not_run" for r in manifest["runs"])
    side = read_json(output / "observation/sidecar.json")
    assert side["failure_type"] == "DryRunBoundary"
    # Not a completed model run; the attempted boundary is not benchmark usage.
    assert "import benchmark_scoring" not in code

    repeated = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "review.json"), str(output), str(ledger)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr
    assert not ledger.exists()
