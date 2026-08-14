# Batch 21.2 Product Surface + Semantic Evaluation

Deterministic acceptance: **PASS**

The deterministic result requires the Fake-Qwen autonomous lane, the controlled compatibility path, and the fixed-workflow baseline. The optional real-Qwen smoke is reported separately and is not converted into a pass.

## Verification lanes

| Lane | Status | Result |
| --- | --- | --- |
| Autonomous Fake | PASS | 10/10 scenarios |
| Controlled ReAct | PASS | Scratch + Cu CMP compatibility path |
| Fixed workflow | PASS | 10/10 scenarios |
| Real Qwen smoke | SKIPPED | DASHSCOPE_API_KEY and RUN_REAL_QWEN_TEST=1 are not configured. |

## Five contract metrics

| Metric | Count | Rate |
| --- | --- | --- |
| Decision valid | 28/28 | 100.0% |
| Evidence gain (ACT only) | 18/20 | 90.0% |
| Redundant (ACT only) | 0/20 | 0.0% |
| Goal success (positive autonomous runs) | 6/6 | 100.0% |
| Stop correct (positive autonomous runs) | 6/6 | 100.0% |

Evidence gain is descriptive rather than a target of 100%: RCA reasoning correctly adds analysis without inventing new Evidence, so it records `evidence_gain=false` and `redundant=false`.

## Semantic negative cases

The semantic lane is a required acceptance boundary, not a sixth public metric.

| Scenario | Status | Link count | Review result |
| --- | --- | ---: | --- |
| SEMANTIC_MATERIAL_TRACE_NEGATIVE | PASS | 0 | `unsupported_capability` |

## Autonomous scenarios

| Scenario | Status | Intent | Action chain | Conclusion | Goal / Stop |
| --- | --- | --- | --- | --- | --- |
| AUTONOMOUS_LOT_IMPACT | PASS | impact_scope | find_shared_exposure | signal | True / True |
| AUTONOMOUS_LOT_SPC | PASS | spc_check | find_shared_exposure -> inspect_fdc_spc | signal | True / True |
| AUTONOMOUS_SCRATCH_CU_CMP_ROOT_CAUSE | PASS | root_cause | inspect_defect_pattern -> find_shared_exposure -> validate_shared_defect_pattern -> inspect_fdc_spc -> run_rca_reasoning | supported | True / True |
| AUTONOMOUS_LOT_HISTORY | PASS | historical_lookup | inspect_defect_pattern -> find_shared_exposure -> validate_shared_defect_pattern -> inspect_fdc_spc -> validate_historical_case | candidate | True / True |
| AUTONOMOUS_PRODUCT_IMPACT | PASS | impact_scope | find_shared_exposure | signal | True / True |
| AUTONOMOUS_PRODUCT_ROOT_CAUSE | PASS | root_cause | find_shared_exposure -> inspect_defect_pattern -> validate_shared_defect_pattern -> inspect_fdc_spc -> run_rca_reasoning | supported | True / True |
| AUTONOMOUS_PREMATURE_STOP_GATE | PASS | root_cause | (no action) | inconclusive | False / False |
| AUTONOMOUS_PARTIAL_EVIDENCE_STOP_GATE | PASS | root_cause | inspect_defect_pattern | signal | False / False |

The two premature-stop scenarios pass only when the Planner's proposed `supported` conclusion is downgraded. With no Evidence the result is `inconclusive`; with only defect Evidence and no supported Hypothesis it remains a `signal`. Both keep `goal_success=false` and `stop_correct=false`.

## Scratch + Cu CMP action audit

| Action / Agent | Execution reason | Inputs | Output Evidence | Observation |
| --- | --- | --- | --- | --- |
| inspect_defect_pattern / defect_wat | Root-cause assessment requires an observed product-outcome pattern. | defect=scratch, lot_id=LOT_A_001, module=CU_CMP | EV_DEFECT_SCRATCH, EV_WAT_LEAKAGE | Selected Lots show scratch/edge_dominant defect evidence and 1 WAT-failing Lots led by leakage; metrology has 0 out-of-spec Wafer records. |
| find_shared_exposure / mes | The observed outcome needs shared equipment, chamber, recipe, and timing context. | defect=scratch, lot_id=LOT_A_001, module=CU_CMP | EV_MES_SOURCE_LOT_CONTEXT, EV_WAT_SOURCE_LOT_ANOMALY, EV_FDC_EXCURSION_WINDOW, EV_MES_IMPACT_LOTS | Lot LOT_A_001 has 19 additional impact Lots sharing operation 6400 on CMP_CU03/CMP_CU03_CH02. |
| validate_shared_defect_pattern / defect_wat | Shared-exposure Lots require a second defect-pattern comparison before FDC RCA. | defect=scratch, lot_id=LOT_A_001, module=CU_CMP | EV_DEFECT_SCRATCH_SHARED_EXPOSURE, EV_WAT_LEAKAGE_SHARED_EXPOSURE | Selected Lots show scratch/edge_dominant defect evidence and 20 WAT-failing Lots led by leakage; metrology has 0 out-of-spec Wafer records. |
| inspect_fdc_spc / fdc | The shared exposure needs FDC/SPC process-mechanism evidence. | defect=scratch, lot_id=LOT_A_001, module=CU_CMP | EV_FDC_ENDPOINT_TIME, EV_FDC_SLURRY_FLOW, EV_SPC_BASELINE_STATUS | CMP_CU03/CMP_CU03_CH02 shows endpoint_time +16.7%, slurry_flow -12.0%, 0 recorded OOC events, and 0 SPC OOC parameters at operation 6400. |
| run_rca_reasoning / rca_reasoning | Scope, mechanism, and product-outcome findings are available for RCA gating. | defect=scratch, lot_id=LOT_A_001, module=CU_CMP | EV_DEFECT_SCRATCH, EV_WAT_LEAKAGE, EV_MES_SOURCE_LOT_CONTEXT, EV_WAT_SOURCE_LOT_ANOMALY, EV_FDC_EXCURSION_WINDOW, EV_MES_IMPACT_LOTS, EV_DEFECT_SCRATCH_SHARED_EXPOSURE, EV_WAT_LEAKAGE_SHARED_EXPOSURE, EV_FDC_ENDPOINT_TIME, EV_FDC_SLURRY_FLOW, EV_SPC_BASELINE_STATUS | Root cause: CMP_CU03_CH02 slurry delivery degradation (confidence 95%). |

Terminal STOP: Qwen selected the goal_satisfied stop boundary. The Python Evidence Gate committed the terminal Question transitions without changing Evidence or conclusion level. Qwen rationale: The deterministic planner reference reached an explicit goal_satisfied boundary. (`goal_satisfied`, final level `supported`).

## Compatibility fallback scenarios

| Scenario | Status | Qwen-attributed evaluation |
| --- | --- | --- |
| AUTONOMOUS_MID_LOOP_FALLBACK | PASS | null |
| AUTONOMOUS_INTENT_FALLBACK | PASS | null |

Fallback scenarios preserve completed work but deliberately leave `run_evaluation=null`; the controlled tail is not attributed to Qwen.

## Fixed-workflow baseline

Status: **PASS**; 10/10 scenarios passed; all established acceptance checks remained true.
