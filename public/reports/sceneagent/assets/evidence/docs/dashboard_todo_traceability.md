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
| simulator smoke/data collection dry run | `runs/smoke_asset_*`, `runs/smoke_basetask_*`, `runs/collect_dryrun_*`, `runs/final_acceptance_20260715/collect_*`, generated/official rollouts, native synchronized data, and policy evidence | Base_Task/CuRobo and `/collect` are covered for three supported tasks; two fresh acceptance `/collect` videos contain 97 consecutive simulator-step frames each |
| unsupported/blocker sample | `runs/probe_static_drawer_mug_unified`, `artifacts/selection2env/task_drawer_mug_blocker.json` | covered: `036_cabinet` and `039_mug` ground and static scene generation passes; executable articulation/interior verifier remains blocked |
| VLM/agent critique beyond collision | `docs/selection2env_schema.md`, `artifacts/visual_review/selection2env_visual_review.json`, `artifacts/visual_review/generated_rollout_final_relation_review.json` | covered for collision/penetration, visual plausibility, task intent, occlusion, reachability, and support/stability across three initial scenes, with generated final-relation review kept separate |
| accepted/rejected candidates | `artifacts/selection2env/*.json`, `artifacts/adapter_catalog/selection2env_queries/*.json` | covered across all three catalog sources with execution gates |
| generated code diff/commit | `external_sources.lock.json`, `vendor/patches/robotwin-text2env-selection2env.patch`, upstream local `029911d`, 5090 `e3e3c76` | covered with public base commit, patch SHA, and identical result tree |
| generation2env blocker memo | `docs/generation2env_blockers.md`, `artifacts/generation_fallback/fallback_blocker_summary.json` | covered with typed fallback blocker artifacts |
| Gaochen handoff API | `docs/selection2env_schema.md`, `docs/openxsim_command_spec.md` | covered |
| scene-task decoupling | `artifacts/scene_task_decoupling/apple_plate_two_tasks.json`, two task-program inputs, `runs/final_acceptance_20260715/scene_task_decoupling/*` | covered with distinct bindings, shared scene/path/SHA, byte-identical initial images, two `check_success=true` executions, and 589/585-frame continuous videos |
| strict eight-item audit | `artifacts/sceneagent_selection2env_acceptance_audit.json`, `scripts/build_sceneagent_acceptance_audit.py` | covered 8/8 with evidence hashes and explicit claim boundaries |

## Text2Env literature review

| acceptance item | artifact | status |
|---|---|---|
| HTML + Markdown bundle | `reports/text2env_literature_review/index.html`, `reports/text2env_literature_review/text2env_literature_review.md`, `report_manifest.json` | covered as a self-contained static bundle |
| primary sources | `artifacts/literature_review/text2env_primary_sources.json`, `docs/text2env_literature_review.md` | covered for 14 rows, including 11 academic primary sources, with primary links, I/O, assets, openness, reproducibility, and RoboTwin/AgenticSim relation |
| bundled source snapshots | `reports/text2env_literature_review/assets/source_pages/*.png`, capture manifest, contact sheet | covered for 10 official primary-source pages; all capture responses are HTTP 200 |
| method matrix | `artifacts/literature_review/text2env_method_matrix.json` | covered for 14 methods by all 8 required capabilities |
| taxonomy | `artifacts/literature_review/text2env_acceptance_audit.json#taxonomy` | covered with explicit `selection2env`, `generation2env`, repair, collection, and evaluation boundaries |
| P0/P1/P2 shortlist | `artifacts/literature_review/text2env_acceptance_audit.json#shortlist` | covered with adopted, next, and deferred status separated from paper claims |
| handoff fields | `artifacts/literature_review/text2env_acceptance_audit.json#handoff`, `docs/selection2env_schema.md` | covered for Zheng Ye producer artifacts, Gaochen command inputs, and open blockers |
| ASPIRE/ENPIRE differentiation | `artifacts/literature_review/text2env_acceptance_audit.json#innovation_after_aspire_enpire` | covered as a falsifiable hypothesis tied to schemas, gates, and not-run experiments; novelty is not claimed as proven |
| report QA | `reports/text2env_literature_review/assets/browser_qa.txt`, `reports/text2env_literature_review/qa/*.png` | covered at 1440x1000 and 390x844 with 10 images, 49 local references, and zero console/page errors |
| Downloads report paths | `/home/jingxiang/Downloads/text2env_literature_review_20260713/` and `/Users/boris/Downloads/text2env_literature_review_20260713/` | covered for 26-file bundles with matching manifest SHA-256 `2e574d6ac29f14e22004b66cf2ac1c7d676e4ff5754042ae5640bd43cb0cacf0`; hosted Dashboard comment is verified separately |

## PEARL Open X Sim command loop

