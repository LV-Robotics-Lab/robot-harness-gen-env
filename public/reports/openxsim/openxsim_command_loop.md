# PEARL Open X Sim Command Spec

## Command Envelope

Every PEARL command writes an envelope matching `schemas/pearl_command.schema.json`. The `/gen-env` route-level output contract is `schemas/gen_env.schema.json`; it covers the P0 `selection2env` branch, the video/RGB-D forge fallback branch, and the material-sidecar branch.

The complete six-command machine-readable registry is `artifacts/openxsim/openxsim_command_registry.json`. The strict adapter matrix is `artifacts/openxsim/openxsim_adapter_matrix.json`, and the eight-item acceptance mapping is `artifacts/openxsim/openxsim_acceptance_audit.json`.

## AgenticSim Isaac Intake Evidence

[COMPUTED] [CONFIDENCE: HIGH] The pinned AgenticSim snapshot at commit `6d952560870b0a9b71f707f0476d28425bfab256` normalizes 745 repositories from the two Awesome Isaac lists and AgenticSim submodules. GitHub metadata detects recognized open-source licenses for 308 repositories; 84 expose documented current-Isaac environment source and 64 pass the static environment-candidate gate.

[KNOWN] [CONFIDENCE: HIGH] Static candidacy is not execution proof. The RTX 5090 baseline passes Isaac Sim 5.1 physics, RTX render, CUDA compute, and a 32-frame video with 32 unique frame hashes and 31 pose-movement transitions. [COMPUTED] [CONFIDENCE: HIGH] A separate normalized `place_container_plate` contract now executes `/gen-env`, `/collect`, `/evaluate`, `/diagnose`, and `/transfer` in Isaac with a 120-step trace, 24/24 unique video frames, and a passing target relation verifier.

[COMPUTED] [CONFIDENCE: HIGH] Twelve exact candidate commits were run. Eleven pass their named bounded reset/step/render probe and are admitted for local noncommercial academic use; WobbleGo fails before reset because its required core USD is unavailable. Six technical passes also close the tested code-and-required-asset open-source boundary. The other five passes retain provenance advisories but are not blocked by the academic-use intake policy. Third-party asset redistribution is not included.

| candidate | exact bounded task | runtime | academic use | provenance |
|---|---|---:|---:|---|
| `enactic/openarm_isaac_lab` | `Isaac-Reach-OpenArm-Play-v0` | 20/20 pass | accepted | strict OSS closure |
| `neuromeka-robotics/nrmk_isaaclab_public` | `Indy-Deploy` | 20/20 pass | accepted | strict OSS closure |
| `noxrick91/WobbleGo` | `WobbleGo-Direct-v0` | 0/20 blocked | blocked | core USD unavailable |
| `fan-ziqi/robot_lab` | `RobotLab-Isaac-Velocity-Flat-Unitree-Go2-v0` | 20/20 pass | accepted | strict OSS closure |
| `unitreerobotics/unitree_rl_lab` | `Unitree-Go2-Velocity` | 20/20 pass | accepted | asset-license advisory |
| `liorbenhorin/lerobot_so101_teleop` | `Lerobot-So101-Teleop-Base` | 5/5 pass | accepted | asset-license advisory |
| `lehome-official/lehome-challenge` | `LeHome-BiSO101-Direct-Garment-v2` | 5/5 pass after recorded patch | accepted | strict OSS closure |
| `iit-DLSLab/basic-locomotion-dls-isaaclab` | `Locomotion-Go2-Flat` | 10/10 pass after recorded patch | accepted | strict OSS closure |
| `iit-DLSLab/simple-joints-identification-isaaclab` | `IsaacLab-Pace-Go2` | 10/10 pass | accepted | strict OSS closure |
| `AccelerationConsortium/Matterix` | `Matterix-Test-Beakers-Franka-v1` | 10/10 pass | accepted | asset-license advisory |
| `abmoRobotics/RLRoverLab` | `Exomy-v0` | 5/5 pass | accepted | asset-license advisory |
| `Rui-li023/LabUtopia` | `level1_pick` | 10/10 pass in bounded harness | accepted | noncommercial asset terms |

[KNOWN] [CONFIDENCE: HIGH] The normative intake evidence is `docs/awesome_isaac_runtime_evidence.json`; the executed command bundle is `runs/isaac_openxsim_place_container_plate_v1/`. [KNOWN] [CONFIDENCE: HIGH] The bundle transfers task semantics through primitive proxies and scripted object-space actions; it does not establish robot-policy transfer, source-asset identity, material parity, or learned-policy quality.

Common fields:

