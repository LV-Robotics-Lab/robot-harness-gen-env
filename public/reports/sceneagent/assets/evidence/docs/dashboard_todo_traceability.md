# Dashboard TODO Traceability

## SceneAgent / selection2env

| acceptance item | current artifact | status |
|---|---|---|
| input/output schema | `schemas/selection2env.schema.json`, `schemas/robotwin_task_program_input.schema.json`, `docs/selection2env_schema.md` | covered with explicit task bindings and placement SHA |
| RoboTwin asset catalog connection | `artifacts/adapter_catalog/robotwin_discovered_catalog.json`, `artifacts/adapter_catalog/selection2env_catalog_sources.json` | covered for 123 live discovered assets |
| AgenticSim asset-list connection | `external/robotwin-text2env-demo/asset_catalogs/agenticsim_placement_assets.json`, source commit `f34d56a` | covered for 8/8 aliases resolved to live RoboTwin backend assets |
| Articraft-10K searchable manifest and sample archive smoke | `artifacts/adapter_catalog/articraft10k_probe.json`, `artifacts/adapter_catalog/articraft10k_manifest.json`, `artifacts/adapter_catalog/articraft10k_search_examples.json`, `runs/articraft_archive_probe_*/probe_report.json` | covered as Hugging Face metadata catalog with 9996 searchable URDF archive entries, three selected archive SAPIEN load/step smokes, and two missing-collision blockers; broad scale/material/task-suitability validation not claimed |
| at least 3 task samples | apple/plate, laptop/knife, vegetable/basket, drawer/mug blocker | covered |
| at least 2 task samples generate Text2Env JSON/task input | `artifacts/task_program_inputs/*.json` | covered; all references and placement SHA values pass schema verification |
| simulator smoke/data collection dry run | `runs/smoke_asset_*`, `runs/smoke_basetask_*`, `runs/collect_dryrun_*`, `runs/official_rollout_*`, `runs/generated_rollout_*`, `runs/generated_collect_*`, `runs/generated_collect_apple_plate_native_sync`, `runs/act_hdf5_native_sync`, final ACT train/eval, `docs/robotwin_smoke_evidence.md` | Base_Task/CuRobo, `/collect`, official/generated scripted action, native synchronized data, ACT train, expert replay, and fixed-placement learned `/evaluate` covered; final task success is 3/3 |
| unsupported/blocker sample | `runs/probe_static_drawer_mug_unified`, `artifacts/selection2env/task_drawer_mug_blocker.json` | covered: `036_cabinet` and `039_mug` ground and static scene generation passes; executable articulation/interior verifier remains blocked |
| VLM/agent critique beyond collision | `docs/selection2env_schema.md`, `artifacts/visual_review/selection2env_visual_review.json`, `artifacts/visual_review/generated_rollout_final_relation_review.json` | covered for initial-scene support and generated final-relation support on apple/plate plus can/basket |
| accepted/rejected candidates | `artifacts/selection2env/*.json`, `artifacts/adapter_catalog/selection2env_queries/*.json` | covered across all three catalog sources with execution gates |
| generated code diff/commit | `external_sources.lock.json`, `vendor/patches/robotwin-text2env-selection2env.patch`, upstream local `029911d`, 5090 `e3e3c76` | covered with public base commit, patch SHA, and identical result tree |
| generation2env blocker memo | `docs/generation2env_blockers.md`, `artifacts/generation_fallback/fallback_blocker_summary.json` | covered with typed fallback blocker artifacts |
| Gaochen handoff API | `docs/selection2env_schema.md`, `docs/openxsim_command_spec.md` | covered |
| scene-task decoupling | `artifacts/scene_task_decoupling/apple_plate_two_tasks.json`, two task-program inputs, two pass rollout directories | covered with distinct bindings, shared scene/path/SHA, byte-identical initial images, and two `check_success=true` executions |

## Text2Env literature review

| acceptance item | artifact | status |
|---|---|---|
| primary sources | `docs/text2env_literature_review.md` | covered |
| method matrix | `docs/text2env_literature_review.md` | covered |
| taxonomy | `docs/text2env_literature_review.md` | covered |
| P0/P1/P2 shortlist | `docs/text2env_literature_review.md` | covered |
| handoff fields | `docs/selection2env_schema.md` and literature review | covered |
| ASPIRE/ENPIRE differentiation | `docs/text2env_literature_review.md`, `docs/embodied_harness_thesis.md` | covered |
| dashboard comment path | `/home/jingxiang/Downloads/text2env_literature_review.md` and `/Users/boris/Downloads/text2env_literature_review.md` | covered in hosted dashboard state and local mirror |

## PEARL Open X Sim command loop

| acceptance item | artifact | status |
|---|---|---|
| command specs | `docs/openxsim_command_spec.md`, `schemas/pearl_command.schema.json` | covered |
| `/gen-env` schema paths | `schemas/gen_env.schema.json`, `schemas/selection2env.schema.json`, `schemas/gen_env_fallback.schema.json`, command spec, `artifacts/gen_env_contract/*.json` | covered for selection2env, forge fallback blocker, and material-sidecar blocker routes |
| 2-3 RoboTwin benchmarks | `artifacts/openxsim_benchmarks/manifest.json` and the three task bundles, backed by `runs/official_rollout_*`; generated and ACT evidence remain separate | covered with one unified command-loop proof surface per `open_laptop`, `place_mouse_pad`, and `place_container_plate`; these are scripted expert bundles, while learned `/evaluate` separately passes 3/3 on fixed apple/plate |
| `/train` and `/evaluate` hook probe | native ACT HDF5 loader, replay, fixed-placement train/eval, `artifacts/diagnosis/native_act_closed_loop_diagnosis.json`, and `artifacts/diagnosis/placement_robustness_diagnosis.json` | generated-task train and learned eval execute; fixed placement passes 3/3, while two varied-placement held-out splits execute 4/4 and each score 1/4 after a targeted failure-to-data retrain; promotion remains rejected |
| run_state/events | `artifacts/openxsim_benchmarks/*/{run_state.json,events.jsonl,scene_manifest.json,task_manifest.json,failure_diagnosis.json}`, plus learned ACT run state/events | covered per each of the three official benchmarks and through learned apple/plate `/evaluate` execution |
| video2sim/NeuMaTeX blocker | `docs/generation2env_blockers.md`, `artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json`, `artifacts/generation_fallback/blocked_neumatex_material_sidecar.json` | covered as typed blockers, not dry runs |
| dense diagnosis categories | official/generated rollout reports, final ACT per-episode diagnoses, fixed-scene and placement-robustness machine-readable diagnoses, exact verifier checks | covered for the six required categories, target-relation verdict, channel-order bug, trajectory identity, expert replay, chunk-horizon control, targeted recovery, and promotion rejection |
| Open X Sim adapter matrix | command spec | covered |
| owner split | command spec | covered |

## Embodied harness paper framing

| acceptance item | artifact | status |
|---|---|---|
| one-page thesis | `docs/embodied_harness_thesis.md` | covered |
| figure-loop mapping | `docs/embodied_harness_thesis.md` | covered |
| embodied harness surfaces | `docs/embodied_harness_thesis.md` | covered |
| novelty table | `docs/embodied_harness_thesis.md` | covered |
| figure caption | `docs/embodied_harness_thesis.md` | covered |
| route implementation requirements into commands | `docs/embodied_harness_thesis.md`, command spec | covered |
