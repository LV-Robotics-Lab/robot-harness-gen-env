# RoboTwin Smoke Evidence

## Scope

This workspace now has a local official RoboTwin checkout and asset subset on `jingxiang-b850m-c`. The smoke evidence has two levels:

- asset-load render smoke without robot/planner initialization;
- RoboTwin `Base_Task` smoke with CuRobo initialized in a RTX 5090-compatible environment;
- bounded `/collect` dry-runs that record a dataset manifest, camera samples, and object-state traces;
- official RoboTwin `play_once()` action rollout probes;
- generated selection2env `play_once()` action-repair probes, a bounded multi-episode generated rollout collection, an ACT HDF5 adapter/loader smoke, and a one-epoch ACT train smoke.

It does not execute policy evaluation or final task-success semantics for every generated selection2env scene. The bounded learned-policy evidence is limited to the declared apple/plate and can/basket gates described below.

## Installed Runtime

| item | evidence |
|---|---|
| GPU | `NVIDIA GeForce RTX 5090`, 32607 MiB, driver 580.159.03 |
| RoboTwin root | `external/RoboTwin` |
| RoboTwin commit | `c3ddfa8` shallow clone |
| object assets | `external/RoboTwin/assets/objects`, 125 top-level object dirs after asset download |
| embodiment assets | `external/RoboTwin/assets/embodiments` |
| asset-only Python env | `/home/jingxiang/miniconda3/envs/robotwin-smoke` |
| Base_Task/CuRobo Python env | `/home/jingxiang/miniconda3/envs/robotwin-5090` |
| PyTorch for Base_Task/CuRobo | `2.11.0+cu128`, CUDA 12.8 |
| CUDA capability test | RTX 5090 `sm_120` CUDA matmul passed |
| CuRobo | editable install from `external/RoboTwin/envs/curobo`, commit `d64c4b005459db10c5dd867d8b30a87d5bda9bdb` |
| SAPIEN | `3.0.0b1` |
| render self-test | `external/RoboTwin/script/test_render.py` printed `Render Well` |

Required assets exist for the three supported smoke cases: `003_plate`, `015_laptop`, `034_knife`, `035_apple`, `069_vagetable`, and `110_basket`.

## Asset-Load Smoke Results

| case | report | status | objects | observer std | head std | pose delta |
|---|---|---|---|---:|---:|---|
| apple/plate | `runs/smoke_asset_apple_plate/smoke_report.json` | `pass_asset_load_render` | `apple_1`, `plate_1` | 13.670 | 15.660 | `apple_1=0.02461`, `plate_1=0.0` |
| laptop/knife | `runs/smoke_asset_laptop_knife/smoke_report.json` | `pass_asset_load_render` | `laptop_1`, `knife_1` | 15.407 | 22.540 | `laptop_1=0.0`, `knife_1=0.0` |
| vegetable/basket | `runs/smoke_asset_vegetable_basket/smoke_report.json` | `pass_asset_load_render` | `vagetable_1`, `basket_1` | 24.641 | 24.046 | `vagetable_1=0.01575`, `basket_1=0.0` |

The committed PNGs are the quick visual proof surface:

- `runs/smoke_asset_apple_plate/observer_camera.png`
- `runs/smoke_asset_laptop_knife/observer_camera.png`
- `runs/smoke_asset_vegetable_basket/observer_camera.png`

Visual review notes:

- apple/plate loads both objects, but the apple is rendered beside the plate, not on top of it;
- laptop/knife loads both objects, including the articulated laptop asset;
- vegetable/basket loads both objects with the basket open upward, but the vegetable is rendered beside the basket, not inside it.

Therefore these are asset-load render smoke passes, not task-success passes.

## Base_Task / CuRobo Smoke Results