- `run_id`: stable run identifier.
- `owner`: person or component responsible for the command.
- `inputs`: typed command inputs.
- `outputs`: expected artifacts and status surfaces.
- `artifact_root`: directory containing `run_state.json`, `events.jsonl`, and command outputs.
- `failure_codes`: explicit machine-readable blockers.

## /gen-env

Owner: Zheng Ye for `selection2env` and `/transfer` integration; Boris for architecture alignment.

Inputs:

- `task_text`;
- optional reference image, RGB-D capture, or video;
- asset catalog roots;
- allowed simulator adapters;
- robot embodiment and workspace constraints.

Outputs:

- `selection2env` artifact;
- unified `/gen-env` route artifact matching `schemas/gen_env.schema.json`;
- scene/task manifest;
- RoboTwin task-program input;
- accepted/rejected asset list;
- validation plan;
- blocker memo for missing assets or unsupported simulator imports;
- typed fallback blocker probes under `artifacts/generation_fallback/*` when video2sim-forge or material-sidecar routes cannot run.

Failure codes:

- `ASSET_CATALOG_MISS`
- `ROBOTWIN_ROOT_MISSING`
- `SIM_IMPORT_FAIL`
- `VISUAL_REVIEW_REQUIRED`
- `GENERATION2ENV_DEFERRED`
- `FORGE_CAPTURE_SOURCE_MISSING`
- `MATERIAL_MULTIVIEW_INPUT_MISSING`
- `FORGE_IMPORT_ARTIFACT_MISSING`
- `MATERIAL_RENDER_BINDING_MISSING`

Artifact roots: `artifacts/gen_env_contract/`, `artifacts/selection2env/`, and `artifacts/generation_fallback/`.

## /collect

Owner: Zheng Ye for scene/task execution; Gaochen for data interface compatibility.

Inputs:

- accepted scene/task manifest;
- policy or scripted expert configuration;
- simulator adapter;
- rollout count and seed plan.

Outputs:

- rollout logs;
- object state traces;
- action traces;
- camera previews;
- data manifest;
- failure diagnosis seed records.

Failure codes:

- `SCENE_LOAD_FAIL`
- `RESET_FAIL`
- `ROLLOUT_CRASH`
- `VERIFIER_TIMEOUT`
- `MISSING_CAMERA_TRACE`

Artifact roots: `runs/collect_dryrun_*/`, `runs/official_rollout_*/`, and `runs/generated_collect_*/`.

Current evidence boundary:

- `runs/collect_dryrun_*` proves observation/object-state artifact writing for three generated placements without policy execution.
- `runs/official_rollout_*` proves official RoboTwin `play_once()` tasks can execute through the action/planner stack. Its observer evidence is continuously sampled through RoboTwin's native step/save hook: `open_laptop` has 640 frames / 53.33 s, `place_mouse_pad` has 531 frames / 44.25 s, and `place_container_plate` has 527 frames / 43.92 s; endpoint-only videos fail validation.
- `artifacts/openxsim/openxsim_video_frame_uniqueness.json` records a final-MP4 decoded-frame audit: the Isaac baseline is 32/32 unique, `open_laptop` is 640/640, `place_mouse_pad` is 531/531, and `place_container_plate` is 527/527. This rules out endpoint-only two-frame repetition without claiming policy quality from frame uniqueness alone.
- `artifacts/openxsim_benchmarks/manifest.json` indexes three typed benchmark bundles for `open_laptop`, `place_mouse_pad`, and `place_container_plate`; every bundle links `run_state.json`, `events.jsonl`, scene/task manifests, rollout/move logs, dense diagnosis, observer video, success verifier, and next data requirement.
- `runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json` proves one generated selection2env action-repair `play_once()` pass.
- `runs/generated_rollout_can_basket_action_repair/rollout_report.json` proves a repaired generated `place_in` can/basket `play_once()` pass.
- `runs/generated_collect_apple_plate_action_repair/collection_report.json` and `runs/generated_collect_can_basket_action_repair/collection_report.json` prove three-episode generated demonstration collections for apple/plate and can/basket.
- `artifacts/scene_task_decoupling/apple_plate_two_tasks.json` proves two distinct task bindings execute with `check_success=true` from the same scene ID and placement SHA; the initial head/observer frames are byte-identical.
- `runs/act_hdf5_generated_smoke/conversion_report.json` and `runs/act_hdf5_generated_smoke/load_data_report.json` prove a six-episode ACT HDF5 adapter/loader smoke for generated planner traces.
- `runs/act_train_smoke_generated/train_smoke_report.json` proves one bounded ACT train smoke on the generated HDF5 config and writes `policy_best.ckpt`.
- `runs/act_eval_smoke_generated/evaluate_report.json` proves the generated-task adapter loads `policy_best.ckpt`, executes 20 learned-policy actions for each of held-out source-collection seeds 4, 5, and 6, and records 3/3 infrastructure completion with 0/3 task success.
- `runs/generated_collect_apple_plate_native_sync/collection_report.json`, `runs/act_hdf5_native_sync/`, and `runs/act_action_replay_native_sync/` prove native synchronized recording, temporally aligned ACT loading, repaired head-camera color order, and successful replay of all 161 converted expert actions.
- `runs/act_train_native_sync_rgb_chunk161_1200e/` and `runs/act_eval_native_sync_rgb_chunk161_1200e_best/` prove a 1200-epoch full-episode ACT policy reaches 3/3 task success on the fixed apple/plate placement. `artifacts/diagnosis/native_act_closed_loop_diagnosis.json` contrasts this with the matched chunk-20 0/3 result.
- `runs/policy_train_eval_entrypoint_probe/probe_report.json` separately records blockers in RoboTwin's default ACT wrappers: `process_data.sh` targets the old source-data path, the default train task name is absent from `SIM_TASK_CONFIGS`, and upstream eval has no `envs.task_apple_plate` module.
- `artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json` and `artifacts/generation_fallback/blocked_neumatex_material_sidecar.json` record the current `/gen-env` fallback blockers for video2sim-forge and NeuMaTeX-style material sidecars.
- `artifacts/gen_env_contract/selection2env_route_sample.json`, `artifacts/gen_env_contract/forge_fallback_route_blocker.json`, and `artifacts/gen_env_contract/material_sidecar_route_blocker.json` validate the unified `/gen-env` schema over the three route families.
- Generated-task learned-policy `/evaluate` execution passes the fixed-scene verifier. Non-identical demonstrations and held-out placement evaluations have since been executed, but both varied-placement policies score 1/4; promotion remains blocked on placement quality, declared randomization, and cross-task learned evaluation. Default upstream wrappers remain a separate compatibility blocker.

