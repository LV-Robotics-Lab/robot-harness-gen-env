# Alchedata Self-Improving Agents Workspace

This workspace turns the dashboard TODOs for "Self-Improving Agents for Physical AI" into concrete artifacts on `jingxiang-b850m-c`.

## Remote target

- Host: `jingxiang-b850m-c`
- Tailscale IP: `100.64.0.6`
- User: `jingxiang`
- GPU observed before workspace setup: `NVIDIA GeForce RTX 5090`, 32607 MiB, driver 580.159.03.

## Source of scope

The active dashboard/TODO scope is the Alchedata/Self-Improving Agents track:

1. `SceneAgent / selection2env`: connect natural-language task intent to existing RoboTwin/AgenticSim/Articraft-style assets, placements, robot constraints, camera/observation fields, and success verifiers.
2. `Text2Env literature review`: position selection2env against text2task, text2scene, text2asset, generation2env, simulation-in-the-loop repair, data generation, and policy evaluation.
3. `PEARL Open X Sim command loop`: define `/gen-env`, `/collect`, `/train`, `/evaluate`, `/diagnose`, and `/transfer`.
4. `Embodied harness paper framing`: define PEARL as an embodied harness system with failure mining, harness proposals, regression gates, promotion, rollback, and memory.

## What is already present here

