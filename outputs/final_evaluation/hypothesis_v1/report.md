# Yield RCA Step 14 Evaluation Report

- Overall status: **PASS**
- Scenarios: 10
- Scenario pass rate: 100.0%
- Top-1 root-cause accuracy: 100.0%
- Top-3 recall: 100.0%
- Inconclusive handling rate: 100.0%
- False-positive rate: 0.0%
- Evidence traceability: 100.0%
- Hallucinated citation rate: 0.0% (0/2381)
- Confidence calibration ECE: 0.0500 (Brier: 0.0025, n=3)
- Tool latency: mean 38.692 ms, P50 2.156 ms, P95 248.125 ms, max 436.245 ms
- End-to-end latency: mean 380.207 ms, P50 316.956 ms, P95 589.885 ms, max 589.885 ms
- Scope accuracy: 100.0%
- Required Warning recall: 100.0%

## Scenario Results

| Scenario | Result | Expected | Actual | Top-3 candidates | Confidence | Runtime (ms) |
| --- | --- | --- | --- | --- | ---: | ---: |
| EVAL_CMP_SLURRY_DECLINE | PASS | supported: CMP_CU03_CH02 slurry delivery degradation | supported: CMP_CU03_CH02 slurry delivery degradation | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 95.0% | 589.885 |
| EVAL_RECIPE_VERSION_CHANGE | PASS | supported: CU_CMP_40N R19 recipe version change | supported: CU_CMP_40N R19 recipe version change | CU_CMP_40N R19 recipe version change<br>Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion | 95.0% | 463.727 |
| EVAL_SINGLE_CHAMBER | PASS | supported: CVD_ILD_01_CH02 deposition rate excursion | supported: CVD_ILD_01_CH02 deposition rate excursion | CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed<br>Slurry delivery degradation reduced Cu CMP removal rate | 95.0% | 388.209 |
| EVAL_SCRATCH_WAT_FAIL | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 313.206 |
| EVAL_MES_NO_FDC | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 309.019 |
| EVAL_FDC_NO_YIELD | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 412.401 |
| EVAL_CONFLICTING_EVIDENCE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CMP_CU03_CH02 slurry delivery degradation<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 398.442 |
| EVAL_MISSING_DATA | PASS | inconclusive: inconclusive | inconclusive: inconclusive | CVD_ILD_01_CH02 deposition rate excursion<br>Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 314.213 |
| EVAL_HIGH_HISTORY_MATCH | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>Transient particle event<br>CVD_ILD_01_CH02 deposition rate excursion | 0.0% | 296.013 |
| EVAL_INCONCLUSIVE_ROOT_CAUSE | PASS | inconclusive: inconclusive | inconclusive: inconclusive | Slurry delivery degradation reduced Cu CMP removal rate<br>CVD_ILD_01_CH02 deposition rate excursion<br>Transient particle or handling event; exact source was not confirmed | 0.0% | 316.956 |

## Tool Latency By Name

| Tool | Calls | Mean (ms) | P50 (ms) | P95 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| analyze_lot_genealogy | 10 | 22.158 | 21.358 | 28.217 | 28.217 |
| analyze_parameter_shift | 10 | 3.413 | 2.423 | 6.548 | 6.548 |
| find_impact_lots | 10 | 35.796 | 23.778 | 64.732 | 64.732 |
| find_ooc_events | 10 | 0.089 | 0.073 | 0.214 | 0.214 |
| get_lot_context | 10 | 280.072 | 246.759 | 436.245 | 436.245 |
| perform_basic_spc_analysis | 10 | 5.671 | 4.329 | 10.154 | 10.154 |
| retrieve_similar_case | 20 | 0.162 | 0.140 | 0.287 | 0.558 |
| summarize_defect_wat | 10 | 0.702 | 0.660 | 1.022 | 1.022 |

## Failed Checks

No failed checks.