The three unified benchmark bundles use official scripted `play_once()` execution. Their `/evaluate` stage is the official task `check_success()` verifier with `learned_policy=false`; learned-policy evidence remains the separate apple/plate ACT report.

## /train

Owner: Gaochen.

Inputs:

- rollout dataset manifest;
- training recipe;
- policy base checkpoint;
- data requirement filters.

Outputs:

- checkpoint;
- training log;
- data coverage report;
- failure-to-data linkage report.

Failure codes:

- `DATA_MANIFEST_INVALID`
- `TRAIN_DATA_FORMAT_BLOCKED`
- `POLICY_DEPENDENCY_MISSING`
- `GPU_OOM`
- `CHECKPOINT_WRITE_FAIL`
- `POLICY_INTERFACE_MISMATCH`

Artifact roots: `runs/act_hdf5_native_sync/` and `runs/act_train_native_sync_rgb_chunk161_1200e/`.

## /evaluate

Owner: Gaochen.

Inputs:

- checkpoint;
- eval task set;
- verifier definitions;
- simulator or real-robot execution mode.

Outputs:

- eval results;
- per-task traces;
- success/failure labels;
- regression report.

Current bounded implementation: `scripts/run_generated_act_eval_smoke.py` writes `runs/act_eval_native_sync_rgb_chunk161_1200e_best/evaluate_report.json`, `run_state.json`, aggregate/per-episode `events.jsonl`, action traces, observer videos, images, success-verifier results, and dense diagnosis. Seeds 4, 5, and 6 all pass, but fixed placement and disabled domain randomization mean this is repeated execution, not scene-distribution generalization.

Failure codes:

- `EVAL_TASK_LOAD_FAIL`
- `EVAL_TASK_ENV_MISSING`
- `POLICY_LOAD_FAIL`
- `VERIFIER_CONTRADICTION`
- `REGRESSION_DETECTED`

Artifact roots: `runs/act_eval_native_sync_rgb_chunk161_1200e_best/` and `artifacts/diagnosis/placement_robustness_diagnosis.json`.

## /diagnose

Owner: Boris for diagnostic schema; Gaochen/Zheng Ye for concrete traces.

Inputs:

- rollout traces;
- simulator state;
- object poses;
- arm motion;
- gripper state;
- visual observations.

Outputs:

- failure cluster;
- root-cause hypothesis;
- next data requirement;
- proposed harness edit.

Failure codes:

- `DIAGNOSIS_INPUT_MISSING`
- `STATE_TRACE_INCOMPLETE`
- `FAILURE_CLUSTER_UNRESOLVED`
- `VISUAL_MATERIAL_REVIEW_MISSING`

Artifact roots: `artifacts/openxsim_benchmarks/*/failure_diagnosis.json`, `artifacts/diagnosis/native_act_closed_loop_diagnosis.json`, and `artifacts/diagnosis/placement_robustness_diagnosis.json`.