- `external/robotwin-text2env-demo/`: RoboTwin tabletop Text2Env base plus the locally committed unified catalog-source extension; `external_sources.lock.json` and `vendor/patches/robotwin-text2env-selection2env.patch` make it reproducible from public commit `78bef41` without a push.
- `runs/probe_static_apple_plate/`: static selection2env pass for `move the apple onto the plate`.
- `runs/probe_static_laptop_knife/`: static selection2env pass for `place the laptop to the right of the knife`.
- `runs/probe_static_vegetable_basket/`: static selection2env pass for `put the vegetable into the basket`.
- `runs/probe_static_drawer_mug_unified/`: `036_cabinet` and `039_mug` grounding plus static scene-module pass; the task remains blocked on drawer articulation/interior-placement API and verifier support.
- `runs/smoke_asset_apple_plate/`: RoboTwin/SAPIEN asset-load render smoke for apple/plate.
- `runs/smoke_asset_laptop_knife/`: RoboTwin/SAPIEN asset-load render smoke for laptop/knife.
- `runs/smoke_asset_vegetable_basket/`: RoboTwin/SAPIEN asset-load render smoke for vegetable/basket.
- `runs/smoke_basetask_apple_plate/`: RoboTwin `Base_Task` + CuRobo smoke for apple/plate.
- `runs/smoke_basetask_laptop_knife/`: RoboTwin `Base_Task` + CuRobo smoke for laptop/knife.
- `runs/smoke_basetask_vegetable_basket/`: RoboTwin `Base_Task` + CuRobo smoke for vegetable/basket.
- `runs/collect_dryrun_apple_plate/`: `/collect` dry-run manifest, camera samples, and object-state trace for apple/plate.
- `runs/collect_dryrun_laptop_knife/`: `/collect` dry-run manifest, camera samples, and object-state trace for laptop/knife.
- `runs/collect_dryrun_vegetable_basket/`: `/collect` dry-run manifest, camera samples, and object-state trace for vegetable/basket.
- `runs/official_rollout_open_laptop/`: official RoboTwin `open_laptop.play_once()` action rollout probe.
- `runs/official_rollout_place_mouse_pad/`: official RoboTwin `place_mouse_pad.play_once()` action rollout probe.
- `runs/official_rollout_place_container_plate/`: official RoboTwin `place_container_plate.play_once()` action rollout probe.
- `runs/probe_static_apple_plate_action_repair/`: action-repair apple/plate placement used for generated `play_once` smoke.
- `runs/generated_rollout_apple_plate_action_repair_pass/`: generated selection2env `play_once` action rollout probe with `check_success=true`.
- `runs/generated_collect_apple_plate_action_repair/`: three-episode generated selection2env `play_once` demonstration collection for apple/plate.
- `runs/probe_static_can_basket_action_repair/` and `runs/generated_rollout_can_basket_action_repair/`: repaired can/basket generated `play_once` action rollout probe with `check_success=true`.
- `runs/generated_collect_can_basket_action_repair/`: three-episode generated selection2env `play_once` demonstration collection for can/basket.
- `runs/act_hdf5_generated_smoke/`: ACT-compatible HDF5 adapter smoke and RoboTwin ACT dataset-loader smoke for six generated planner-trace episodes.
- `runs/act_train_smoke_generated/`: one-epoch RoboTwin ACT train smoke on generated HDF5 data, including a best checkpoint and train/validation plots.
- `runs/act_eval_smoke_generated/`: learned-ACT `/evaluate` execution for apple/plate on held-out source-collection seeds 4, 5, and 6; 3/3 episodes execute, while task success is 0/3.
- `runs/generated_collect_apple_plate_native_sync/`: RoboTwin-native synchronized apple/plate recording; all four episodes contain 162 camera/qpos frames, while three scripted episodes pass the task verifier and one is retained as failure evidence.
- `runs/act_hdf5_native_sync/`: three successful native recordings converted to ACT HDF5 with explicit `qpos[t] -> action[t+1]` alignment, repaired JPEG channel order, and a passing RoboTwin ACT loader report.
- `runs/act_action_replay_native_sync/`: fresh-reset replay of all 161 converted expert actions with exact initial qpos match and task success.
- `runs/act_train_native_sync_rgb_chunk161_1200e/`: 1200-epoch ACT training with full-episode chunking and `best_val_loss=0.015496`.
- `runs/act_eval_native_sync_rgb_chunk161_1200e_best/`: final fixed-placement head-camera ACT evaluation; seeds 4, 5, and 6 execute and pass the task verifier, 3/3.
- `artifacts/diagnosis/native_act_closed_loop_diagnosis.json`: machine-readable failure-to-fix comparison between chunk 20 (0/3) and chunk 161 (3/3), with dataset-identity and color-repair evidence.
- `artifacts/diagnosis/placement_robustness_diagnosis.json`: machine-readable varied-placement and failure-to-data audit. The initial and repaired ACT policies both execute 4/4 signature-disjoint held-out episodes but score 1/4, so promotion is explicitly rejected.
- `artifacts/openxsim_benchmarks/`: three unified official RoboTwin benchmark bundles, each with command-loop run state, events, scene/task manifests, rollout logs, dense diagnosis, success verifier, video, and next data requirement.
- `artifacts/gen_env_contract/`: unified `/gen-env` contract examples for the selection2env, video2sim-forge fallback, and material-sidecar routes.
- `artifacts/generation_fallback/`: typed `/gen-env` blocker artifacts for the current video2sim-forge and NeuMaTeX-style material-sidecar fallbacks.
- `artifacts/adapter_catalog/robotwin_discovered_catalog.json`: 123 assets discovered from the live RoboTwin object root.
- `artifacts/adapter_catalog/selection2env_catalog_sources.json`: unified gate for 123 RoboTwin assets, 8/8 AgenticSim aliases, and 9996 Articraft metadata entries.
- `artifacts/adapter_catalog/articraft10k_probe.json`: Hugging Face Articraft-10K metadata probe with `9996` searchable URDF archive entries.
- `artifacts/adapter_catalog/articraft10k_manifest.json`: searchable Articraft-10K manifest parsed from Hugging Face dataset metadata.
- `runs/articraft_archive_probe_weight_bench/`, `runs/articraft_archive_probe_microscope/`, `runs/articraft_archive_probe_monitor/`: selected Articraft archive download, URDF parse, and SAPIEN load/step smokes.
- `runs/articraft_archive_probe_washing_machine/`, `runs/articraft_archive_probe_zippo_lighter/`: selected Articraft archive probes that load/step in SAPIEN but fail the collision-geometry gate.
- `runs/policy_train_eval_entrypoint_probe/`: RoboTwin ACT entrypoint probe documenting blockers in the default process/train/eval wrappers; the generated-task adapter above is separate and executable.
- `artifacts/runtime/robotwin_5090_env.json`: RTX 5090-compatible runtime and CuRobo build evidence.
- `artifacts/visual_review/selection2env_visual_review.json`: Codex visual inspection of Base_Task observer/head images for initial-scene support.
- `artifacts/visual_review/generated_rollout_final_relation_review.json`: agent visual review plus simulator metrics for generated final rollout relations.
- `artifacts/generated_rollout_repair/generated_selection2env_action_repair_summary.json`: generated action-repair pass/failure summary.
- `runs/scene_task_decoupling/` and `artifacts/scene_task_decoupling/apple_plate_two_tasks.json`: byte-identical shared scene, two distinct task specs, two `play_once/check_success` passes, screenshots/videos, and retained repair failures.