| case | report | status | objects | note |
|---|---|---|---|---|
| apple/plate | `runs/smoke_basetask_apple_plate/smoke_report.json` | `pass` | `apple_1`, `plate_1` | `Base_Task` scene includes RoboTwin arms and CuRobo init |
| laptop/knife | `runs/smoke_basetask_laptop_knife/smoke_report.json` | `pass` | `laptop_1`, `knife_1` | articulated laptop and knife load through `Base_Task` |
| vegetable/basket | `runs/smoke_basetask_vegetable_basket/smoke_report.json` | `pass` | `vagetable_1`, `basket_1` | basket and vegetable load through `Base_Task` |

The committed PNGs are:

- `runs/smoke_basetask_apple_plate/observer_camera.png`
- `runs/smoke_basetask_laptop_knife/observer_camera.png`
- `runs/smoke_basetask_vegetable_basket/observer_camera.png`

These images include the RoboTwin dual-arm setup, table, and selected objects. They prove planner-stack initialization and render capture, not policy execution.

## /collect Dry-Run Results

| case | report | status | manifest | object trace | observations |
|---|---|---|---|---|---:|
| apple/plate | `runs/collect_dryrun_apple_plate/collect_report.json` | `pass_collect_dry_run` | `runs/collect_dryrun_apple_plate/dataset_manifest.json` | `runs/collect_dryrun_apple_plate/episode_000/object_states.jsonl` | 10 PNGs + observer MP4 |
| laptop/knife | `runs/collect_dryrun_laptop_knife/collect_report.json` | `pass_collect_dry_run` | `runs/collect_dryrun_laptop_knife/dataset_manifest.json` | `runs/collect_dryrun_laptop_knife/episode_000/object_states.jsonl` | 10 PNGs + observer MP4 |
| vegetable/basket | `runs/collect_dryrun_vegetable_basket/collect_report.json` | `pass_collect_dry_run` | `runs/collect_dryrun_vegetable_basket/dataset_manifest.json` | `runs/collect_dryrun_vegetable_basket/episode_000/object_states.jsonl` | 10 PNGs + observer MP4 |

Each dry-run starts RoboTwin `Base_Task`, loads the selection2env placement, steps 96 frames, captures observer/head camera samples at steps 0/24/48/72/96, and writes object poses for every step. The reports explicitly set `policy_execution=not_run` and `task_success_claim=not_claimed`.

The final acceptance rerun under `runs/final_acceptance_20260715/collect_apple_plate` and `collect_laptop_knife` keeps those five PNG sample pairs but writes a continuous observer frame at every simulator step. Both reports pass with 97 encoded frames, 12 fps, 8.083 seconds, `simulator_step_stride=1`, and `video_endpoint_only=false`; stdout and stderr are retained beside each report.

## Official Action Rollout Probes

| case | report | status | `check_success` | move events | planned paths | observer video |
|---|---|---|---|---:|---:|---|
| open laptop | `runs/official_rollout_open_laptop/rollout_report.json` | `pass_action_rollout` | true | 4 | 5 | 640 frames / 53.33 s |
| place mouse on pad | `runs/official_rollout_place_mouse_pad/rollout_report.json` | `pass_action_rollout` | true | 3 | 5 | 531 frames / 44.25 s |
| place container on plate | `runs/official_rollout_place_container_plate/rollout_report.json` | `pass_action_rollout` | true | 4 | 6 | 527 frames / 43.92 s |

Each official action probe starts an official RoboTwin task class, runs its `play_once()`, records `move_events.jsonl`, saves initial/final observer and head camera images, and writes a continuous observer MP4 through RoboTwin's native `save_freq/_take_picture` step hook at stride 4. The reports set `video_endpoint_only=false`; `ffprobe` confirms the encoded frame counts above, and frame-MD5 inspection found every encoded frame unique in all three videos. The probes also record dense diagnosis categories plus next data requirements and prove the RoboTwin action/planner path is usable on `robotwin-5090`.

