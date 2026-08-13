# PEARL as an Embodied Harness System

## One-Page Thesis

[INFERRED] [CONFIDENCE: HIGH] PEARL should be framed as an embodied harness system: a versioned experimental layer around an embodied model or policy under test. The object under test is a declared checkpoint executed against a declared scene, task, embodiment, seed plan, and simulator or robot adapter. The evolving harness contains prompts, tools, simulator adapters, reset and evaluation protocols, validators, memory, data requirements, promotion rules, and rollback pointers.

[KNOWN] [CONFIDENCE: HIGH] The public paper claim must not currently be "first real embodied harness system." Priority is not established by this workspace, and nearby systems already cover substantial pieces: coding harnesses cover execution and repair; ASPIRE covers multimodal trace debugging, code-as-policy repair, regression, and skill reuse; ENPIRE covers reset, execute, verify, and refine loops; RoboTwin/Text2Env covers task, scene, simulator, and data generation; VLA evaluation covers held-out task execution and metrics. The defensible current claim is narrower: PEARL specifies and partially implements an embodied harness artifact contract that versions more physical-experiment surfaces together.

[INFERRED] [CONFIDENCE: HIGH] The scientific unit is a controlled harness transition `h_t -> h_{t+1}`, not a vague self-improvement episode. `h_t` declares reset, observations, actions, tools, adapters, validators, memory, data requirements, safety limits, and rollback. `/collect` or `/evaluate` executes the embodied task and writes synchronized traces. `/diagnose` mines trace-grounded weaknesses and clusters failures. A proposal changes a bounded harness surface, records expected effect and confounds, and produces a versioned diff. Regression compares baseline and candidate under matched checkpoint, seeds, tasks, and declared environment conditions. Promotion requires distinct execution, task, safety, robustness, and provenance gates. Rejected candidates remain immutable evidence.

[KNOWN] [CONFIDENCE: HIGH] Policy retraining is a separate intervention. If the policy checkpoint, dataset, scene distribution, or benchmark changes during a candidate comparison, the result cannot be attributed to a harness-only edit. PEARL may orchestrate `/train`, but the paper must report policy and harness changes separately.

[COMPUTED] [CONFIDENCE: HIGH] The current implementation supports this framing with typed `/gen-env`, `/collect`, `/train`, `/evaluate`, `/diagnose`, and `/transfer` contracts; real RoboTwin command-loop traces and media; scene-task decoupling; dense diagnosis; fallback gates; a fixed-checkpoint RGB-adapter promotion from 0/3 to 3/3; a matched memory-mediated 0/3 to 3/3 correction; one Isaac task-semantic five-command bundle with 120 trace steps and a passing verifier; and a privileged pose-conditioned open-loop policy that passes 4/4 held-out, 4/4 randomized, and 3/3 second-task fixed-placement seed gates. It does not prove publication priority, real-robot evolution, robot-policy cross-simulator transfer, or a visual language policy.

## Loop Mapping

| order | harness sketch term | PEARL term | primary artifact | promotion gate |
|---:|---|---|---|---|
| 1 | current harness `h_t` | versioned commands, adapters, validators, memory, tasks, rollback | command registry and schemas | all references resolve before execution |
| 2 | embodied task execution | `/collect` or `/evaluate` rollout | `run_state.json`, `events.jsonl`, traces, verifier | completion and task success remain separate |
| 3 | weakness mining | `/diagnose` over state/action/gripper/visual/contact traces | dense diagnosis | verdicts cite concrete trace fields |
| 4 | clustered failure patterns | failure cluster plus next data requirement | placement robustness diagnosis | episode and artifact provenance retained |
| 5 | harness proposal | bounded proposal against typed surfaces | proposal record | edit, effect, confounds, owner, rollback named |
| 6 | proposed edits | schema/prompt/tool/adapter/validator/reset/data patch | candidate diff | no silent policy or benchmark change |
| 7 | regression test | schema, replay, visual, safety, held-out eval | verifier and regression report | matched checkpoint, seeds, tasks, conditions |
| 8 | promotion decision | accept, reject, or block | promotion record | execution alone cannot trigger promotion |
| 9 | updated harness `h_{t+1}` | committed state with parent and rollback | versioned artifact | only accepted candidate becomes active |

## Embodied-Specific Harness Surfaces

| surface | required contract | example artifacts |
|---|---|---|
| reset | robot/simulator state, seed, placement, calibration, reset failures | run state, scene manifest |
| observations | cameras, proprioception, object state, timing, channels | PNG/MP4, object states, HDF5 |
| actions | action space, controller, frequency, gripper semantics, alignment | action traces, move events |
| tool calls | planner, simulator, renderer, asset and validator I/O | command events |
| simulator adapters | engine, renderer, assets, materials, reset/step/verifier, access | adapter matrix |
| safety limits | workspace, collision, velocity, contact, termination | robot constraints and safety verdict |
| real/sim traces | timestamped state, action, image, process, verifier evidence | run state, events, rollout logs |
| validation rules | schema, import, physics, visual, task, robustness, provenance | verifier and visual review |
| data requirements | failure-linked coverage, diversity, and split request | next data requirement, dataset manifest |
| memory | versioned failure, proposal, decision, reusable debug record | diagnosis and audit log |
| rollback | parent harness, candidate diff, rejection evidence, restore pointer | commit and rollback pointer |

## Novelty Table

