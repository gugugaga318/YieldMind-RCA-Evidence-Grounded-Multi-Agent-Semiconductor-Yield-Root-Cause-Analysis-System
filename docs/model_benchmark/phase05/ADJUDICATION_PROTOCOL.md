# Unknown-expression adjudication protocol — adjudication/1.0

Frozen at the 2026-10-09 Pilot final review, before any Pilot output exists. This protocol replaces the informal workflow paragraph in SCORING.md when preparing review packets. It does not approve or change formal-r2-scorer/1.0 GT. The final execution manifest must pin this protocol's hash before use.

## Roles and identity isolation

A coordinator prepares packets and retains a private mapping. Reviewers receive no provider/model/run identity and cannot access raw run folders, the mapping, paired-model output, execution order, telemetry, prices or the model comparison scorecard. Two independent reviewers assess each packet. A third reviewer resolves disagreements after both first decisions are sealed; unresolved disagreement stays unresolved.

Use fresh opaque packet IDs and candidate/Evidence aliases. Native run/job/request/call/candidate IDs, file paths, artifact names, timestamps, record hashes that permit joining to public records, provider labels and model names stay in the coordinator-only mapping. Do not derive packet IDs from the run_id (which contains the provider). Randomize packet presentation independently of the execution order; keep the permutation private. Reviewers must not be told which first/second-model slot a packet represents.

Packet allowlist: protocol version; opaque packet ID; rubric/scorer version and reference hash; frozen GT dimensions including source proof-granularity restrictions; candidate proposition/profile; applicable Evidence facts with packet-local reference IDs; dimensions requiring review. Include enough factual context to distinguish a mechanism from unsupported component detail, while hiding provider identity. Mechanism prose/style alone cannot guarantee that a reviewer never guesses a model; no identity metadata or confirmation of guesses is supplied.

Before release, the coordinator inspects all nested fields, free text, Evidence provenance, IDs, filenames and links for identity leakage. Provider self-identification or model names inside output are replaced by an identity redaction marker only. Preserve the original and transformation log privately; do not rewrite causal claims. If redaction would destroy the evidence needed to judge a claim, mark the packet unresolved and escalate rather than guessing. A failed anonymization check prevents packet release.

## Trigger and decision rules

1. An unmapped expression or missing/ambiguous required dimension is `unresolved / needs_review`. It is neither automatically correct nor incorrect. A known explicit mismatch remains an explicit mismatch; reviewers cannot hide it behind a similar mechanism name.
2. Evaluate mechanism, operation, equipment, chamber, recipe ID/version, scope and time independently. A narrower/broader scope does not alone change underlying mechanism identity. Required Lane/entity constraints remain separate. Missing data is unknown, never a fabricated canonical identity.
3. Enforce source GT proof granularity: Lane/parameter/equipment-or-recipe scope evidence does not prove an uninstrumented component failure or sub-step. The existing rubric's omission of this source restriction is a final-review blocker and must be resolved/versioned before use.
4. Use only the frozen rubric and permitted evidence. No internet search for the emitting model, other model output, model-as-judge, or free-form GT repair during adjudication.
5. Each reviewer records every dimension as `match`, `mismatch`, `unknown`, or `not_applicable`, with rationale and packet-local Evidence references. `not_applicable` is allowed only when the frozen GT explicitly has no constraint; missing observation is `unknown`.
6. Require independent reviewer identities, timestamps, immutable packet/reference hashes and signed/attributable decisions. Both initial reviews are sealed before disagreement resolution. A third reviewer does not silently replace them.
7. The coordinator links decisions back through the private mapping only after the decision is sealed. Derived score layers must match source-record, rubric, scorer and packet hashes. Preserve the automatic result, both initial decisions, resolution and final adjudicated result as separate immutable artifacts.
8. Reviewers cannot edit Ground Truth, aliases, weights, acceptance criteria or source output. A suspected GT error creates `gt_issue`, pauses the Pilot/evaluation, and requires a new scorer/benchmark version and declared re-evaluation layer across both models. Never silently repair the current scores or continue the same mixed-version iteration.
9. If no defensible decision is reached, retain unresolved/needs_review. Reports show unknown count and eligible denominator; no dropping unknown runs or turning them into zero to manufacture a winner.

## Trace record contract

Coordinator-only mapping: opaque packet ID; source run/record hash; original snapshot operation/attempt/candidate; Evidence alias map; original/refacted payload hashes; redaction log; randomization reference; protocol/scorer/rubric versions and hashes; creation time.

Reviewer decision: opaque packet ID; reviewed packet hash; protocol/scorer/rubric version/hash; reviewer ID; UTC submission time; per-dimension decision, rationale and Evidence references; overall `resolved`, `needs_review` or `gt_issue`; declaration that no provider/model identity was disclosed.

Resolution: both initial decision hashes; third reviewer ID if needed; disagreement dimensions; reasoned resolution; remaining unknowns; UTC time. Later corrections create a superseding decision referencing the old hash; nothing is overwritten.

Artifacts are exclusive-create. The coordinator verifies no collisions, unchanged reference hash, correct source binding, two distinct reviewers, completed identity-leak check and preserved prior decisions before accepting an adjudicated score. Packet-release and score-merge tooling have not yet been validated by the current Phase 0.5 implementation; this document freezes the procedure, not a claim of an operationally tested anonymous reviewer pipeline.

## Runtime separation

These documents, reference answers, packets, mapping and decisions are evaluator-only. Keep them under a scoring/private root rejected by runtime_read_guard. No objects from these files may be preloaded into an execution closure. Use a fresh dedicated execution process. No scorer/reviewer result enters Prompt, State, Tools or ModelRuntime context.
