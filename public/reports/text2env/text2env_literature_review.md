# Text2Env / SceneAgent Literature Review for PEARL

Reviewed: 2026-07-14

## Executive Verdict

[COMPUTED] [CONFIDENCE: HIGH] The review package contains 20 audited source rows, including 17 academic primary sources, a 20-by-8 method matrix, a strict selection2env/generation2env taxonomy, an implementation shortlist, a typed Zheng Ye-to-Gaochen handoff, and a post-ASPIRE/ENPIRE experiment plan.

[INFERRED] [CONFIDENCE: HIGH] PEARL should not claim novelty from generic self-improvement, code repair, task generation, real-robot rollout loops, or scene generation in isolation. ASPIRE, ENPIRE, RoboGen, FATE, SceneSmith, and RoboTwin already cover substantial portions of those claims.

[INFERRED] [CONFIDENCE: HIGH] The defensible system hypothesis is narrower and testable: keep scene, task, simulator adapter, traces, memory, data requirements, and promotion gates as separately versioned harness surfaces. This is not yet established publication priority; cross-simulator execution, memory ablation, matched ASPIRE-style repair comparison, and successful failure-to-data promotion remain missing.

Machine-readable evidence:

- `artifacts/literature_review/text2env_primary_sources.json`
- `artifacts/literature_review/text2env_method_matrix.json`
- `artifacts/literature_review/text2env_acceptance_audit.json`
- `artifacts/literature_review/candidate_project_intake_paul_20260714.json`
- `schemas/text2env_literature_review.schema.json`
- `scripts/text2env_literature_review.py`

## Taxonomy and Command Boundary

| term | project definition | PEARL route | hard boundary |
|---|---|---|---|
| text2task | Generate task definitions, bindings, constraints, and success verifiers from language. | `/gen-env` | May reuse a scene without changing geometry or placement. |
| text2scene | Generate or assemble spatial layout, support surfaces, and object placement from language. | `/gen-env` | Scene layout is versioned independently from task bindings. |
| text2asset | Generate or reconstruct geometry, articulation, textures, materials, or physics metadata. | generation2env | An asset is not executable until import and physics gates pass. |
| selection2env | Retrieve and place existing execution-eligible assets, then emit reusable scene and task specs. | P0 `/gen-env` | Must not invent geometry or silently promote catalog-only assets. |
| generation2env | Create missing assets or scenes. | P1/P2 `/gen-env` | Must pass geometry, scale, collision, support, material, import, render, and verifier gates. |
| sim-in-loop repair | Diagnose static or embodied failure, patch scene/task/program specs, and rerun regression cases. | `/gen-env`, `/diagnose` | A patch is not promoted from static validation alone. |
| domain randomization / data generation | Produce controlled placement, appearance, language, embodiment, and trajectory variation. | `/collect`, `/train` | Diversity is measured from actual manifests and trajectories. |
| policy eval / data hook | Connect scene/task artifacts to evaluation, diagnosis, promotion, and failure-to-data decisions. | `/evaluate`, `/diagnose` | Execution completion is separate from task success and promotion. |

[COMPUTED] [CONFIDENCE: HIGH] The current P0 implementation follows this boundary: RoboTwin and AgenticSim aliases are execution candidates, Articraft metadata is searchable but catalog-only by default, and new-asset forge/material routes remain typed blockers.

## Primary Source Registry

### Academic and project sources

