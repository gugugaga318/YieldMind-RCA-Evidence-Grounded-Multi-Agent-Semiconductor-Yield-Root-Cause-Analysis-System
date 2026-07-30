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
- Tool latency: mean 40.402 ms, P50 2.189 ms, P95 264.240 ms, max 423.347 ms
- End-to-end latency: mean 397.309 ms, P50 346.202 ms, P95 589.174 ms, max 589.174 ms
- Scope accuracy: 100.0%
- Required Warning recall: 100.0%

## Scenario Results

| Scenario | Result | Expected | Actual | Top-3 candidates | Confidence | Runtime (ms) |
| --- | --- | --- | --- | --- | ---: | ---: |
| EVAL_CMP_SLURRY_DECLINE | PASS | supported: CMP_CU03_CH02 slurry delivery degradation | supported: CMP_CU03_CH02 slurry delivery degradation | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 95.0% | 589.174 |
| EVAL_RECIPE_VERSION_CHANGE | PASS | supported: CU_CMP_40N R19 recipe version change | supported: CU_CMP_40N R19 recipe version change | CU_CMP_40N R19 recipe version change<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 95.0% | 505.786 |
| EVAL_SINGLE_CHAMBER | PASS | supported: CVD_ILD_01_CH02 deposition rate excursion | supported: CVD_ILD_01_CH02 deposition rate excursion | CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed<br>Slurry delivery degradation reduced Cu CMP removal rate | 95.0% | 379.906 |
| EVAL_SCRATCH_WAT_FAIL | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 333.168 |
| EVAL_MES_NO_FDC | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 334.146 |
| EVAL_FDC_NO_YIELD | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 441.650 |
| EVAL_CONFLICTING_EVIDENCE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 419.491 |
| EVAL_MISSING_DATA | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CVD_ILD_01_CH02 deposition rate excursion<br>Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 306.525 |
| EVAL_HIGH_HISTORY_MATCH | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle event<br>CVD_ILD_01_CH02 deposition rate excursion | 0.0% | 317.043 |
| EVAL_INCONCLUSIVE_ROOT_CAUSE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 346.202 |

## Tool Latency By Name

| Tool | Calls | Mean (ms) | P50 (ms) | P95 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| analyze_lot_genealogy | 10 | 23.528 | 23.082 | 27.934 | 27.934 |
| analyze_parameter_shift | 10 | 3.672 | 2.204 | 6.573 | 6.573 |
| find_impact_lots | 10 | 39.182 | 27.594 | 66.942 | 66.942 |
| find_ooc_events | 10 | 0.114 | 0.072 | 0.417 | 0.417 |
| get_lot_context | 10 | 288.977 | 262.133 | 423.347 | 423.347 |
| perform_basic_spc_analysis | 10 | 6.878 | 7.228 | 10.853 | 10.853 |
| retrieve_similar_case | 20 | 0.189 | 0.142 | 0.239 | 0.957 |
| summarize_defect_wat | 10 | 0.892 | 0.666 | 1.407 | 1.407 |

## Failed Checks

No failed checks.