`artifacts/openxsim_benchmarks/manifest.json` packages these same three probes into per-task PEARL bundles. Each bundle has a typed benchmark manifest, `run_state.json`, command-level `events.jsonl`, scene/task manifests, a standalone failure diagnosis, and links to the original rollout report, rollout/move events, camera images, and observer video. The bundles mark `learned_policy=false`; they are command-loop organization and scripted success-verifier evidence, not learned-policy results.

## Generated Selection2Env Action Rollout Probes

| case | report | status | `check_success` | move events | planned paths | relation |
|---|---|---|---|---:|---:|---|
| apple onto plate action repair | `runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json` | `pass_generated_action_rollout` | true | 4 | 6 | apple/plate XY distance `0.03184 m` |
| can into basket action repair | `runs/generated_rollout_can_basket_action_repair/rollout_report.json` | `pass_generated_action_rollout` | true | 5 | 7 | can/basket XY distance `0.04404 m` |

The apple/plate pass uses `runs/probe_static_apple_plate_action_repair/final_placement.json`, a reachability-repaired placement that moves the plate from the original right-side static scene into the center clearance area. The generated probe builds a `GeneratedSelection2EnvTask(Base_Task)`, loads the generated placement, runs a generated `play_once()` template, records `events.jsonl`, `move_events.jsonl`, initial/final observer and head camera images, and `observer_rollout_probe.mp4`.

The can/basket repair uses `runs/probe_static_can_basket_action_repair/final_placement.json`. The direct top-down attempt previously missed the basket relation, but the canonical repaired probe uses the generated `auto` motion path: `grasp_actor`, `place_actor`, then a direct top-down fallback after the planner place attempt. The final can/basket relation is below the verifier threshold and the rollout now records `check_success=true`.

`artifacts/generated_rollout_repair/generated_selection2env_action_repair_summary.json` is the compact generated action-repair summary.

## Same-Scene Task-Decoupling Acceptance

`runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate` and `apple_to_left_front` both reset from placement SHA-256 `c39169d68ab617debdd8b4462d7bc9d30ad6fae0ce5c3d4aa9e4a34893bee592`. Their initial observer images are byte-identical with SHA-256 `18e53b736c0a9716e7489dc889a2c2375c7d26fe3078d207dccf61e8cac53012`. The primary `place_on` task passes with a 589-frame video; the alternate `place_in_region` task passes with a 585-frame video. Both use the native RoboTwin step hook at stride 4 and set `video_endpoint_only=false`.

`artifacts/scene_task_decoupling/apple_plate_two_tasks.json` binds the two task programs, placement hash, initial image hash, verifier results, relation metrics, and encoded video counts. This proves one scene can execute two task specs; it does not prove cross-task learned-policy transfer.

## Generated Rollout Collection

| case | report | status | episodes | pass/fail | claim boundary |
|---|---|---|---:|---:|---|
| apple onto plate action repair | `runs/generated_collect_apple_plate_action_repair/collection_report.json` | `pass_generated_rollout_collection` | 3 | 3/0 | generated scripted `play_once`, no learned policy training/evaluation |
| can into basket action repair | `runs/generated_collect_can_basket_action_repair/collection_report.json` | `pass_generated_rollout_collection` | 3 | 3/0 | generated scripted `play_once`, no learned policy training/evaluation |

The collection runner writes per-episode rollout reports, events, move events, policy traces, camera images, observer videos, process logs, and an aggregate `dataset_manifest.json`. Apple/plate uses seeds 0, 1, and 3 because seed 2 did not satisfy the final relation after rerun; can/basket uses seeds 0, 1, and 2 with the generated `auto` motion path. The apple/plate and can/basket collections are demonstration collection gates for `/train`, but they are not policy-training results.

## ACT HDF5 Adapter Smoke

