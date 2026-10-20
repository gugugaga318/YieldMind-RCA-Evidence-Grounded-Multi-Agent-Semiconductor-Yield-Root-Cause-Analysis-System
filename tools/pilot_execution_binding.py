"""Trusted Benchmark launch identity/authorization. No scoring or provider calls.

All Git and evaluator-file hashing happens before runtime isolation. Infrastructure
approval is an explicit hash/commit allowlist, never an arbitrary descendant rule.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from benchmark_execution import verify_freeze
from benchmark_observer import sha

MANIFEST_PATH = "docs/model_benchmark/scorer_closure/execution_manifest.json"
MANIFEST_COMMIT = "21e95f32c85494f0686470798ee01faacb263dfc"
BASELINE = "70635e41bbfc5046a7de453a0238311fdf7ef4f1"
MANIFEST_HASH = "6ebdd9bda47f1dcd0004d3d347841d81387940857900f3d73de6b1693ab9c9d4"
CONFIG_HASH = "641173e27076008d9e1cde1fea47ef92fa0c57142066db1aee96aeb583a29af0"
LAUNCH_FILES = (
    "tools/pilot_execution_binding.py",
    "tools/pilot_launcher.py",
    "tools/pilot_export.py",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def validate_authorization(manifest, authorization):
    if not isinstance(authorization, dict) or authorization.get("status") != "authorized":
        raise ValueError("External paid authorization required")
    expected = {
        "manifest_hash": sha(manifest),
        "manifest_freeze_commit": MANIFEST_COMMIT,
        "code_baseline_commit": manifest["source_commit"],
        "benchmark_version": manifest["benchmark_version"],
        "scorer_version": manifest["scorer_version"],
        "config_hash": manifest["config_hash"],
        "authorized_run_count": len(manifest["runs"]),
        "runs": manifest["runs"],
        "allowed_pairs": sorted({(r["provider"], r["model"]) for r in manifest["runs"]}),
    }
    for key, value in expected.items():
        actual = authorization.get(key)
        if key == "allowed_pairs" and isinstance(actual, list):
            actual = sorted(tuple(p) for p in actual)
        if actual != value:
            raise ValueError("Authorization binding mismatch: " + key)
    stamp = datetime.fromisoformat(authorization["authorization_timestamp"])
    if stamp.tzinfo is None or not authorization.get("authorization_evidence"):
        raise ValueError("Authorization must have timestamp and human evidence")
    if set(authorization) != set(expected) | {
        "status",
        "authorization_timestamp",
        "authorization_evidence",
    }:
        raise ValueError("Unexpected authorization field")
    return sha(authorization)


def verify_history(repo, baseline, freeze_commit, approved_commits, frozen_paths):
    head = git(repo, "rev-parse", "HEAD")
    for ancestor in (baseline, freeze_commit):
        subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, head], cwd=repo, check=True)
    for commit in git(repo, "rev-list", "--reverse", baseline + ".." + head).splitlines():
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(parents) != 2:
            raise ValueError("Unreviewed merge in execution history")
        changed = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if set(changed) & set(frozen_paths):
            raise ValueError("Frozen code changed in execution history")
        expected = approved_commits.get(commit)
        if commit == freeze_commit:
            expected = {MANIFEST_PATH: file_hash(repo / MANIFEST_PATH)}
        if expected is None or set(changed) != set(expected):
            raise ValueError("Unapproved execution commit: " + commit)
        for name, digest in expected.items():
            if commit != freeze_commit and not (
                name in LAUNCH_FILES
                or name
                in {
                    "tests/unit/test_pilot_execution_binding.py",
                    "tests/integration/test_pilot_launch_dryrun.py",
                }
                or name.startswith("docs/model_benchmark/execution_infrastructure/")
            ):
                raise ValueError("Approved commit exceeds infrastructure scope")
            data = subprocess.check_output(["git", "show", commit + ":" + name], cwd=repo)
            if hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() != digest:
                raise ValueError("Approved commit content mismatch")
    return head


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable serialized metadata only; never keys, GT, or mutable caller objects."""

    payload: str

    def to_dict(self):
        return json.loads(self.payload)


def validate_context(context, manifest, authorization):
    data = context.to_dict()
    if data["manifest_hash"] != sha(manifest):
        raise ValueError("Context manifest mismatch")
    if data["authorization_hash"] != validate_authorization(manifest, authorization):
        raise ValueError("Context authorization mismatch")
    for key in ("config_hash", "prompt_hash", "workflow_hash", "rubric_hash"):
        if data[key] != manifest[key]:
            raise ValueError("Context hash mismatch: " + key)
    if (
        data["code_baseline_commit"] != manifest["source_commit"]
        or data["manifest_freeze_commit"] != MANIFEST_COMMIT
    ):
        raise ValueError("Context baseline mismatch")
    return data