## Artifact map

- `schemas/selection2env.schema.json`: normalized selection2env artifact contract.
- `schemas/robotwin_task_program_input.schema.json`: explicit scene ID, placement SHA, source/target binding, adapter, and verifier contract.
- `schemas/gen_env.schema.json`: unified `/gen-env` route contract covering selection2env, forge fallback, and material sidecar outputs.
- `schemas/pearl_command.schema.json`: command envelope contract for PEARL commands.
- `schemas/gen_env_fallback.schema.json`: typed `/gen-env` fallback blocker contract.
- `schemas/openxsim_benchmark_bundle.schema.json`: typed per-benchmark command-loop artifact contract.
- `docs/selection2env_schema.md`: schema explanation and Gaochen handoff API.
- `docs/openxsim_command_spec.md`: command specs, artifact paths, owner split, and failure codes.
- `docs/text2env_literature_review.md`: primary-source literature review and method matrix.
- `docs/embodied_harness_thesis.md`: one-page thesis, loop mapping, novelty table, and figure brief.
- `docs/generation2env_blockers.md`: generation2env and local simulator-smoke blockers.
- `docs/robotwin_smoke_evidence.md`: current RoboTwin install, render smoke evidence, and `robotwin-5090` CuRobo runtime evidence.
- `docs/dashboard_todo_traceability.md`: requirement-to-artifact traceability.
- `docs/todo_completion_audit.md`: current requirement-by-requirement completion audit.
- `docs/native_act_closed_loop_diagnosis.md`: synchronized ACT data, replay, training, evaluation, failure diagnosis, and claim boundary.
- `docs/placement_robustness_diagnosis.md`: deterministic placement splits, native diversity gates, two held-out evaluations, targeted recovery data, additional-task regression, and the rejected promotion decision.
- `scripts/build_selection2env_examples.py`: builds normalized artifacts from the existing static runs.
- `scripts/run_robotwin_asset_smoke.py`: runs RoboTwin/SAPIEN asset-load render smoke without initializing the planner stack.
- `scripts/run_collect_dry_run.py`: runs bounded RoboTwin `Base_Task` `/collect` dry-runs without claiming policy execution.
- `scripts/run_official_task_rollout_probe.py`: runs official RoboTwin task `play_once()` probes and records action rollout evidence.
- `scripts/run_generated_selection2env_rollout_probe.py`: runs a bounded generated selection2env `play_once` action-stack probe for action-repair placements.
- `scripts/selection2env_contract.py`: resolves task bindings and validates placement references/SHA.
- `scripts/build_scene_task_decoupling_report.py`: requires two successful task rollouts over one exact scene before scene-task decoupling passes.
- `scripts/run_generated_rollout_collection.py`: repeats generated selection2env `play_once` episodes and writes an aggregate collection report and dataset manifest.
- `scripts/convert_native_robotwin_collection_to_act_hdf5.py`: converts RoboTwin-native synchronized head-camera/qpos recordings into temporally aligned ACT HDF5 episodes and repairs the native JPEG channel order.
- `scripts/run_act_action_replay_probe.py`: replays converted expert actions from a fresh reset to test action semantics independently of learning.
- `scripts/build_native_act_diagnosis.py`: compares native data identity, channel repair, expert replay, chunk-20 failure, and chunk-161 success.
- `scripts/build_placement_robustness_diagnosis.py`: compiles the varied-placement failure-to-data iteration and enforces the robustness promotion boundary.
- `scripts/convert_generated_collection_to_act_hdf5.py`: converts generated planner traces into ACT-compatible HDF5 smoke episodes and a generated `SIM_TASK_CONFIGS` file.
- `scripts/run_act_hdf5_loader_smoke.py`: imports RoboTwin ACT dataset utilities and reads one converted HDF5 item.
- `scripts/run_act_train_smoke.py`: runs a bounded one-epoch ACT train smoke on the generated HDF5 config and records checkpoint/log evidence.
- `scripts/run_generated_act_eval_smoke.py`: loads the generated ACT checkpoint, runs bounded policy inference/actions in the generated RoboTwin task, and writes `/evaluate` state, events, traces, videos, verifier results, and dense diagnosis.
- `scripts/build_openxsim_benchmark_bundles.py`: packages three official RoboTwin rollouts into unified PEARL benchmark proof surfaces without claiming learned-policy evaluation.
- `scripts/build_articraft_manifest.py`: builds the Articraft-10K searchable metadata manifest from Hugging Face dataset metadata without downloading full archives.
- `scripts/probe_articraft_archive.py`: downloads one selected Articraft archive, parses its URDF, and can run a SAPIEN load/physics-step smoke.
- `scripts/probe_policy_train_eval_hooks.py`: probes RoboTwin ACT process/train/eval entrypoints against current generated collection artifacts and records blocking reasons.
- `scripts/build_gen_env_contract_examples.py`: writes sample `/gen-env` contract artifacts for selection2env, forge fallback, and material sidecar routes.
- `scripts/build_gen_env_fallback_blockers.py`: writes machine-readable blocker probes for video2sim-forge and NeuMaTeX-style fallback routes.
- `scripts/verify_workspace.py`: validates required docs, schemas, run summaries, and generated artifacts.

