# Evaluation V2 End-to-End RCA Report

- Dataset: `synthetic-semiconductor-causal-v2` (Synthetic benchmark)
- Fixed reference: **COMPLETE**
- Controlled compatibility: **PASS**
- Real Qwen llm_react: **NOT_RUN**
- RCA quality gate: **BLOCKED**
- Governance gate: **PASS**

## Fixed deterministic reference (Test partition)

- Root Cause Correctness: 100.00% (6/6)
- Evidence Completeness: 100.00% (48/48)
- Impact Lot Precision: 100.00%
- Impact Lot Recall: 100.00%
- Correct Abstention Rate: 100.00% (1/1)
- Required Warning Recall: 100.00%

## Real Qwen boundary

Real Qwen was not run: Paid real-Qwen evaluation requires both --run-real-qwen and --confirm-paid-qwen with DASHSCOPE_API_KEY.
The RCA release gate is therefore BLOCKED. Fake LLM output was not used.

## Failed scenarios

- None

## Limitations

These numbers measure a reviewed Synthetic benchmark, not confidential Fab data or production accuracy. Hidden labels are used only by the evaluator after each workflow run.
