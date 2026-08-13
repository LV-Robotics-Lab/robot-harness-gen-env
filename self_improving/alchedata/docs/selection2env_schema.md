# selection2env v0 Schema and Handoff

## Purpose

`selection2env` is the P0 `/gen-env` route. It does not synthesize new assets. It takes a task intent and selects existing assets, support surfaces, placement regions, initial poses, observation requirements, robot constraints, and verifiers. The output must be consumable by a RoboTwin/SAPIEN task-program adapter and by the PEARL `/collect` command.

This separates scene construction from task definition. The same selected scene may support multiple task specs; the workspace records this through the shared `scene_id` and alternate task-program inputs.

## Required Fields

The executable contract is `schemas/selection2env.schema.json`.

| field | role |
|---|---|
| `task_text` | Natural-language task intent. |
| `catalog_sources` | RoboTwin, AgenticSim, and Articraft source counts, execution eligibility, and query coverage. |
| `asset_candidates` | Accepted, rejected, and missing assets with reasons. |
| `selected_assets` | The actual RoboTwin/AgenticSim assets chosen for the scene. |
| `placement_regions` | Named tabletop or workspace regions. |
| `support_surface` | Surface type, coordinate frame, and bounds. |
| `pose_constraints` | Initial xyz/qpos/z policy and physical flags per object. |
| `camera_observation` | Required views and semantic visual checks. |
| `robot_constraints` | Embodiment, reachability, and action-interface constraints. |
| `success_verifier` | Static, simulator, visual, and task-intent checks. |
| `blockers` | Missing assets, missing simulator path, missing dependency, or unsupported task evidence. |
| `handoff` | `/collect`, `/train`, and `/evaluate` artifact interfaces. |

## Current Evidence in This Workspace

The RoboTwin Text2Env reference repo is cloned and reproducibly extended at:

```text
external/robotwin-text2env-demo
external_sources.lock.json
vendor/patches/robotwin-text2env-selection2env.patch
```

`artifacts/adapter_catalog/robotwin_discovered_catalog.json` contains 123 objects discovered from the live RoboTwin root. `external/robotwin-text2env-demo/asset_catalogs/agenticsim_placement_assets.json` snapshots eight AgenticSim placement-agent aliases from source commit `f34d56a`; all eight resolve to assets in the discovered RoboTwin catalog. `artifacts/adapter_catalog/selection2env_catalog_sources.json` verifies those counts and keeps AgenticSim aliases execution-eligible only when the RoboTwin backend asset exists.

Articraft-10K is not mounted as full archives on this host, but public Hugging Face metadata is parsed into a searchable catalog. The probe and manifest artifacts are:

```text
artifacts/adapter_catalog/articraft10k_probe.json
artifacts/adapter_catalog/articraft10k_manifest.json
artifacts/adapter_catalog/articraft10k_search_examples.json
runs/articraft_archive_probe_weight_bench/probe_report.json
runs/articraft_archive_probe_microscope/probe_report.json
runs/articraft_archive_probe_monitor/probe_report.json
runs/articraft_archive_probe_washing_machine/probe_report.json
runs/articraft_archive_probe_zippo_lighter/probe_report.json
```

`artifacts/adapter_catalog/articraft10k_manifest.json` contains `9996` `.tar.gz` archive entries from `camvsl/Articraft-10K`, parsed into semantic text, semantic tokens, source paths, and stable Hugging Face URLs. The selected archive probes now cover five assets. Weight bench, microscope, and monitor download/extract `model.urdf`, parse links/joints/collision geometry, load as SAPIEN `PhysxArticulation`, and step physics 20 times. Washing machine and Zippo lighter also load/step in SAPIEN, but fail the collision-geometry gate because their URDFs contain zero collision geometries. This is sampled archive/import and blocker evidence only: no broad catalog import, scale suitability, material fidelity, or policy task success is claimed.

The normalized task artifacts are:

| case | task text | status | evidence |
|---|---|---|---|
| apple plate | `move the apple onto the plate` | `pass_sim_smoke` | `artifacts/selection2env/task_apple_plate.json` |
| laptop knife | `place the laptop to the right of the knife` | `pass_sim_smoke` | `artifacts/selection2env/task_laptop_knife.json` |
| vegetable basket | `put the vegetable into the basket` | `pass_sim_smoke` | `artifacts/selection2env/task_vegetable_basket.json` |
| drawer mug | `open the drawer and place the mug inside` | `unsupported_blocker` after static scene pass | `runs/probe_static_drawer_mug_unified/scene_generation_summary.json` |

The source placement pipelines remain `pass_static_only`, but the normalized supported artifacts are `pass_sim_smoke` because their asset-load render, `Base_Task`/CuRobo, and `/collect` dry-run evidence all pass. The drawer/mug source now grounds both `036_cabinet` and `039_mug` and emits a static scene module; it remains unsupported because no verified drawer-open/interior-place action and success verifier exist.