| evidence | status | episodes | loader proof | claim boundary |
|---|---|---:|---|---|
| `runs/act_hdf5_generated_smoke/conversion_report.json` | `pass_act_hdf5_adapter_smoke` | 6 | `runs/act_hdf5_generated_smoke/load_data_report.json` reports `pass_act_hdf5_loader_smoke` | ACT format and dataset utility loading only |

The adapter converts generated `policy_trace.json` segment positions into six ACT-compatible HDF5 episodes under `runs/act_hdf5_generated_smoke/data`, writes `SIM_TASK_CONFIGS.generated.json`, and verifies one sample through RoboTwin ACT `utils.EpisodicDataset`. The adapter smoke uses 96x72 observer frames to keep artifacts small. The separate train smoke below covers checkpoint writing; neither smoke evaluates a policy.

## ACT Train Smoke

| evidence | status | checkpoint | validation loss | claim boundary |
|---|---|---|---:|---|
| `runs/act_train_smoke_generated/train_smoke_report.json` | `pass_act_train_smoke` | `runs/act_train_smoke_generated/act_ckpt/policy_best.ckpt` | `10.308693` | one-epoch train smoke only |

The train smoke installs the missing ACT dependencies in `robotwin-5090`, points ACT `SIM_TASK_CONFIGS.json` at `runs/act_hdf5_generated_smoke/SIM_TASK_CONFIGS.generated.json`, runs `imitate_episodes.py` for one epoch with reduced model dimensions, and writes dataset stats, plots, and `policy_best.ckpt`. This proves import, generated-HDF5 data loading, loss computation, and checkpoint writing. It does not prove learned policy quality or `/evaluate`.

## Generated ACT Evaluate Smoke

| evidence | infrastructure | held-out seeds | task success | claim boundary |
|---|---|---|---:|---|
| `runs/act_eval_smoke_generated/evaluate_report.json` | `pass_generated_act_evaluate_execution`, 3/3 episodes completed | 4, 5, 6 versus source-collection seeds 0, 1, 2, 3 | 0/3 | learned inference/action/verifier hook only |

`scripts/run_generated_act_eval_smoke.py` instantiates the generated apple/plate placement, loads all `12,078,607` ACT parameters from `policy_best.ckpt`, maps the converter's `cam_high` key back to its actual observer-camera source, and sends 20 predicted qpos actions per episode through RoboTwin `take_action`. Each episode writes action traces, events, initial/final camera images, an observer video, relation metrics, and the required dense diagnosis categories. The three evaluations complete without infrastructure errors, but the apple does not move and the task verifier fails in all episodes. Fixed placement and disabled domain randomization mean the held-out seeds test repeated execution, not scene-distribution generalization.

The upstream `policy/ACT/eval.sh` path remains a distinct blocker: it expects `policy_last.ckpt`, hard-codes three cameras, and imports an upstream `envs.<task_name>` module. The bounded generated-task adapter uses the actual train-smoke checkpoint and one-camera architecture without claiming that the default wrapper is repaired.

## Native Synchronized ACT Closed Loop

| stage | evidence | result | claim boundary |
|---|---|---|---|
| native recording | `runs/generated_collect_apple_plate_native_sync/collection_report.json` | 4/4 native recordings contain 162 synchronized frames; 3/4 scripted task executions pass | one fixed placement |
| ACT conversion/load | `runs/act_hdf5_native_sync/conversion_report.json`, `load_data_report.json` | 3 successful episodes, each 161 aligned steps; loader passes | all three episodes are byte-identical |
| expert replay | `runs/act_action_replay_native_sync/replay_report.json` | initial qpos max error 0; 161 actions execute; task success true | action semantics, not learned policy |
| chunk-20 policy | `runs/act_train_native_sync_rgb_chunk20_1200e/`, `runs/act_eval_native_sync_rgb_chunk20_1200e_best/` | train passes; eval executes 3/3 and succeeds 0/3 | matched failure baseline |
| chunk-161 policy | `runs/act_train_native_sync_rgb_chunk161_1200e/`, `runs/act_eval_native_sync_rgb_chunk161_1200e_best/` | train passes; eval executes 3/3 and succeeds 3/3 | fixed placement, no domain randomization |
| varied-placement ACT failure-to-data loop | `artifacts/diagnosis/placement_robustness_diagnosis.json` | 15 successful episodes across 10 placements and 14 unique trajectories; two held-out eval splits execute 4/4 and score 1/4 | ACT promotion rejected |
| bounded pose-conditioned baseline | `artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json` | 4/4 signature-disjoint held-out placements, 4/4 declared domain randomization, 3/3 fixed-placement can/basket seeds | accepted only for the privileged open-loop SceneAgent gate |

