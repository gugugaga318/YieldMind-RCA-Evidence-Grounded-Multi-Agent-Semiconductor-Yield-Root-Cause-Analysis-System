# Formal Blind Evaluation Governance

## Current status

Formal V1 (`FORMAL_001`–`FORMAL_024`) is a **development/regression set**. Its
cases were inspected during data review and its execution failures were used to
modify the runtime. It remains useful for reproducing failures and preventing
regressions, but it is no longer an unseen test and must not be reported as a
final blind-evaluation or résumé accuracy result.

This rule is independent of whether an individual V1 run used Qwen cleanly. A
clean execution proves the orchestration path; it does not restore blindness.

## Formal V2 sealed-blind boundary

Formal V2 must be generated and reviewed by agents that are independent of the
development agent. The responsibilities are deliberately separated:

| Role | May read `public/` | May read Ground Truth | May modify RCA code |
| --- | --- | --- | --- |
| Data generation/review agent | Yes | Yes, when assigned | No |
| Development/execution agent | Yes | No | Yes, before the packet is sealed |
| External scoring agent | Frozen run only | Yes | No |

The public packet must contain `cases.json`, `fab_data/`, and
`sealed_blind_manifest.json`. The manifest declares:

```json
{
  "schema_version": "1.0",
  "dataset_id": "formal-v2-...",
  "evaluation_role": "sealed_blind",
  "dataset_generation_independent": true,
  "ground_truth_custodian": "external_agent",
  "ground_truth_sha256_commitment": "64-character SHA-256 of ground_truth.json",
  "development_agent_ground_truth_access": false,
  "sealed_before_execution": true,
  "public_files": [
    {"path": "cases.json", "sha256": "...", "bytes": 123}
  ]
}
```

`public_files` covers every public file except the manifest itself. The external
data custodian creates this snapshot after Agent A and Agent B finish reviewing
the data. The Ground Truth commitment freezes the private answer without exposing
it. The execution agent validates the public packet with:

```powershell
.\.venv\Scripts\python.exe scripts\validate_sealed_blind_packet.py `
  --public-dir C:\path\to\formal_v2\public
```

The runner records the public SHA-256 snapshot and current Git commit. It has no
Ground Truth parameter:

```powershell
.\.venv\Scripts\python.exe scripts\run_formal_blind_rca.py `
  --public-dir C:\path\to\formal_v2\public `
  --output-dir outputs\formal_blind_v2\qwen_run `
  --evaluation-role sealed_blind `
  --orchestration-mode llm_react `
  --confirm-paid-qwen
```

## Freeze and invalidation rules

1. Record the frozen public hashes and code commit before the first V2 run.
2. Use a clean committed tracked worktree. Uncommitted code is not reproducible.
3. Run the complete catalogue before scoring. `sealed_blind` rejects `--case-id`.
4. Never overwrite a sealed run directory; use a new directory for an allowed
   operational rerun.
5. Do not change prompts, policies, gates, or code in response to a V2 case.
6. Only the external scoring agent may combine the frozen run with Ground Truth.
7. If a V2 result is used to change the system, V2 immediately becomes a
   development/regression set. Create a new independently generated V3 for the
   next final blind evaluation.
8. Provider outages may be rerun only under a predeclared operational rerun
   policy. Failed reasoning or validation is a model/system result, not an outage.

## External Ground Truth and scoring contract

The external scoring agent keeps Ground Truth outside `public/`. A sealed V2
supported case uses semantic components instead of one exact generated sentence:

```json
{
  "dataset_id": "formal-v2-...",
  "evaluation_role": "sealed_blind",
  "cases": [
    {
      "case_id": "FORMAL_V2_001",
      "expected_status": "supported",
      "expected_root_cause": {
        "equipment": ["EQ_D509B8"],
        "chamber": ["EQ_D509B8_CH01"],
        "operation": ["operation 4000", "OP4000"],
        "mechanism": ["center seam void", "non-uniform copper fill"],
        "abnormal_parameters": ["backside pressure CV"]
      },
      "expected_impact_lots": ["LOT_...", "LOT_..."]
    }
  ]
}
```

Aliases must describe the same reviewed fact, not broaden the answer after seeing
model output. A component may use an empty array only when it is genuinely not
applicable to the case. An inconclusive case still carries the same object shape
for audit consistency, but root-cause components are not scored as confirmed.

The final score has two independent layers:

- Execution layer: workflow completion, strict Qwen acceptance, actual
  `llm_react`, provider health, Qwen candidate source, Qwen stop proposal, Python
  terminal Evidence Gate, and LLM-call count. It never reads answer labels.
- RCA quality layer: status accuracy, supported precision/recall, inconclusive
  recall, five root-cause component accuracies and structured exact rate, impact
  Lot precision/recall/F1/exact, Brier score, and over/under-confirmation rates.

A run passes only when the public snapshot is unchanged and both layers pass.
Workflow completion or a clean Qwen path alone is never presented as RCA
correctness.

## What each result can claim

- `development_regression`: reproducibility, safety regression, and execution
  diagnostics only.
- `sealed_blind`: an unseen Synthetic benchmark result, provided the manifest,
  full-run freeze, and external scoring audit all pass.
- Neither result is production-Fab accuracy. Synthetic limitations must remain in
  reports and interview claims.