Three supported cases now also have RoboTwin/SAPIEN asset-load render smoke, stronger `Base_Task`/CuRobo smoke, and bounded `/collect` dry-runs:

| case | asset smoke | Base_Task/CuRobo smoke | `/collect` dry-run |
|---|---|---|---|
| apple plate | `runs/smoke_asset_apple_plate/smoke_report.json` | `runs/smoke_basetask_apple_plate/smoke_report.json` | `runs/collect_dryrun_apple_plate/collect_report.json` |
| laptop knife | `runs/smoke_asset_laptop_knife/smoke_report.json` | `runs/smoke_basetask_laptop_knife/smoke_report.json` | `runs/collect_dryrun_laptop_knife/collect_report.json` |
| vegetable basket | `runs/smoke_asset_vegetable_basket/smoke_report.json` | `runs/smoke_basetask_vegetable_basket/smoke_report.json` | `runs/collect_dryrun_vegetable_basket/collect_report.json` |

`pass_asset_load_render` proves the official object assets load into SAPIEN and render into nonblank observer/head images. `pass` in `runs/smoke_basetask_*` additionally proves RoboTwin `Base_Task` and CuRobo initialize on the RTX 5090-compatible `robotwin-5090` env. `pass_collect_dry_run` proves `/collect` can write a dataset manifest, camera samples, and object-state traces for the loaded placement. `runs/official_rollout_*` proves three official RoboTwin task `play_once()` probes pass with `check_success=true`.

The final acceptance rerun adds `runs/final_acceptance_20260715/collect_apple_plate` and `collect_laptop_knife`. Each run passes on `jingxiang-b850m-c`, records 97 consecutive simulator-step observer frames at 12 fps, retains five observer/head PNG sample pairs, and writes stdout/stderr logs. These replace sparse endpoint-style dry-run videos as the primary acceptance media.

Generated action-repair evidence is now split:

| case | generated action evidence | result |
|---|---|---|
| apple plate | `runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json` | generated `play_once()` action-repair pass with `check_success=true` |
| can basket | `runs/generated_rollout_can_basket_action_repair/rollout_report.json` | generated `play_once()` action-repair pass with `check_success=true` |

The multi-episode generated demonstration collections are:

| case | generated collection evidence | result |
|---|---|---|
| apple plate | `runs/generated_collect_apple_plate_action_repair/collection_report.json` | 3 generated `play_once()` episodes, 3 pass, 0 fail |
| can basket | `runs/generated_collect_can_basket_action_repair/collection_report.json` | 3 generated `play_once()` episodes, 3 pass, 0 fail |

These generated probes and collections are scripted demonstrations and do not themselves prove learned-policy quality, held-out robustness, or semantic success for every generated scene.

## Same Scene, Two Tasks

`schemas/robotwin_task_program_input.schema.json` defines explicit source/target bindings and pins the placement bytes with `placement_sha256`. The two task specs below share `scene_apple_plate_shared_two_task_v0` and the exact placement SHA `c39169d68ab617debdd8b4462d7bc9d30ad6fae0ce5c3d4aa9e4a34893bee592`:

- `artifacts/task_program_inputs/task_apple_plate.json`: place `apple_1` on `plate_1`;
- `artifacts/task_program_inputs/task_apple_plate_to_left_front.json`: place `apple_1` in `left_front_reachable_area`.

Both reset from `runs/scene_task_decoupling/shared_apple_plate_scene.json`, execute generated `play_once()`, and return `check_success=true`. The fresh acceptance runs under `runs/final_acceptance_20260715/scene_task_decoupling/` have byte-identical initial observer pixels and continuous 589/585-frame videos captured through RoboTwin's native simulator-step hook. `artifacts/scene_task_decoupling/apple_plate_two_tasks.json` independently checks task IDs, scene ID, placement path/SHA, bindings, initial image hash, rollout status, and encoded video metadata. Failed pre-repair attempts remain under `runs/scene_task_decoupling/*_fail/`.

`artifacts/visual_review/generated_rollout_final_relation_review.json` adds agent visual review of the apple/plate and can/basket collection final observer frames. That review supports the generated final relation for those six episodes, but does not replace `/train`, `/evaluate`, or held-out semantic review.