Dense failure categories:

- wrong grasp location;
- object knocked over;
- arm jitter;
- uncontrolled gripper open/close;
- after-contact failure;
- visual-material mismatch;
- object occlusion;
- unsupported asset or state transition.

## /transfer

Owner: Zheng Ye.

Inputs:

- external simulator/environment source;
- adapter metadata;
- asset format and material system;
- reset/step/verifier API descriptions.

Outputs:

- adapter manifest;
- migration blocker list;
- smoke command;
- license/access note;
- verifier mapping.

Failure codes:

- `ADAPTER_SOURCE_UNAVAILABLE`
- `LICENSE_ACCESS_UNRESOLVED`
- `ASSET_FORMAT_UNSUPPORTED`
- `RESET_STEP_API_UNMAPPED`
- `VERIFIER_API_UNMAPPED`
- `MATERIAL_BINDING_UNSUPPORTED`
- `TRANSFER_SMOKE_FAIL`

Artifact roots: `artifacts/openxsim/openxsim_adapter_matrix.json` and `artifacts/openxsim/agenticsim_awesome_isaac_snapshot.json`.

Initial adapter matrix:

| source | engine/renderer | asset format | material system | reset/step/verifier | license/access | migration difficulty | status |
|---|---|---|---|---|---|---|---|
| RoboTwin/SAPIEN | SAPIEN physics/rendering | object dirs, GLB/mesh, model metadata, task modules | SAPIEN materials and textures | `Base_Task` setup/action plus `check_success` | public RoboTwin MIT repo; preserve asset provenance | generated-task registration and robust policy coverage | P0 executed |
| AgenticSim Open X Sim intake | Isaac Sim 5.1 / Isaac Lab 2.3 plus RoboTwin alias source | 745-repository catalog, USD assets, 8 RoboTwin aliases | USD/MDL plus inherited SAPIEN materials | 12 exact-commit probes; 11 bounded reset/step/render passes; one normalized task has five-command execution and target verifier | local noncommercial academic policy admits all 11 passes; 6 strict OSS closures and 5 provenance advisories; 1 runtime blocker | expand robot embodiment, source assets/materials, observations, and task-verifier coverage; retain licensing as advisory metadata | P1 runtime intake plus one completed task-semantic bundle |
| Articraft-10K | generated articulated assets probed in SAPIEN | searchable metadata plus `.tar.gz` URDF archives | archive visual meshes/textures where present | SAPIEN load/20-step smoke; no task verifier adapter | public Hugging Face access; usage terms require promotion audit | scale, collision, articulation, material, task suitability | P1 bounded smoke |
| Isaac Sim/USD | PhysX + Omniverse RTX | USD | USD/MDL material bindings | RTX baseline plus 11 named task smokes pass; one normalized place_on bundle executes scene generation, collection, evaluation, diagnosis, transfer, and a target verifier | NVIDIA 5.1 access terms plus separate per-candidate code and required-asset audit | add robot embodiment, source assets/materials, joint-action policy, and more task verifiers | P1 executed normalized task bundle |
| MuJoCo | MuJoCo native renderer | MJCF/XML | MJCF material/texture/RGBA | core reset/step exists; PEARL verifier not mapped | public open-source repo; bundle licenses still required | articulation, contacts, cameras, verifier semantics | P2 target, not run |
| LIBERO | MuJoCo through robosuite-style envs | BDDL, MJCF, meshes, textures | MuJoCo/robosuite materials | benchmark reset/step/success need adapter | public repo; benchmark assets/dependencies require audit | BDDL semantics, observations, predicates | P2 target, not run |
| robosuite | MuJoCo wrappers | MJCF, meshes, textures, arena/object classes | MuJoCo materials/textures | reset/step exists; success predicates need manifests | public repo; asset/dependency licenses require manifest | controllers, cameras, registries, predicates | P2 target, not run |
| BEHAVIOR/OmniGibson | Omniverse/PhysX | USD-backed scene/object assets | USD/Omniverse materials | activity reset/step/predicates need adapter | public project/dataset; usage terms require audit | large-scene state, predicates, embodiment, verifier mapping | P2 target, not run |

The JSON matrix is normative because it preserves complete evidence paths and claim boundaries. RoboTwin/SAPIEN remains the only backend with robot-action benchmark execution. Isaac Sim now has one completed normalized task-semantic `/gen-env`, `/collect`, `/evaluate`, `/diagnose`, and `/transfer` bundle plus 11 bounded task smokes; the bundle explicitly does not transfer robot embodiment, joint-action policy, source assets, or materials. The remaining rows are bounded catalog sources or unexecuted targets.