| comparison | overlap already covered | PEARL system hypothesis | implemented evidence | missing evidence |
|---|---|---|---|---|
| LLM coding harnesses | execution, tests, tools, repair, memory, regression | embodied reset, timing, adapters, safety, physical traces, task verifiers need a richer contract | typed commands, traces/media, verifiers, fixed-checkpoint adapter promotion, matched memory-mediated repair | matched coding-harness baseline absent |
| ASPIRE | multimodal traces, code-as-policy repair, held-out regression, skill reuse | also version scene, task, adapter, data, validator and rollback surfaces | scene-task decoupling, dense traces, failure-to-data loop, one matched failure-memory ablation | matched ASPIRE implementation and skill-library comparison absent |
| ENPIRE | reset, execute, verify, refine, repeated real improvement | expose simulation-first scene/task/adapter artifacts before real promotion | RoboTwin robot-action loops and one Isaac task-semantic five-command bundle | no real-robot harness evolution or sim-to-real result |
| RoboTwin/Text2Env | task/scene generation, simulator execution, data, repair | Text2Env is one `/gen-env` route under collection, diagnosis, evaluation, transfer and rollback | selection2env, same-scene two-task execution, Isaac semantic transfer, material sidecar roundtrip | generative scene synthesis, source-asset/material identity, robot-policy transfer incomplete |
| standard VLA evaluation | held-out tasks, success metrics, traces, benchmarks | failures become typed harness proposals and data requirements with rollback | failed ACT promotion retained; privileged open-loop policy passes bounded held-out, randomized, and second-task gates | no visual, language-conditioned, closed-loop broad task policy |

[INFERRED] [CONFIDENCE: HIGH] The differentiation column remains a system hypothesis, not a priority result. The workspace now supplies one matched fixed-checkpoint harness edit, one controlled memory-mediated correction, and one second-simulator task-semantic bundle; publication-level support still requires independent replications, stronger baselines, broader tasks, robot-policy transfer, and real-robot evaluation.

## Figure Caption And Brief

**Caption.** PEARL maintains a versioned embodied harness `h_t` around a declared model or policy checkpoint. It executes tasks through simulator or robot adapters, records synchronized state, action, visual, contact, tool, and verifier traces, clusters weaknesses, and proposes bounded edits to harness surfaces. Candidate edits run matched regression and safety gates; accepted candidates become `h_{t+1}`, while rejected candidates remain evidence with rollback context.

**Diagram brief.** Left: `h_t` around a fixed checkpoint and explicit harness surfaces. Top: embodied execution and traces, separating completion from success. Center: trace-grounded weakness mining and clustered failure patterns. Bottom: bounded candidate edits and matched regression with accept, reject, and blocked outcomes. Right: `h_{t+1}` with parent/rollback arrows. Show policy retraining as a separate versioned intervention, not a hidden harness edit.

The current diagram asset is `reports/embodied_harness/assets/embodied_harness_loop.png`.

## Implementation Routing

| command | routed harness surfaces | required fields/artifacts | promotion gate |
|---|---|---|---|
| `/gen-env` | adapters, validation, reset | scene/task/asset manifests, verifier, blockers | schema, provenance, import, placement, visual, physics, verifier |
| `/collect` | reset, observations, actions, traces, data | seed/reset record, state/action/camera traces, dataset manifest | completeness, alignment, diversity, collection provenance |
| `/evaluate` | validation, safety, traces, rollback | checkpoint, split, verifier, episodes, promotion decision | matched protocol, success, safety, held-out robustness, provenance |
| `/diagnose` | traces, memory, data, validation | cluster, root cause, trace provenance, next data, proposed edit | trace-grounded verdict with unknown states preserved |
| `/transfer` | adapters, tools, validation, rollback | source, engine/renderer, formats, materials, APIs, license/access, difficulty | access, translation, API parity, smoke execution |

The normative field lists and evidence paths are in `artifacts/embodied_harness/embodied_harness_spec.json`; command contracts remain in `artifacts/openxsim/openxsim_command_registry.json`.

## Proof Obligations

| claim | current status | next evidence |
|---|---|---|
| implemented embodied harness artifact contract | proven, bounded | replicate the candidate-promotion contract beyond one fixed scene and RGB adapter |
| harness edits improve outcomes independently of policy changes | proven, bounded | repeat across independent harness surfaces, tasks, and null-effect controls |
| Open X Sim supports task-semantic reuse | proven, bounded | add robot embodiment, joint-action policy, source assets/materials, and another task |
| failure memory improves a controlled harness decision | proven, bounded | repeat across independent failure classes and null-effect controls |
| learned policy passes the bounded SceneAgent promotion contract | proven, bounded | replace privileged open-loop pose input with visual/language closed-loop control and broader tasks |
| real-robot harness evolution | not run | safety-reviewed hardware transition with reset, traces, verifier, rollback, and attribution |
| first real embodied harness system | not established | systematic priority search, precise definition, independent audit, public timestamps |

## Claim Boundary

[COMPUTED] [CONFIDENCE: HIGH] A fixed-checkpoint matched ablation changes only `observations.runtime_color_adapter`: the swapped-red-blue baseline executes 3/3 episodes and succeeds 0/3, while the identity candidate executes and succeeds 3/3; the candidate is accepted with a rollback pointer. A matched memory controller then selects the same correction for 3/3 success versus 0/3 without memory. One normalized task executes five task-semantic commands in Isaac with 120 trace steps, 24/24 unique frames, declared transfer losses, and target-verifier success. A separate privileged pose-conditioned open-loop policy passes 4/4 held-out placements, 4/4 domain-randomized placements, and 3/3 fixed-placement seeds on a second task. [KNOWN] [CONFIDENCE: HIGH] These results do not establish first-in-field priority, real-robot evolution, robot-policy cross-simulator transfer, source-asset/material parity, intrinsic neural-material recovery, or a visual language closed-loop policy.