`runs/act_hdf5_generated_smoke/conversion_report.json` and `runs/act_hdf5_generated_smoke/load_data_report.json` add an ACT data-format smoke: six generated planner-trace episodes are converted to ACT-compatible HDF5 with 96x72 observer frames and are readable through RoboTwin ACT dataset utilities. `runs/act_train_smoke_generated/train_smoke_report.json` adds a one-epoch ACT train smoke that writes `policy_best.ckpt`. `runs/act_eval_smoke_generated/evaluate_report.json` then loads that checkpoint and completes 3/3 bounded apple/plate evaluations for held-out source-collection seeds 4, 5, and 6, with 0/3 task success. This resolves the generated-task inference/action/verifier hook, not policy quality, randomized robustness, or default upstream wrapper integration.

The production-oriented ACT adapter path is separate. `runs/generated_collect_apple_plate_native_sync/collection_report.json` records RoboTwin-native synchronized head-camera and 14-D qpos frames. `runs/act_hdf5_native_sync/conversion_report.json` repairs the native JPEG channel order and aligns `qpos[t], image[t] -> action=qpos[t+1]`; its loader report sees three 161-step episodes. `runs/act_action_replay_native_sync/replay_report.json` proves all 161 converted expert actions succeed from an exact fresh reset. A 1200-epoch chunk-20 policy scores 0/3, while `runs/act_train_native_sync_rgb_chunk161_1200e/` plus `runs/act_eval_native_sync_rgb_chunk161_1200e_best/` score 3/3 with a full 161-step chunk. The follow-up ACT placement audit grows data to 15 episodes, 10 pose signatures, and 14 unique action/qpos/image trajectories, but both signature-disjoint held-out eval splits score 1/4; that ACT branch remains unpromoted.

`artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json` records a separate bounded baseline. It learns a phase-aligned trajectory regressor from the same 15 successful demonstrations using privileged initial source/target poses, then passes 4/4 signature-disjoint held-out placements, 4/4 held-out placements with declared background/light/camera/table-height randomization, and 3/3 fixed-placement can/basket seeds. This promotes only the privileged open-loop baseline for the bounded SceneAgent gate; it does not promote ACT or establish visual, language-conditioned, closed-loop, or broad task transfer.

## VLM / Agent Critique Requirements

The critic must check more than collision:

- task-intent coverage: selected assets and relations match the prompt;
- visual plausibility: flat objects lie flat unless the prompt requires otherwise;
- occlusion: required objects are visible from at least one task camera;
- reachability: poses are inside named robot-reachable regions;
- support and stability: containers/support surfaces plausibly hold target objects;
- task decoupling: a scene can receive a new task spec without regenerating assets.

Current artifact: `artifacts/visual_review/selection2env_visual_review.json` binds exact image hashes and explicitly checks collision/penetration, visual plausibility, task intent, occlusion, reachability, and support/stability for all three supported cases. Reachability and stability also require runtime corroboration; they are not inferred from pixels alone. Final task-success semantics remain `not_claimed` in this initial-scene artifact.

## Gaochen Pipeline Handoff

`/collect` reads:

- `artifacts/selection2env/<task_id>.json`
- `artifacts/task_program_inputs/<task_id>.json`
- `runs/<case>/final_placement.json`
- `runs/<case>/validation_plan.json`

`/collect` writes:

- `run_state.json`
- `events.jsonl`
- rollout logs;
- camera previews;
- object state traces;
- failure diagnosis;
- next data requirement.

`/train` reads:

- rollout dataset manifest;
- policy config;
- data requirement spec;
- accepted task/scene manifest.

`/evaluate` reads:

- policy checkpoint;
- eval task set;
- verifier outputs;
- failure trace clusters.

## Failure Codes

| code | meaning | owner |
|---|---|---|
| `DEFAULT_ACT_PLACEMENT_ROBUSTNESS_FAILED` | The retained ACT recovery branch scores 1/4 on varied-placement holdout. The separate privileged pose-conditioned baseline passes its bounded gate, but does not repair or promote ACT. | Zheng Ye / Gaochen |
| `LEARNED_POLICY_TASK_COVERAGE_BOUNDARY` | A supported normalized task has selection2env/smoke evidence but no learned-policy evaluation attached; this is a post-TODO coverage boundary, not a selection2env completion blocker. | Zheng Ye / Gaochen |
| `DEFAULT_POLICY_WRAPPER_NOT_WIRED` | `runs/policy_train_eval_entrypoint_probe/probe_report.json` shows the default ACT process/train/eval wrappers still target old data/config/task-module conventions even though the bounded generated-task adapter executes. | Zheng Ye / Gaochen |
| `ARTICULATED_CONTAINER_TASK_API_UNSUPPORTED` | drawer and mug assets are found, but opening `036_cabinet`, placing inside it, and verifying containment are not wired as an executable task. | Zheng Ye / RoboTwin task adapter owner |
| `VISUAL_REVIEW_REQUIRED` | generic future-scene code when smoke artifacts exist but no VLM/human visual semantics review has accepted them; it is not active on the three supported current artifacts. | Boris / Zheng Ye |
