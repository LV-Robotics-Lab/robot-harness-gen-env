# ACT placement robustness and failure-to-data diagnosis

The machine-readable source of truth is `artifacts/diagnosis/placement_robustness_diagnosis.json`.

## Decision

The failure-to-data iteration executed end to end, but policy promotion is rejected. Both signature-disjoint held-out placement evaluations completed 4/4 episodes and achieved only 1/4 task success. The result is a valid negative robustness result, not a promoted policy.

## Evidence chain

| Stage | Evidence | Result |
| --- | --- | --- |
| deterministic placement split | `runs/placement_robustness_apple_plate_verified_v1/placement_manifest.json` | 8 scripted-feasible train placements and 4 signature-disjoint eval placements |
| native varied collection | two repaired 8-placement collections plus the 2-placement supplement | 12 successful synchronized demonstrations across 7 unique placement signatures |
| ACT diversity gate | `runs/act_hdf5_apple_plate_varied_v3/diversity_report.json` | 12 episodes; 7 placement signatures; 11 unique action, qpos, and image trajectories |
| initial train | `runs/act_train_apple_plate_varied_chunk175_1200e_v3/train_smoke_report.json` | 1200 epochs; best validation loss 0.004906 |
| initial held-out eval | `runs/act_eval_apple_plate_varied_heldout_chunk175_v3/evaluate_report.json` | 4/4 execute, 1/4 task success, all placement signatures held out |
| execution-horizon control | `runs/act_eval_apple_plate_varied_heldout_chunk175_h40_v3/evaluate_report.json` | first 40 of 175 actions before replanning; still 1/4 |
| failure-to-data recovery | `runs/generated_collect_apple_plate_failed_eval_recovery_v4/collection_report.json` | the three failed placement IDs produce 3/3 successful synchronized expert demonstrations |
| repaired ACT diversity gate | `runs/act_hdf5_apple_plate_recovery_v4/diversity_report.json` | 15 episodes; 10 placement signatures; 14 unique action, qpos, and image trajectories |
| recovery train | `runs/act_train_apple_plate_recovery_chunk175_1200e_v4/train_smoke_report.json` | 1200 epochs; best validation loss 0.005318 |
| new held-out feasibility | `runs/generated_collect_apple_plate_holdout_candidates_eval_v4a/collection_report.json` | 6/8 candidates pass the scripted verifier; four diverse passing candidates are selected |
| new held-out eval | `runs/act_eval_apple_plate_recovery_new_holdout_chunk175_v4/evaluate_report.json` | 4/4 execute, 1/4 task success, all placement signatures held out |
| additional task regression | `runs/generated_collect_can_basket_native_regression_v4/collection_report.json` | can/basket current-code execution and synchronized recording pass 2/2 |

## Diagnosis

The original fixed-placement data problem is repaired: the varied datasets are not byte-identical, and the final training set covers ten explicit source/target pose signatures. Replanning every 40 actions does not improve the initial 1/4 score, so full-chunk execution is not the primary cause. Adding expert data for every initial failed placement also leaves a fresh held-out split at 1/4, so the targeted repair is insufficient for broad placement generalization under the current ACT configuration.

The evaluation infrastructure is not the blocker. Every learned-policy episode completes, all final placements pass the scripted task verifier before learned evaluation, and the final eval signatures are disjoint from all passed training signatures.

## Claim boundary

This work proves varied native demonstrations, byte-level trajectory diversity, two 1200-epoch ACT training runs, two held-out placement evaluations, one explicit failure-to-data iteration, and a fresh additional-task scripted regression. It does not prove robust policy quality. Object placement is varied, but lighting, camera, background, table height, and other visual/physics randomization remain disabled. The can/basket evidence is scripted execution, not cross-task learned-policy evaluation.

## Next data requirement

1. Increase successful unique placement coverage substantially across the declared source and target regions.
2. Compare the current 96x72 head camera against a higher-resolution observation and an explicit object-pose-conditioned baseline.
3. Train and evaluate declared lighting, camera, background, and table-height variations.
4. Train and evaluate a learned policy for at least one additional generated task before promotion.
