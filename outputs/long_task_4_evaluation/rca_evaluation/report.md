# Yield RCA Step 14 Evaluation Report

- Overall status: **PASS**
- Scenarios: 10
- Scenario pass rate: 100.0%
- Top-1 root-cause accuracy: 100.0%
- Top-3 recall: 100.0%
- Inconclusive handling rate: 100.0%
- False-positive rate: 0.0%
- Evidence traceability: 100.0%
- Hallucinated citation rate: 0.0% (0/2403)
- Confidence calibration ECE: 0.0500 (Brier: 0.0025, n=3)
- Tool latency: mean 43.097 ms, P50 2.571 ms, P95 321.636 ms, max 442.148 ms
- End-to-end latency: mean 421.490 ms, P50 397.439 ms, P95 555.095 ms, max 555.095 ms
- Scope accuracy: 100.0%
- Required Warning recall: 100.0%

## Scenario Results

| Scenario | Result | Expected | Actual | Top-3 candidates | Confidence | Runtime (ms) |
| --- | --- | --- | --- | --- | ---: | ---: |
| EVAL_CMP_SLURRY_DECLINE | PASS | supported: CMP_CU03_CH02 slurry delivery degradation | supported: CMP_CU03_CH02 slurry delivery degradation | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 95.0% | 555.095 |
| EVAL_RECIPE_VERSION_CHANGE | PASS | supported: CU_CMP_40N R19 recipe version change | supported: CU_CMP_40N R19 recipe version change | CU_CMP_40N R19 recipe version change<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 95.0% | 539.550 |
| EVAL_SINGLE_CHAMBER | PASS | supported: CVD_ILD_01_CH02 deposition rate excursion | supported: CVD_ILD_01_CH02 deposition rate excursion | CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed<br>Slurry delivery degradation reduced Cu CMP removal rate | 95.0% | 429.165 |
| EVAL_SCRATCH_WAT_FAIL | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 296.822 |
| EVAL_MES_NO_FDC | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 359.084 |
| EVAL_FDC_NO_YIELD | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 511.232 |
| EVAL_CONFLICTING_EVIDENCE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 397.439 |
| EVAL_MISSING_DATA | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CVD_ILD_01_CH02 deposition rate excursion<br>Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 480.693 |
| EVAL_HIGH_HISTORY_MATCH | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle event<br>CVD_ILD_01_CH02 deposition rate excursion | 0.0% | 307.798 |
| EVAL_INCONCLUSIVE_ROOT_CAUSE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 338.025 |

## Tool Latency By Name

| Tool | Calls | Mean (ms) | P50 (ms) | P95 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| analyze_lot_genealogy | 10 | 23.400 | 21.359 | 28.570 | 28.570 |
| analyze_parameter_shift | 10 | 3.809 | 2.564 | 7.065 | 7.065 |
| find_impact_lots | 10 | 38.173 | 31.680 | 63.649 | 63.649 |
| find_ooc_events | 10 | 0.096 | 0.070 | 0.200 | 0.200 |
| get_lot_context | 10 | 314.182 | 269.264 | 442.148 | 442.148 |
| perform_basic_spc_analysis | 10 | 6.153 | 4.349 | 10.218 | 10.218 |
| retrieve_similar_case | 20 | 0.207 | 0.168 | 0.290 | 0.588 |
| summarize_defect_wat | 10 | 1.648 | 0.782 | 9.188 | 9.188 |

## Failed Checks

No failed checks.