def preflight(repo, public, manifest, authorization, approval, reference_file, *, dry_run):
    repo, public = Path(repo).resolve(), Path(public).resolve()
    if sha(manifest) != MANIFEST_HASH or manifest["source_commit"] != BASELINE:
        raise ValueError("Frozen manifest differs")
    if manifest["config_hash"] != CONFIG_HASH or sha(manifest["config"]) != CONFIG_HASH:
        raise ValueError("Frozen execution config differs")
    if read_json(repo / MANIFEST_PATH) != manifest:
        raise ValueError("Working manifest differs")
    committed = json.loads(git(repo, "show", MANIFEST_COMMIT + ":" + MANIFEST_PATH))
    if committed != manifest:
        raise ValueError("Committed manifest differs")
    auth_hash = validate_authorization(manifest, authorization)
    if approval.get("manifest_hash") != sha(manifest):
        raise ValueError("Infrastructure approval manifest mismatch")
    hashes = approval["launch_file_hashes"]
    if set(hashes) != set(LAUNCH_FILES):
        raise ValueError("Unexpected launch infrastructure files")
    for name, expected in hashes.items():
        if file_hash(repo / name) != expected:
            raise ValueError("Launch infrastructure drift: " + name)
    head = verify_history(
        repo, BASELINE, MANIFEST_COMMIT, approval["approved_commits"], manifest["source_hashes"]
    )
    if git(repo, "diff", "HEAD", "--name-only"):
        raise ValueError("Tracked working tree is not clean")
    untracked = git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "tools",
        "scripts",
        "core",
        "backend",
    ).splitlines()
    untracked_code = {p for p in untracked if p.endswith(".py")}
    if untracked_code and (not dry_run or untracked_code != set(LAUNCH_FILES)):
        raise ValueError("Uncommitted execution code")
    if not dry_run and approval.get("status") != "approved_for_execution":
        raise ValueError("Infrastructure review required before paid launch")
    verify_freeze(manifest, repo, public)
    # Hash reference in a separate short-lived evaluator process. No GT objects
    # are loaded into the process that will construct and execute the workflow.
    code = (
        "import json,hashlib,sys; v=json.load(open(sys.argv[1],encoding='utf-8')); "
        "print(hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),"
        "ensure_ascii=False).encode()).hexdigest())"
    )
    rubric_hash = subprocess.check_output(
        [sys.executable, "-c", code, str(reference_file)], text=True
    ).strip()
    if rubric_hash != manifest["rubric_hash"]:
        raise ValueError("Rubric hash differs")
    artifacts = {
        "schema_hash": "scorer_closure/record.schema.json",
        "pricing_hash": "phase05/pricing_snapshot.json",
    }
    for key, relative in artifacts.items():
        if sha(read_json(repo / "docs/model_benchmark" / relative)) != manifest[key]:
            raise ValueError("Frozen artifact differs: " + key)
    if (
        file_hash(repo / "docs/model_benchmark/phase05/ADJUDICATION_PROTOCOL.md")
        != manifest["adjudication_protocol_hash"]
    ):
        raise ValueError("Adjudication protocol differs")
    data = {
        "code_baseline_commit": BASELINE,
        "manifest_freeze_commit": MANIFEST_COMMIT,
        "execution_head": head,
        "working_tree_clean": not bool(untracked_code),
        "tracked_working_tree_clean": True,
        "manifest_hash": sha(manifest),
        "authorization_hash": auth_hash,
        "infrastructure_approval_hash": sha(approval),
        "launch_file_hashes": hashes,
        "draft_infrastructure": bool(untracked_code),
        "dry_run": dry_run,
        **{
            key: manifest[key]
            for key in ("config_hash", "prompt_hash", "workflow_hash", "rubric_hash")
        },
        "source_hashes_verified": len(manifest["source_hashes"]),
    }
    return ExecutionContext(json.dumps(data, sort_keys=True))


def export_manifest(context, manifest, authorization):
    """Derived launch view, saved separately. Original frozen manifest never mutated.

    Frozen exporter accepts an execution source and explicit authorization boolean;
    both are supplied only after validating their external binding here.
    """
    data = validate_context(context, manifest, authorization)
    view = json.loads(json.dumps(manifest))
    view.update(
        source_commit=data["execution_head"],
        paid_execution_authorized=True,
        code_baseline_commit=data["code_baseline_commit"],
        manifest_freeze_commit=data["manifest_freeze_commit"],
        frozen_manifest_hash=data["manifest_hash"],
        authorization_hash=data["authorization_hash"],
        execution_context_hash=sha(data),
    )
    return view
