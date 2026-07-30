# Yield RCA Step 14 Evaluation Report

- Overall status: **PASS**
- Scenarios: 10
- Scenario pass rate: 100.0%
- Top-1 root-cause accuracy: 100.0%
- Top-3 recall: 100.0%
- Inconclusive handling rate: 100.0%
- False-positive rate: 0.0%
- Evidence traceability: 100.0%
- Hallucinated citation rate: 0.0% (0/2367)
- Confidence calibration ECE: 0.0507 (Brier: 0.0026, n=3)
- Tool latency: mean 51.195 ms, P50 3.168 ms, P95 341.361 ms, max 500.260 ms
- End-to-end latency: mean 501.304 ms, P50 445.328 ms, P95 696.573 ms, max 696.573 ms
- Scope accuracy: 100.0%
- Required Warning recall: 100.0%

## Scenario Results

| Scenario | Result | Expected | Actual | Top-3 candidates | Confidence | Runtime (ms) |
| --- | --- | --- | --- | --- | ---: | ---: |
| EVAL_CMP_SLURRY_DECLINE | PASS | supported: CMP_CU03_CH02 slurry delivery degradation | supported: CMP_CU03_CH02 slurry delivery degradation | CMP_CU03_CH02 slurry delivery degradation<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 95.0% | 696.573 |
| EVAL_RECIPE_VERSION_CHANGE | PASS | supported: CU_CMP_40N R19 recipe version change | supported: CU_CMP_40N R19 recipe version change | CU_CMP_40N R19 recipe version change<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 94.8% | 616.832 |
| EVAL_SINGLE_CHAMBER | PASS | supported: CVD_ILD_01_CH02 deposition rate excursion | supported: CVD_ILD_01_CH02 deposition rate excursion | CVD_ILD_01_CH02 deposition rate excursion<br>Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle or handling event; exact source was not confirmed | 95.0% | 484.051 |
| EVAL_SCRATCH_WAT_FAIL | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 60.0% | 415.630 |
| EVAL_MES_NO_FDC | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 60.0% | 445.328 |
| EVAL_FDC_NO_YIELD | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 60.0% | 543.770 |
| EVAL_CONFLICTING_EVIDENCE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 60.0% | 532.497 |
| EVAL_MISSING_DATA | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CVD_ILD_01_CH02 deposition rate excursion<br>Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle or handling event; exact source was not confirmed | 31.4% | 417.754 |
| EVAL_HIGH_HISTORY_MATCH | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle event<br>CVD_ILD_01_CH02 deposition rate excursion | 46.7% | 437.228 |
| EVAL_INCONCLUSIVE_ROOT_CAUSE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 46.7% | 423.381 |

## Tool Latency By Name

| Tool | Calls | Mean (ms) | P50 (ms) | P95 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| analyze_lot_genealogy | 10 | 29.837 | 29.826 | 41.285 | 41.285 |
| analyze_parameter_shift | 10 | 4.502 | 3.313 | 8.069 | 8.069 |
| find_impact_lots | 10 | 55.709 | 40.739 | 95.463 | 95.463 |
| find_ooc_events | 10 | 0.107 | 0.082 | 0.283 | 0.283 |
| get_lot_context | 10 | 362.180 | 327.619 | 500.260 | 500.260 |
| perform_basic_spc_analysis | 10 | 7.192 | 6.124 | 11.661 | 11.661 |
| retrieve_similar_case | 20 | 0.173 | 0.148 | 0.310 | 0.488 |
| summarize_defect_wat | 10 | 0.878 | 0.795 | 1.204 | 1.204 |

## Failed Checks

No failed checks.