The native JPEG serializer applies `cv2.imencode` directly to runtime RGB arrays. `scripts/convert_native_robotwin_collection_to_act_hdf5.py` reverses the decoded red/blue channels; the diagnostic MSE against the runtime reference drops from 153.04 to 4.79. The converted action contract is `qpos[t], head_camera_rgb[t] -> action=qpos[t+1]`.

The matched chunk experiment holds the source data, 1200 epochs, reduced ACT architecture, camera source, placement, and seeds constant. The chunk-20 policy advances only to nearest expert index 82 and fails 0/3. The full 161-step chunk reaches task success at policy step 130 in all three evaluations. See `docs/native_act_closed_loop_diagnosis.md` and `artifacts/diagnosis/native_act_closed_loop_diagnosis.json`.

The ACT placement follow-up removes the byte-identical-data blocker, evaluates four scripted-feasible held-out placements, collects expert data for all three failures, retrains, and evaluates a second four-placement held-out set. Both ACT policies score 1/4. A 40-action replanning prefix also stays at 1/4. See `docs/placement_robustness_diagnosis.md`; the failure-to-data loop passes as execution evidence, while ACT promotion is rejected. A separate supervised pose-conditioned trajectory regressor passes the bounded gates above, but uses privileged initial object poses and open-loop actions.

## Visual Review

`artifacts/visual_review/selection2env_visual_review.json` binds exact hashes for the three Base_Task observer/head image pairs and explicitly checks collision/penetration, visual plausibility, task intent, occlusion, reachability, and support/stability. Runtime reports corroborate reachability and stability; final task relation remains `not_claimed`.

`artifacts/visual_review/generated_rollout_final_relation_review.json` reviews the generated apple/plate and can/basket collection final observer frames, with the contact sheet at `artifacts/visual_review/generated_rollout_final_relation_contact_sheet.png`. It marks final relation support as passing for those six generated episodes while keeping learned-policy `/train` and `/evaluate` outside the claim.

## Resolved Planner-Stack Issue

The default RoboTwin requirement pins `torch==2.4.1`; that installed as `torch 2.4.1+cu121` in `robotwin-smoke`. The RTX 5090 reports `sm_120`, while that wheel only supports up to `sm_90`. A direct CUDA tensor test failed with:

```text
RuntimeError: CUDA error: no kernel image is available for execution on the device
```

The working fix is `robotwin-5090`:

- Python 3.10;
- `torch 2.11.0+cu128`;
- CUDA 12.8 runtime;
- `CUDA_HOME=/home/jingxiang/miniconda3/envs/wmfactory`;
- `TORCH_CUDA_ARCH_LIST=12.0`;
- CuRobo editable install built with additional CUDA include/library paths from `wmfactory`.

## Post-TODO Research Gates

- visual or language-conditioned closed-loop control beyond privileged initial-pose conditioning;
- broader task and placement transfer beyond the bounded apple/plate and fixed-placement can/basket gates;
- default ACT process/train/eval wrapper wiring if that upstream route must be supported;
- broader Articraft scale/material/task-suitability coverage beyond the five sampled archive probes;
- actual generation2env forge/material execution, which the dashboard acceptance explicitly excludes.
