"""Counterexamples for the three confirmed launch blockers; synthetic GT only."""

import copy
import json
import subprocess
from pathlib import Path

import pilot_execution_binding as binding
import pytest
from benchmark_observer import runtime_read_guard, sha
from pilot_export import run_envelope


def authorization(m):
    return {
        "status": "authorized",
        "authorization_timestamp": "2026-10-09T16:46:02+08:00",
        "authorization_evidence": "Synthetic offline test grant; not a real paid authorization",
        "manifest_hash": sha(m),
        "manifest_freeze_commit": binding.MANIFEST_COMMIT,
        "code_baseline_commit": m["source_commit"],
        "benchmark_version": m["benchmark_version"],
        "scorer_version": m["scorer_version"],
        "config_hash": m["config_hash"],
        "authorized_run_count": len(m["runs"]),
        "runs": copy.deepcopy(m["runs"]),
        "allowed_pairs": [
            list(p) for p in sorted({(r["provider"], r["model"]) for r in m["runs"]})
        ],
    }


@pytest.fixture
def manifest():
    root = Path(__file__).resolve().parents[2]
    return binding.read_json(root / "tests/fixtures/benchmark_execution_manifest.json")


@pytest.mark.parametrize(
    "mutation",
    ["absent", "wrong_manifest", "extra_run", "extra_provider", "wrong_order", "config", "version"],
)
def test_authorization_rejects_expansion(manifest, mutation):
    auth = authorization(manifest)
    if mutation == "absent":
        auth = None
    elif mutation == "wrong_manifest":
        auth["manifest_hash"] = "0" * 64
    elif mutation == "extra_run":
        auth["authorized_run_count"] = 19
    elif mutation == "extra_provider":
        auth["allowed_pairs"].append(["other", "model"])
    elif mutation == "wrong_order":
        auth["runs"].reverse()
    elif mutation == "config":
        auth["config_hash"] = "0" * 64
    else:
        auth["scorer_version"] = "new"
    with pytest.raises(ValueError):
        binding.validate_authorization(manifest, auth)


def test_external_grant_leaves_frozen_manifest_unchanged(manifest):
    before = sha(manifest)
    assert binding.validate_authorization(manifest, authorization(manifest))
    assert manifest["paid_execution_authorized"] is False
    assert sha(manifest) == before