The Text2Env literature review is also copied to:

- `/home/jingxiang/Downloads/text2env_literature_review.md`
- `/Users/boris/Downloads/text2env_literature_review.md`

## Commands

Build normalized artifacts:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
python3 scripts/build_selection2env_examples.py
```

Verify:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
python3 scripts/verify_workspace.py
```

Run a `Base_Task`/CuRobo smoke:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
cd external/robotwin-text2env-demo
CUDA_HOME=/home/jingxiang/miniconda3/envs/wmfactory \
PATH=/home/jingxiang/miniconda3/envs/robotwin-5090/bin:/home/jingxiang/miniconda3/envs/wmfactory/bin:$PATH \
LD_LIBRARY_PATH=/home/jingxiang/miniconda3/envs/wmfactory/targets/x86_64-linux/lib:/home/jingxiang/miniconda3/envs/wmfactory/lib:/home/jingxiang/miniconda3/envs/robotwin-5090/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$LD_LIBRARY_PATH \
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python generate_scene/tools.py run-smoke \
  --robotwin-root /home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin \
  --placement /home/jingxiang/workspace/alchedata-self-improving-agents/runs/probe_static_apple_plate/final_placement.json \
  --out-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/smoke_basetask_apple_plate \
  --task-config demo_clean \
  --python-executable /home/jingxiang/miniconda3/envs/robotwin-5090/bin/python
```

Run a bounded `/collect` dry-run:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
CUDA_HOME=/home/jingxiang/miniconda3/envs/wmfactory \
PATH=/home/jingxiang/miniconda3/envs/robotwin-5090/bin:/home/jingxiang/miniconda3/envs/wmfactory/bin:$PATH \
LD_LIBRARY_PATH=/home/jingxiang/miniconda3/envs/wmfactory/targets/x86_64-linux/lib:/home/jingxiang/miniconda3/envs/wmfactory/lib:/home/jingxiang/miniconda3/envs/robotwin-5090/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$LD_LIBRARY_PATH \
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python scripts/run_collect_dry_run.py \
  --robotwin-root /home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin \
  --placement /home/jingxiang/workspace/alchedata-self-improving-agents/runs/probe_static_apple_plate/final_placement.json \
  --out-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/collect_dryrun_apple_plate \
  --task-id task_apple_plate \
  --task-config demo_clean
```