| source | year | primary links | input -> output | environment / assets | open and reproducible status | RoboTwin / AgenticSim relation | tier |
|---|---:|---|---|---|---|---|---|
| SceneSmith | 2026 | [project](https://scenesmith.github.io/) · [paper](https://arxiv.org/abs/2602.09153) · [code](https://github.com/nepfaff/scenesmith) · [data](https://huggingface.co/datasets/nepfaff/scenesmith-example-scenes) | language scene prompt -> simulation-ready hierarchical indoor scene and policy-evaluation scenes | HSSD, Objaverse/ObjectThor, ArtVIP/PartNet-Mobility, AmbientCG, SAM3D/Hunyuan3D | MIT code released; public snapshot audited. Full run still needs gated checkpoints, datasets, API credentials, GPU, and storage. | Adopt designer/critic/orchestrator, retrieval, support-surface, collision/stability, and policy-eval gates; translate outputs to RoboTwin/SAPIEN contracts. | P1 |
| RoboTwin 2.0 | 2025 | [project](https://robotwin-platform.github.io/) · [paper](https://arxiv.org/abs/2506.18088) · [code](https://github.com/RoboTwin-Platform/RoboTwin) · [docs/data](https://robotwin-platform.github.io/doc/) | task/object annotations + MLLM code -> executable tasks, expert data, benchmark episodes | RoboTwin-OD: 731 instances, 147 categories; 50 tasks; five embodiments; five randomization axes | MIT code released and installed. Asset, Base_Task/CuRobo, collect, rollout, and verifier paths execute on RTX 5090. | Primary P0 execution/data backend; AgenticSim aliases require a concrete RoboTwin backend asset. | P0 |
| RoboTwin generative digital twins | 2025 | [project](https://robotwin-benchmark.github.io/) · [paper](https://arxiv.org/abs/2504.13059) · [code](https://github.com/RoboTwin-Platform/RoboTwin) | single object image + task/spatial relations -> generated object twin, task code, sim/real data | generated twins and RoboTwin platform | Shared MIT code is installed, but image-to-twin generation was not rerun here. | Keep as P1 generation2env reference; require scale, collision, URDF/import, and task-verifier gates. | P1 |
| RoboGen | 2024 | [project](https://robogen-ai.github.io/) · [paper](https://arxiv.org/abs/2311.01455) · [code](https://github.com/Genesis-Embodied-AI/RoboGen) | proposed task/skill -> generated environment, supervision, trajectories, learned skill | RoboGen manipulation and locomotion simulation stack | Apache-2.0 code released; audited but not installed or run here. | Positioning reference for open-ended generate-learn loops, not a direct P0 adapter. | positioning |
| FATE | 2026 | [paper](https://arxiv.org/abs/2603.01505) · code: not released | generated task + scene/policy spec -> grounded task after static audit, embodied feasibility, and active repair | simulator scenes and embodied interaction | Paper open; no official code link located. Only audit ordering and contracts are reproducible. | Adopt static-affordance/layout checks before execution feasibility and repair/revalidation. | P0 |
| VLMbench / AMSolver | 2022 | [paper](https://arxiv.org/abs/2206.08522) · [code](https://github.com/UCSB-AI/VLMbench) · [dataset script](https://github.com/UCSB-AI/VLMbench/blob/main/download_dataset.sh) | compositional instruction + constraints -> tasks, demonstrations, benchmark episodes | RLBench, CoppeliaSim/PyRep, AMSolver templates | MIT code released; audited but not installed here. | Borrow compositional constraints and verifiers; use `/transfer` for RLBench-to-RoboTwin equivalence. | P1 |
| REALM | 2025 | [project](https://martin-sedlacek.com/realm/) · [paper](https://arxiv.org/abs/2512.19562) · [code](https://github.com/martin-sedlacek/REALM) | VLA + task + perturbations -> generalization metrics and real-to-sim validation | 7 skills, 15 perturbations, thousands of objects, aligned control | Code released but no SPDX license detected; not installed here. | Reference for held-out perturbations, control alignment, real-to-sim correlation, and promotion evidence. | P1 |
| ASPIRE | 2026 | [project](https://research.nvidia.com/labs/gear/aspire/) · [paper](https://arxiv.org/abs/2607.00272) · code: coming soon | task + code-as-policy + multimodal traces -> repaired program and reusable skill | LIBERO-Pro, Robosuite, BEHAVIOR-1K, execution engine | Paper/project open; official page explicitly says code is coming soon. | Direct prior for trace capture, failure localization, re-execution, held-out regression, and skill promotion. | P0 |
| ENPIRE | 2026 | [project](https://research.nvidia.com/labs/gear/enpire/) · [paper](https://arxiv.org/abs/2606.19980) · code: not released | real task + policy + reset/rollout feedback -> repeated verify/refine iterations | Push-T, pin insertion, zip-tie, GPU insertion, simulation evaluation | Paper/project open; no official code link located. | Direct prior for reset-execute-verify-refine; PEARL must prove extra scene/adapter/data surfaces. | positioning |
| Articraft | 2026 | [project](https://articraft3d.github.io/) · [paper](https://arxiv.org/abs/2605.15187) · [dataset](https://huggingface.co/datasets/camvsl/Articraft-10K/tree/main) · generation code: not located | articulated-asset request -> agent-written asset program, geometry, joints, tests, URDF archives | Articraft-10K, over 10K assets and 245 categories; audited snapshot has 9,996 archives | Dataset is CC-BY-4.0. Workspace metadata is complete; 3 sample imports pass and 2 retain collision blockers. | Searchable P1 source only; require archive, URDF, mesh, scale, collision, SAPIEN, and task gates. | P1 |
| NeuMatEx | 2026 | [project](https://nvlabs.github.io/neumatex/) · [paper](https://arxiv.org/abs/2606.26715) · code: not released | calibrated multi-view images -> diffuse base color and neural specular material latent | inverse-rendered appearance; geometry/tasks are out of scope | Paper/project open; no official extraction code located. | P2 material sidecar only; cannot substitute for geometry, physics, or task verification. | P2 |
| EmbodiedGen V2 | 2026 | [project](https://horizonrobotics.github.io/EmbodiedGen/) · [paper](https://arxiv.org/abs/2607.07459) · [code](https://github.com/HorizonRobotics/EmbodiedGen) | language/images/edit/task requirements -> articulated assets, layouts, affordances, task worlds, URDF/mesh/3DGS and cross-sim exports | generated rigid/articulated objects, garments, rooms, backgrounds, and task-oriented worlds | Apache-2.0 code released and audited at `cc3015c`; full generation needs checkpoints/services/storage and was not run here. | P1 generation2env backend candidate; require geometry, scale, collision, articulation, import, verifier, and transfer-equivalence gates. | P1 |
| RoboVerse / MetaSim | 2025 | [project](https://roboverseorg.github.io/) · [paper](https://arxiv.org/abs/2504.18904) · [code](https://github.com/RoboVerseOrg/RoboVerse) | normalized robot/scene/task/simulator configs -> cross-simulator environments, trajectories, datasets, and evaluations | Isaac, MuJoCo, SAPIEN, PyBullet, Genesis, and related backend integration | Apache-2.0 code released and audited at `e9b5c6e`; not installed or run here. | P1 Open X Sim schema/adapter candidate; it is an integration substrate rather than a language-to-scene generator. | P1 |
| Generative 3D worlds for sim-to-real VLA RL | 2026 | [paper](https://arxiv.org/abs/2603.18532) · [EmbodiedGen project](https://horizonrobotics.github.io/EmbodiedGen/) | pretrained imitation VLA + generated interactive worlds -> RL-fine-tuned policy and sim/real evaluation | hundreds of EmbodiedGen interactive scenes | Paper open; no separate RL training codebase was identified and results were not reproduced here. | P1 evidence that generation must connect to `/train`, held-out `/evaluate`, and `/transfer`, not stop at visual output. | P1 |
| DIPO | 2025 | [project](https://rq-wu.github.io/projects/DIPO/DIPO.html) · [paper](https://arxiv.org/abs/2505.20460) · [code](https://github.com/RQ-Wu/DIPO) | dual-state object images -> articulated parts, layout, joints, and URDF-oriented assets | PM-X and LEGO-Art-expanded articulated objects | Code audited at `78efeff`; no repository LICENSE file was located; checkpoints, data, and GPT-4o/Azure configuration are required. | P1 missing-articulation fallback after retrieval failure; require URDF, joint, collision, state-transition, import, and task gates. | P1 |
| 3D-Fixer | 2026 | [project](https://zx-yin.github.io/3dfixer/) · [paper](https://arxiv.org/abs/2604.04406) · [code](https://github.com/HorizonRobotics/3D-Fixer) | single scene image + fragmented geometry anchors -> in-place completed 3D assets preserving layout | ARSG-110K plus reconstructed scene objects | Apache-2.0 code audited at `f6a6032`; models/data/submodules and about 24 GB GPU are documented; not run here. | P1 image-conditioned geometry fallback; require metric scale, watertightness, collision, physics, import, render alignment, and verifier gates. | P1 |
| Uni3R | 2026 | [paper](https://arxiv.org/abs/2508.03643) · [code](https://github.com/HorizonRobotics/Uni3R) | unposed multi-view RGB -> Gaussian scene, depth, and open-vocabulary semantics | observation-derived Gaussian representation | CC-BY-NC-SA-4.0 code audited at `4a5dd00`; README release statements conflict with retained checkpoint/inference TODOs; not run here. | P2 observation/semantic sidecar only; it is not collision geometry, dynamics, or an executable task environment. | P2 |

### Direct implementation references

| source | year | primary links | input -> output | open / reproducible status | direct relation | tier |
|---|---:|---|---|---|---|---|
| video2sim-forge | 2026 | [code](https://github.com/Marvelousp4/video2sim-forge) | RGB-D/video + calibration + prompts -> masks, meshes, poses, scene JSON, URDF, physics metadata | MIT code released and audited; no valid capture package or imported asset exists here. | P1 forge fallback only after existing-asset failure; must pass world-frame, scale, collision, import, and verifier gates. | P1 |
| robotwin-text2env-demo | 2026 | [code](https://github.com/yezheng04/robotwin-text2env-demo) | tabletop task prompt + catalog -> candidates, placement, scene module, static validation, critic | Public base plus locked patch reproduces the audited result tree; local and RTX 5090 tests pass. Public repo has no detected SPDX license. | Direct P0 implementation base. | P0 |
| AgenticSim placement_agent | 2026 | private/local implementation; public link unavailable | task prompt + alias catalog -> placement spec with backend asset ids | Audited alias snapshot records source commit `f34d56a`; 8/8 aliases resolve to live RoboTwin assets. Full AgenticSim repo is not on the 5090. | Direct placement-only compatibility surface; alias eligibility depends on concrete backend resolution. | P0 |

[COMPUTED] [CONFIDENCE: HIGH] Public repository snapshots and license metadata were rechecked through 2026-07-14. The exact snapshot hashes are recorded in `text2env_primary_sources.json`; paper-only systems are not represented as reproduced code.

## Candidate-Nominated Project Audit

[COMPUTED] [CONFIDENCE: HIGH] The candidate CV contributed 12 project labels. Nine map to identifiable public primary sources; six enter the core registry, three remain adjacent evidence, and three remain resume-only because no uniquely identifiable public artifact was located. The intake stores the CV filename and SHA-256 while omitting contact details.

### Adjacent public evidence

| project | public primary source | verified relevance | disposition |
|---|---|---|---|
| GaussTR | [paper](https://arxiv.org/abs/2412.13193) · [code](https://github.com/hustvl/GaussTR) | Gaussian semantic occupancy and foundation-model alignment on nuScenes; useful for observation/data-loop context. | Appendix only: it does not generate executable simulator environments. |
| DirtNet | [Fraunhofer publication](https://publica.fraunhofer.de/entities/publication/a98dd796-7363-469d-9fc2-030e157ba603) · [IEEE record](https://ieeexplore.ieee.org/document/9196559) | Visual dirt detection with synthetic data generation for cleaning robots. | Appendix only: historical synthetic-data evidence, not Text2Env. |
| InstanceNet | [Fraunhofer publication](https://publica.fraunhofer.de/handle/publica/412776) | Fast incremental instance detection through an ensemble of single-class detectors. | Appendix only: continual-learning/data-loop relevance, not environment generation. |

### Resume-only items

| project label | public-source result | disposition |
|---|---|---|
| WAM | No uniquely identifiable public paper, repository, or project page located under the CV label. | Do not add to scientific registry; request a title or link before review. |
| 4DLABEL | No uniquely identifiable public paper, repository, or project page located under the CV label. | Do not infer method, dataset access, or reproducibility from the resume description. |
| Unnamed CoRL 2026 instruction-following benchmark | No title, paper identifier, or repository was supplied. | Keep as unverified follow-up, not evidence. |

[COMPUTED] [CONFIDENCE: HIGH] `artifacts/literature_review/candidate_project_intake_paul_20260714.json` is the complete machine-readable audit. It verifies public artifacts and scope, not the candidate's personal contribution to those artifacts.

## Method Matrix

Legend: H = high/direct capability, M = medium/partial, L = low/adjacent, - = none. These are ordinal literature judgments, not benchmark scores.

| method | task gen | asset retrieve/gen | placement | physics/collision | code gen/repair | sim smoke | data collect | policy eval |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SceneSmith | M | H | H | H | M | H | M | H |
| RoboTwin 2.0 | H | H | M | H | H | H | H | H |
| RoboTwin digital twins | H | H | M | M | M | H | H | H |
| RoboGen | H | H | M | M | M | M | H | M |
| FATE | H | L | M | H | H | H | M | M |
| VLMbench / AMSolver | H | M | M | M | L | M | H | H |
| REALM | L | H | L | H | - | H | M | H |
| ASPIRE | M | - | L | H | H | H | M | H |
| ENPIRE | L | - | L | H | H | H | H | H |
| Articraft | - | H | - | M | H | L | - | - |
| video2sim-forge | - | H | M | L | L | L | L | - |
| NeuMatEx | - | M | - | - | - | L | - | L |
| robotwin-text2env-demo | M | H | H | M | M | H | M | L |
| AgenticSim placement_agent | M | M | H | L | L | L | - | - |
| EmbodiedGen V2 | H | H | H | H | H | H | H | H |
| RoboVerse / MetaSim | M | M | M | H | M | H | H | H |
| Generative 3D worlds for sim-to-real VLA RL | L | H | H | H | L | H | H | H |
| DIPO | - | H | - | M | M | L | - | - |
| 3D-Fixer | - | H | M | L | L | L | - | - |
| Uni3R | - | M | L | - | - | L | L | M |

[COMPUTED] [CONFIDENCE: HIGH] The machine-readable matrix requires all eight capability columns for every source and rejects rows that omit a capability.

## P0/P1/P2 Decision

### P0: adopted now

- [COMPUTED] [CONFIDENCE: HIGH] `robotwin-text2env-demo` is the locked implementation base. The public base plus local patch reproduces the same Git tree locally and on the RTX 5090.
- [COMPUTED] [CONFIDENCE: HIGH] RoboTwin is the trusted execution/data backend; AgenticSim aliases are eligible only when their backend asset resolves in the live RoboTwin catalog.
- [COMPUTED] [CONFIDENCE: HIGH] FATE's static-then-execution ordering is partially implemented through static validation, simulator rollout, retained failures, and repair/revalidation. General active repair is not complete.
- [COMPUTED] [CONFIDENCE: HIGH] ASPIRE-style typed traces, held-out seeds, diagnosis, and failure-to-data evidence exist. A promoted reusable skill library and a matched ASPIRE baseline do not.

### P1: next adapter and fallback work

- [COMPUTED] [CONFIDENCE: HIGH] Articraft metadata integration is complete for 9,996 entries; import evidence remains sample-level at three passes and two collision blockers.
- [KNOWN] [CONFIDENCE: HIGH] SceneSmith retrieval/support-surface logic is a strong reference, but its code, gated models, and datasets are not installed here.
- [COMPUTED] [CONFIDENCE: HIGH] video2sim-forge has a typed fallback contract only. No RGB-D/reference-video input, generated mesh, URDF, or RoboTwin import exists.
- [COMPUTED] [CONFIDENCE: HIGH] REALM-style held-out simulation has started, but policy success remains 1/4 and real-to-sim correlation has not been measured.
- [COMPUTED] [CONFIDENCE: HIGH] EmbodiedGen V2, DIPO, and 3D-Fixer now have audited producer contracts, snapshots, and downstream gates; none has produced an imported RoboTwin or Isaac asset in this workspace.
- [COMPUTED] [CONFIDENCE: HIGH] RoboVerse/MetaSim is now an Open X Sim adapter candidate. The schema relation is documented, but dependencies and same-task equivalence have not been executed.
- [KNOWN] [CONFIDENCE: HIGH] The generative-world sim-to-real paper makes `/train`, held-out `/evaluate`, and real `/transfer` part of the environment-generation evidence chain; its reported training result is not reproduced here.

### P2: deferred

- [COMPUTED] [CONFIDENCE: HIGH] NeuMatEx-style fields exist only as a material-sidecar blocker. Multiview extraction and renderer binding are not executed.
- [COMPUTED] [CONFIDENCE: HIGH] Uni3R is restricted to a Gaussian observation/depth/semantic sidecar until collision geometry, dynamics, and task bindings are independently supplied and validated.
- [KNOWN] [CONFIDENCE: HIGH] RLBench, Isaac Sim, MuJoCo, LIBERO, Robosuite, BEHAVIOR, and REALM require explicit `/transfer` contracts and same-task cross-adapter execution.
- [INFERRED] [CONFIDENCE: HIGH] Full generation2env should stay deferred until geometry, scale, collision, support, material, import, render, and task-verifier gates are automated.

## Zheng Ye to Gaochen Handoff

### Zheng Ye produces through `/gen-env`

Required fields:

`task_text`, `asset_candidates`, `selected_assets`, `placement_regions`, `support_surface`, `pose_constraints`, `camera_observation`, `robot_constraints`, `success_verifier`, `blockers`.

Required artifacts:

- selection2env JSON and placement manifest;
- RoboTwin task-program input with scene id, task binding, and placement SHA-256;
- generated scene module or adapter payload;
- static validation and critic review;
- simulator smoke and failure screenshots/videos;
- catalog, code, patch/commit, and source provenance.

### Gaochen consumes

| command | required inputs | required outputs / gates |
|---|---|---|
| `/collect` | scene id, task id, placement hash, camera definitions, object state schema, task binding | camera streams, qpos/actions, object states, success result, events, dataset manifest |
| `/train` | dataset manifest, episode paths, task config, camera order, action horizon, split definition | checkpoint, train state, validation metrics, provenance |
| `/evaluate` | checkpoint, scene/task binding, held-out seeds and placements, success verifier | per-episode execution, task success, dense diagnosis, aggregate promotion decision |

Open blockers:

- [COMPUTED] [CONFIDENCE: HIGH] Drawer articulation and interior-placement task API/verifier are not executable.
- [KNOWN] [CONFIDENCE: HIGH] SceneSmith and video2sim generation paths are not imported into RoboTwin/SAPIEN.
- [COMPUTED] [CONFIDENCE: HIGH] The material result is a bounded observation-color sidecar imported as `UsdPreviewSurface`; NeuMaTeX-style intrinsic neural material reconstruction is not reproduced.
- [KNOWN] [CONFIDENCE: HIGH] No matched ASPIRE implementation or reusable skill-library comparison exists.
- [COMPUTED] [CONFIDENCE: HIGH] The promoted policy is privileged, initial-pose-conditioned, and open-loop; visual, language-conditioned, closed-loop broad task generalization is not proven.

## After ASPIRE and ENPIRE

[KNOWN] [CONFIDENCE: HIGH] ASPIRE already covers multimodal trace debugging, code-as-policy repair, held-out validation, and reusable skill memory. Its official project page currently says code is coming soon.

[KNOWN] [CONFIDENCE: HIGH] ENPIRE already covers the real-world reset, execute, verify, and refine loop plus coding-agent autoresearch. No official code link is exposed by its project page.

[INFERRED] [CONFIDENCE: HIGH] PEARL's distinct hypothesis therefore depends on five combined system dimensions, not on generic self-improvement:

1. scene-task decoupling;
2. Open X Sim aggregation;
3. Any Sim Transfer;
4. MCP memory/debug loop;
5. failure-to-data requirement.

Required acceptance gates:

- scenes have stable ids and content hashes independent of task bindings;
- adapters declare asset, reset, step, observation, action, renderer, physics, and verifier contracts;
- failures produce typed diagnoses and next data or harness requirements;
- proposed harness edits run regression cases and preserve rollback state before promotion;
- target-backend execution is required for cross-simulator or real-robot claims.

[INFERRED] [CONFIDENCE: HIGH] This combination is a research hypothesis, not proven publication novelty or priority. A broader related-work search and controlled ablations are still required.

## Next Experiments

| experiment | current status | evidence / missing proof |
|---|---|---|
| Two task specs from one byte-identical RoboTwin scene | pass | `artifacts/scene_task_decoupling/apple_plate_two_tasks.json` |
| Same task through RoboTwin/SAPIEN and Isaac Sim task-semantic adapters | pass, bounded | Isaac emits `/gen-env`, `/collect`, `/evaluate`, `/diagnose`, and `/transfer`; 120 trace steps, 24/24 unique frames, and target verifier pass in `runs/isaac_openxsim_place_container_plate_v1/`. |
| PEARL diagnosis/repair versus matched ASPIRE-style program repair | not run | ASPIRE code is not released and no matched baseline is implemented. |
| Failure memory versus no-memory adapter selection | pass, bounded | Fixed checkpoint, placement, seeds 4/5/6, actions, and evaluator: no-memory `swap_red_blue` 0/3; memory-selected `identity` 3/3 in `artifacts/text2env_empirics/memory_ablation_rgb_adapter_v1.json`. |
| Failure-to-data retraining improves held-out task success enough for promotion | executed, failed promotion | Recovery data and retraining executed; held-out success remained 1/4. |
| Observation-derived material sidecar roundtrip | pass, bounded | 94 source foreground pixels; native `UsdPreviewSurface`; 15,204 rendered foreground pixels; RGB MAE 0.05098 and CIE76 Delta-E 13.8758 in `runs/isaac_material_sidecar_roundtrip_v1/roundtrip_report.json`. |
| Predeclared failure score predicts verifier failures | executed, null/negative result retained | 12/12 executable episodes, 10 successes, 2 failures; point-biserial `r=-0.2545`, exact two-sided label-permutation `p=0.7273`. The score is not useful as a prioritization signal in this sample. |
| Learned policy promotion across held-out, randomized, and cross-task gates | pass, bounded | Apple/plate held-out 4/4, randomized 4/4, can/basket fixed-placement seed holdout 3/3 in `artifacts/sceneagent_policy_promotion/pose_conditioned_policy_promotion_v1.json`. |

## Claim Boundary

[COMPUTED] [CONFIDENCE: HIGH] This package proves source coverage, method comparison, taxonomy, implementation routing, handoff fields, and five bounded empirical follow-through gates: task-semantic cross-simulator execution, memory ablation, material sidecar roundtrip, retained failure-score correlation, and privileged policy promotion.

[KNOWN] [CONFIDENCE: HIGH] It does not reproduce paper-only systems, establish public priority or state of the art, complete generative scene synthesis, prove robot-policy cross-simulator transfer, reproduce intrinsic neural-material estimation, validate real-to-sim transfer, build a reusable skill library, or prove a visual language policy.