def command(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def commit(repo, message):
    command(repo, "add", ".")
    command(
        repo,
        "-c",
        "user.name=Offline test",
        "-c",
        "user.email=offline@example.invalid",
        "commit",
        "-qm",
        message,
    )
    return command(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    command(repo, "init", "-q")
    p = repo / "core.py"
    p.write_text("frozen\n")
    base = commit(repo, "baseline")
    m = repo / binding.MANIFEST_PATH
    m.parent.mkdir(parents=True)
    m.write_text("{}\n")
    frozen = commit(repo, "manifest only")
    return repo, base, frozen


def test_manifest_only_descendant_passes(history):
    repo, base, frozen = history
    assert binding.verify_history(repo, base, frozen, {}, ["core.py"]) == frozen


@pytest.mark.parametrize("approved", [False, True])
def test_changed_frozen_source_rejected_even_if_approved(history, approved):
    repo, base, frozen = history
    (repo / "core.py").write_text("changed\n")
    head = commit(repo, "changed source")
    allow = {head: {"core.py": binding.file_hash(repo / "core.py")}} if approved else {}
    with pytest.raises(ValueError, match="Frozen code"):
        binding.verify_history(repo, base, frozen, allow, ["core.py"])


def test_change_then_revert_is_not_allowed(history):
    repo, base, frozen = history
    (repo / "core.py").write_text("changed\n")
    commit(repo, "bad")
    (repo / "core.py").write_text("frozen\n")
    commit(repo, "reverted")
    with pytest.raises(ValueError, match="Frozen code"):
        binding.verify_history(repo, base, frozen, {}, ["core.py"])


def test_unapproved_descendant_fails(history):
    repo, base, frozen = history
    (repo / "notes.txt").write_text("metadata")
    commit(repo, "unapproved")
    with pytest.raises(ValueError, match="Unapproved"):
        binding.verify_history(repo, base, frozen, {}, ["core.py"])


def test_approved_infrastructure_commit_requires_exact_hash(history):
    repo, base, frozen = history
    p = repo / "tools/pilot_launcher.py"
    p.parent.mkdir()
    p.write_text("# infrastructure\n")
    head = commit(repo, "approved infrastructure")
    allow = {head: {"tools/pilot_launcher.py": binding.file_hash(p)}}
    assert binding.verify_history(repo, base, frozen, allow, ["core.py"]) == head
    allow[head]["tools/pilot_launcher.py"] = "0" * 64
    with pytest.raises(ValueError, match="content mismatch"):
        binding.verify_history(repo, base, frozen, allow, ["core.py"])


def test_approved_non_infrastructure_path_still_fails(history):
    repo, base, frozen = history
    p = repo / "production_new.py"
    p.write_text("# unexpected production\n")
    head = commit(repo, "out of scope")
    with pytest.raises(ValueError, match="exceeds infrastructure scope"):
        binding.verify_history(
            repo, base, frozen, {head: {p.name: binding.file_hash(p)}}, ["core.py"]
        )


def context_for(m, auth, dry=True):
    return binding.ExecutionContext(
        json.dumps(
            {
                "manifest_hash": sha(m),
                "authorization_hash": sha(auth),
                "code_baseline_commit": m["source_commit"],
                "manifest_freeze_commit": binding.MANIFEST_COMMIT,
                "execution_head": binding.MANIFEST_COMMIT,
                "dry_run": dry,
                "draft_infrastructure": dry,
                "working_tree_clean": True,
                **{k: m[k] for k in ("config_hash", "prompt_hash", "workflow_hash", "rubric_hash")},
            }
        )
    )


def test_sanitized_context_enters_canonical_envelope(manifest):
    auth = authorization(manifest)
    context = context_for(manifest, auth)
    data = context.to_dict()
    data["execution_head"] = "forged"
    row = run_envelope(context, manifest, auth, manifest["runs"][0], "snapshot", dry_run=True)
    assert row.source_commit == binding.MANIFEST_COMMIT
    assert row.actual_model is None
    assert row.run_id.startswith("dryrun_")
    assert row.execution_kind == "offline_fixture"
    assert (
        row.metrics["execution_identity"].value["code_baseline_commit"] == manifest["source_commit"]
    )
    view = binding.export_manifest(context, manifest, auth)
    assert view["source_commit"] == binding.MANIFEST_COMMIT
    assert view["paid_execution_authorized"] is True
    assert manifest["paid_execution_authorized"] is False
    assert view["config_hash"] == manifest["config_hash"]


def test_guard_still_denies_gt_and_subprocess(tmp_path):
    with runtime_read_guard():
        with pytest.raises(PermissionError):
            (tmp_path / "ground_truth.json").read_text()
        with pytest.raises(PermissionError):
            subprocess.run(["git", "--version"], check=True)


def test_no_gt_imports_in_runtime_launch_module():
    import ast

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "tools/pilot_launcher.py").read_text(encoding="utf-8-sig"))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert "benchmark_scoring" not in imports


@pytest.mark.parametrize("name", ["core.py", "scorer.py", "config.json"])
def test_hash_verification_refuses_any_protected_file_drift(tmp_path, name):
    from benchmark_execution import verify_freeze

    repo = tmp_path / "repo"
    public = tmp_path / "public"
    repo.mkdir()
    public.mkdir()
    (repo / name).write_text("frozen")
    m = {"source_hashes": {name: binding.file_hash(repo / name)}, "public_hashes": {}}
    assert verify_freeze(m, repo, public)["verified"]
    (repo / name).write_text("changed")
    with pytest.raises(ValueError, match="drift"):
        verify_freeze(m, repo, public)