Run the generated selection2env action-repair probe:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
CUDA_HOME=/home/jingxiang/miniconda3/envs/wmfactory \
PATH=/home/jingxiang/miniconda3/envs/robotwin-5090/bin:/home/jingxiang/miniconda3/envs/wmfactory/bin:$PATH \
LD_LIBRARY_PATH=/home/jingxiang/miniconda3/envs/wmfactory/targets/x86_64-linux/lib:/home/jingxiang/miniconda3/envs/wmfactory/lib:/home/jingxiang/miniconda3/envs/robotwin-5090/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$LD_LIBRARY_PATH \
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python scripts/run_generated_selection2env_rollout_probe.py \
  --robotwin-root /home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin \
  --task-program-input /home/jingxiang/workspace/alchedata-self-improving-agents/artifacts/task_program_inputs/task_apple_plate.json \
  --scene-module /home/jingxiang/workspace/alchedata-self-improving-agents/external/robotwin-text2env-demo/generated_scenes/apple_plate_scene.py \
  --out-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/scene_task_decoupling/apple_on_plate \
  --task-config demo_clean \
  --motion-mode direct-topdown \
  --grasp-z-offset 0.0 \
  --place-z-offset 0.04
```

Repeat that command with `task_apple_plate_to_left_front.json` and output directory `runs/scene_task_decoupling/apple_to_left_front`, then run `scripts/build_scene_task_decoupling_report.py`. The report passes only when both task IDs, the shared scene ID, placement SHA, and both rollout verifiers agree.

Run the generated selection2env collection gate:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
CUDA_HOME=/home/jingxiang/miniconda3/envs/wmfactory \
PATH=/home/jingxiang/miniconda3/envs/robotwin-5090/bin:/home/jingxiang/miniconda3/envs/wmfactory/bin:$PATH \
LD_LIBRARY_PATH=/home/jingxiang/miniconda3/envs/wmfactory/targets/x86_64-linux/lib:/home/jingxiang/miniconda3/envs/wmfactory/lib:/home/jingxiang/miniconda3/envs/robotwin-5090/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$LD_LIBRARY_PATH \
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python scripts/run_generated_rollout_collection.py \
  --robotwin-root /home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin \
  --placement /home/jingxiang/workspace/alchedata-self-improving-agents/runs/probe_static_apple_plate_action_repair/final_placement.json \
  --task-id task_apple_plate \
  --scene-module /home/jingxiang/workspace/alchedata-self-improving-agents/external/robotwin-text2env-demo/generated_scenes/apple_plate_scene.py \
  --out-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/generated_collect_apple_plate_action_repair \
  --task-config demo_clean \
  --seed-list 0,1,3 \
  --motion-mode direct-topdown \
  --grasp-z-offset 0.0 \
  --place-z-offset 0.04
```

Run the bounded learned-ACT `/evaluate` adapter:

```bash
cd /home/jingxiang/workspace/alchedata-self-improving-agents
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python scripts/run_generated_act_eval_smoke.py \
  --robotwin-root /home/jingxiang/workspace/alchedata-self-improving-agents/external/RoboTwin \
  --placement /home/jingxiang/workspace/alchedata-self-improving-agents/runs/probe_static_apple_plate_action_repair/final_placement.json \
  --checkpoint-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/act_train_smoke_generated/act_ckpt \
  --out-dir /home/jingxiang/workspace/alchedata-self-improving-agents/runs/act_eval_smoke_generated \
  --seed 4 --seed 5 --seed 6 --max-steps 20
```

The current evidence proves selection over 123 live RoboTwin assets, eight AgenticSim aliases, and 9996 searchable Articraft metadata entries without allowing catalog-only Articraft assets to bypass import gates. Three selection2env cases pass asset render, `Base_Task`/CuRobo, and `/collect` dry-run gates. One byte-identical apple/plate scene executes two distinct generated task specs with `check_success=true`; the pulled PNG/MP4 artifacts and failed repair attempts are retained. The policy path is separate: both varied-placement ACT policies score only 1/4 on held-out splits, so promotion remains rejected. The workspace does not prove domain-randomized or cross-task learned-policy robustness, broad Articraft scale/material/task suitability, forge/material execution, or drawer articulation task support.
