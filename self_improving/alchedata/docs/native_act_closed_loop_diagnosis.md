# Native Synchronized ACT Closed-Loop Diagnosis

## Outcome

The generated apple/plate task now completes a fixed-scene `/collect -> /train -> /evaluate` loop with RoboTwin-native synchronized observations and actions. The final ACT checkpoint succeeds in 3/3 evaluations for seeds 4, 5, and 6. All three evaluations use the same fixed placement with domain randomization disabled, so they prove repeatable execution, not scene-distribution generalization.

The machine-readable diagnosis is `artifacts/diagnosis/native_act_closed_loop_diagnosis.json`.

## Evidence Chain

| stage | artifact | result |
|---|---|---|
| native `/collect` | `runs/generated_collect_apple_plate_native_sync/collection_report.json` | 4/4 synchronized recordings, 162 frames each; 3/4 scripted task successes |
| ACT conversion | `runs/act_hdf5_native_sync/conversion_report.json` | 3 successful source episodes converted, 1 failed source episode skipped |
| loader gate | `runs/act_hdf5_native_sync/load_data_report.json` | image `[1,3,72,96]`, qpos `[14]`, action `[161,14]` |
| expert action replay | `runs/act_action_replay_native_sync/replay_report.json` | fresh-reset qpos error 0, all 161 actions execute, task success true |
| chunk-20 train/eval | `runs/act_train_native_sync_rgb_chunk20_1200e`, `runs/act_eval_native_sync_rgb_chunk20_1200e_best` | train passes; evaluation executes 3/3 and succeeds 0/3 |
| chunk-161 train/eval | `runs/act_train_native_sync_rgb_chunk161_1200e`, `runs/act_eval_native_sync_rgb_chunk161_1200e_best` | train passes; evaluation executes 3/3 and succeeds 3/3 |

## Bugs and Diagnosis

RoboTwin's native serializer passes RGB arrays directly to `cv2.imencode`, whose input convention is BGR. Decoding the stored JPEG without repair gives MSE 153.04 against the runtime head-camera image. Swapping decoded red and blue channels reduces MSE to 4.79. `scripts/convert_native_robotwin_collection_to_act_hdf5.py` applies this repair before writing ACT frames.

The three converted successful episodes are byte-identical in qpos, action, and image arrays. Their different seed labels do not provide trajectory diversity because placement and randomization are fixed.

The chunk-20 policy advances through a narrow portion of the expert trajectory and then stalls: its nearest expert action index ends at 82 in all three evaluations. Keeping the dataset, model size, optimizer, 1200 epochs, camera source, and evaluation seeds fixed while changing the chunk to the full 161-step episode raises success from 0/3 to 3/3. This supports temporal-progress collapse under repeated short-chunk replanning as the immediate failure mode for this dataset.

## Claim Boundary

The fixed-scene policy loop passes. Policy promotion remains blocked because the data contains only one unique successful trajectory and evaluation does not vary placement, lighting, clutter, camera pose, or task identity.

That next data requirement was executed in `docs/placement_robustness_diagnosis.md`: non-identical varied-placement data, held-out evaluation, targeted recovery data, and an additional-task scripted regression now exist. The resulting learned policy remains blocked at 1/4 success on both held-out splits; domain randomization and cross-task learned evaluation are still absent.