def test_real_frozen_exporter_accepts_execution_head_and_external_grant(tmp_path):
    from benchmark_record import export_run
    from pilot_export import export_bound_run
    from test_benchmark_freeze import case
    from test_model_benchmark_phase0 import fixture

    state, _ = fixture(tmp_path)
    reference = {
        "scorer_version": "formal-r2-scorer/1.1",
        "cases": [
            {
                **case("inconclusive"),
                "case_id": "CASE_A",
                "case_revision": "r2",
                "exposure_class": "normal_development",
                "ground_truth_class": "partial_source_with_frozen_rubric",
            }
        ],
    }
    ref = tmp_path / "reference.json"
    ref.write_text(json.dumps(reference))
    root = Path(__file__).resolve().parents[2]
    pricing = binding.read_json(root / "docs/model_benchmark/phase05/pricing_snapshot.json")
    m = {
        "benchmark_version": "development-rca/1.1",
        "scorer_version": "formal-r2-scorer/1.1",
        "source_commit": "a" * 40,
        "prompt_hash": "b" * 64,
        "workflow_hash": "c" * 64,
        "config_hash": sha({}),
        "config": {},
        "rubric_hash": sha(reference),
        "pricing_hash": sha(pricing),
        "requires_commit_binding_before_paid_run": False,
        "paid_execution_authorized": False,
        "runs": [
            {
                "case_id": "CASE_A",
                "run_id": "synthetic",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "order_index": 1,
            }
        ],
    }
    auth = authorization(m)
    context = context_for(m, auth, dry=False)
    raw = binding.read_json(tmp_path / "run_manifest.json")
    raw["code_commit"] = binding.MANIFEST_COMMIT
    (tmp_path / "run_manifest.json").write_text(json.dumps(raw))
    view = binding.export_manifest(context, m, auth)
    sidecar = {
        "source_verification": {"verified": True, "manifest_hash": sha(view)},
        "capture": {
            "complete": True,
            "operations": [],
            "diagnostics": [],
            "snapshots": [
                {
                    "stage": "finalizer_output",
                    "authoritative_rca_result": {
                        "root_cause": None,
                        "confirmation_status": "inconclusive",
                    },
                }
            ],
        },
        "challenge": {"records": []},
        "workflow_latency_ms": 30,
        "model_telemetry": state["execution_metadata"]["model_telemetry"],
    }
    side = tmp_path / "sidecar.json"
    side.write_text(json.dumps(sidecar))
    row = export_bound_run(
        tmp_path, side, context, m, auth, run_id="synthetic", reference_file=ref, pricing=pricing
    )
    unchanged = export_run(
        tmp_path,
        side,
        view,
        execution_kind="paid_benchmark",
        run_id="synthetic",
        reference_file=ref,
        pricing=pricing,
    )
    assert row.quality == unchanged.quality
    assert row.source_commit == binding.MANIFEST_COMMIT
    assert row.metrics["execution_identity"].value["code_baseline_commit"] == "a" * 40
    assert row.quality.value["correct_inconclusive"] is True
    assert m["paid_execution_authorized"] is False
    sidecar["source_verification"]["manifest_hash"] = "0" * 64
    side.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="execution-time"):
        export_bound_run(
            tmp_path,
            side,
            context,
            m,
            auth,
            run_id="synthetic",
            reference_file=ref,
            pricing=pricing,
        )


@pytest.mark.parametrize("state", ["exhausted", "out_of_order", "incomplete"])
def test_paid_ledger_cannot_expand_reorder_or_repeat(manifest, tmp_path, state):
    from pilot_launcher import dispatch

    repo = Path(__file__).resolve().parents[2]
    public = Path(__import__("os").environ.get("BENCHMARK_PUBLIC_DIR", str(repo / "unused")))
    if not public.exists():
        pytest.skip("explicit public snapshot required")
    auth = authorization(manifest)
    data = context_for(manifest, auth, dry=False).to_dict()
    data["launch_file_hashes"] = {p: binding.file_hash(repo / p) for p in binding.LAUNCH_FILES}
    ctx = binding.ExecutionContext(json.dumps(data))
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    if state == "exhausted":
        for i, r in enumerate(manifest["runs"], 1):
            (ledger / f"{i:02d}.json").write_text(json.dumps(r))
    else:
        run = manifest["runs"][0 if state == "incomplete" else 1]
        (ledger / "01.json").write_text(json.dumps(run))
    with pytest.raises(
        ValueError,
        match={
            "exhausted": "exhausted",
            "out_of_order": "order ledger",
            "incomplete": "incomplete",
        }[state],
    ):
        dispatch(
            ctx, manifest, auth, repo=repo, public=public, output=tmp_path / "output", ledger=ledger
        )
    assert not (tmp_path / "output").exists()