| acceptance item | artifact | status |
|---|---|---|
| command specs | `artifacts/openxsim/openxsim_command_registry.json`, `docs/openxsim_command_spec.md`, `schemas/pearl_command.schema.json` | covered for all 6 commands with inputs, outputs, artifact roots, owners, and non-empty failure-code sets |
| `/gen-env` schema paths | `schemas/gen_env.schema.json`, `schemas/selection2env.schema.json`, `schemas/gen_env_fallback.schema.json`, command spec, `artifacts/gen_env_contract/*.json` | covered for selection2env, forge fallback blocker, and material-sidecar blocker routes |
| 2-3 RoboTwin benchmarks | `artifacts/openxsim_benchmarks/manifest.json` and the three task bundles, backed by `runs/official_rollout_*`; generated and ACT evidence remain separate | covered with one unified command-loop proof surface per `open_laptop`, `place_mouse_pad`, and `place_container_plate`; observer videos contain 640/531/527 continuous step-sampled frames rather than endpoint-only clips; these are scripted expert bundles, while learned `/evaluate` separately passes 3/3 on fixed apple/plate |
| `/train` and `/evaluate` hook probe | native ACT HDF5 loader/replay/eval, retained ACT diagnosis, and `artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json` | default ACT recovery remains 1/4 and unpromoted; a separate privileged pose-conditioned open-loop baseline passes bounded gates at 4/4 held-out, 4/4 declared domain randomization, and 3/3 fixed-placement can/basket |
| run_state/events | `artifacts/openxsim_benchmarks/*/{run_state.json,events.jsonl,scene_manifest.json,task_manifest.json,failure_diagnosis.json}`, plus learned ACT run state/events | covered per each of the three official benchmarks and through learned apple/plate `/evaluate` execution |
| video2sim/NeuMaTeX blocker | `artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json`, `artifacts/generation_fallback/blocked_neumatex_material_sidecar.json` | covered as typed blockers with machine-readable import, scale, collision, support, material, render, and verifier gate verdicts; no fallback execution is claimed |
| dense diagnosis categories | official/generated rollout reports, final ACT per-episode diagnoses, fixed-scene and placement-robustness machine-readable diagnoses, exact verifier checks | covered for the six required categories, target-relation verdict, channel-order bug, trajectory identity, expert replay, chunk-horizon control, targeted recovery, and promotion rejection |
| Open X Sim adapter matrix | `artifacts/openxsim/openxsim_adapter_matrix.json` | covered for 8 rows and all required engine/renderer, format, material, API, license/access, migration, and current-status fields; only RoboTwin/SAPIEN is an executed backend |
| owner split | `artifacts/openxsim/openxsim_command_registry.json#owner_split` | covered for Zheng Ye, Gaochen, and Boris |
| acceptance audit | `artifacts/openxsim/openxsim_acceptance_audit.json`, `scripts/openxsim_command_loop.py` | covered 8/8 with strict package validation |
| HTML report and bundled media | `reports/openxsim_command_loop/` | covered with 6 actual benchmark frames, 3 observer videos, 2 official reference-page screenshots, raw JSON, Markdown, manifest, and desktop/mobile QA |
| Downloads report paths | `/Users/boris/Downloads/openxsim_command_loop_20260713/`, `/home/jingxiang/Downloads/openxsim_command_loop_20260713/` | covered for matching 35-file bundles with manifest SHA-256 `1d225a552fdf3853a466f326da3cedde990a68920015d612d839ad3b6caab6e6`; hosted Dashboard comment is verified separately |

## Embodied harness paper framing

| acceptance item | artifact | status |
|---|---|---|
| one-page thesis | `docs/embodied_harness_thesis.md#one-page-thesis`, `artifacts/embodied_harness/embodied_harness_spec.json#paper_claim` | covered with a scoped working claim, fixed-checkpoint attribution control, and `first real embodied harness system` explicitly marked not established |
| figure-loop mapping | `artifacts/embodied_harness/embodied_harness_spec.json#loop_steps` | covered for all 9 ordered `h_t` execution, weakness, proposal, regression, promotion, and `h_{t+1}` steps with inputs, outputs, gates, and evidence |
| embodied harness surfaces | `artifacts/embodied_harness/embodied_harness_spec.json#embodied_surfaces` | covered for 11 reset, observation, action, tool, adapter, safety, trace, validation, data, memory, and rollback surfaces |
| novelty table | `artifacts/embodied_harness/embodied_harness_spec.json#novelty_table` | covered against all 5 required comparators with overlap, hypothesis, implemented evidence, and missing evidence separated |
| figure caption and brief | `artifacts/embodied_harness/embodied_harness_spec.json#figure`, `reports/embodied_harness/assets/embodied_harness_loop.png` | covered with bundled original diagram and explicit policy-retraining separation |
| route implementation requirements into commands | `artifacts/embodied_harness/embodied_harness_spec.json#command_routing`, `artifacts/openxsim/openxsim_command_registry.json` | covered for `/gen-env`, `/collect`, `/evaluate`, `/diagnose`, and `/transfer` fields, artifacts, and promotion gates |
| acceptance audit | `artifacts/embodied_harness/embodied_harness_acceptance_audit.json`, `scripts/embodied_harness.py` | covered 6/6; priority status is machine-checked as `not_established` |
| HTML report and bundled images | `reports/embodied_harness/` | covered with original harness diagram, ASPIRE/ENPIRE/RoboTwin source snapshots, Open X Sim execution/blocker screenshots, Markdown, JSON, desktop/mobile QA, and manifest |
| Downloads report paths | `/Users/boris/Downloads/embodied_harness_20260713/`, `/home/jingxiang/Downloads/embodied_harness_20260713/` | covered for matching 19-file bundles with manifest SHA-256 `a48999e15f9a673c48befe6f7095f5427e67dce0c0fb999015c41e9b5d9e2fa0`; hosted Dashboard comment is verified separately |
