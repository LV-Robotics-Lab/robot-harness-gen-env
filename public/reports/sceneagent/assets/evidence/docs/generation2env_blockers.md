# generation2env Blocker Memo

## Current Decision

`selection2env` is the P0 route. It selects and arranges existing assets, then emits scene/task specs and task-program inputs. Full `generation2env` remains gated until asset import, collision proxies, scale, articulated joints, materials, physics metadata, simulator load, and verifier smoke are reliable.

## Local Blockers on `jingxiang-b850m-c`

| blocker | evidence | implication |
|---|---|---|
| generated selection2env rollout evidence is still narrow | `runs/generated_rollout_apple_plate_action_repair_pass/rollout_report.json` and `runs/generated_rollout_can_basket_action_repair/rollout_report.json` pass generated `play_once()` action-repair probes; `runs/generated_collect_apple_plate_action_repair/collection_report.json` and `runs/generated_collect_can_basket_action_repair/collection_report.json` pass three-episode generated collections; `runs/act_eval_smoke_generated/evaluate_report.json` executes learned ACT on apple/plate only | scripted apple/plate and can/basket collections plus bounded apple/plate learned-policy execution can be claimed; broad task coverage and robustness cannot |
| learned policy does not pass the placement-robustness gate and default wrappers are not wired | fixed-placement ACT passes 3/3; varied native data reaches 15 episodes, 10 placements, and 14 unique trajectories; two signature-disjoint held-out evals each score 1/4; `runs/policy_train_eval_entrypoint_probe/probe_report.json` separately records old data/config/module assumptions | substantially broader placement data, declared domain randomization, and cross-task learned eval are required for promotion; default wrapper repair is optional only if the bounded adapter becomes the supported route |
| broad task-success semantics not accepted | smoke images show initial placement scenes, and generated final-relation review covers only apple/plate plus can/basket collection episodes | visual semantic review remains required for new generated scenes and held-out tasks |
| forge/material fallbacks are blocked by missing inputs | `artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json`, `artifacts/generation_fallback/blocked_neumatex_material_sidecar.json`, and `artifacts/generation_fallback/fallback_blocker_summary.json` record typed `/gen-env` blockers for the video2sim-forge route and NeuMaTeX-style material sidecar route | these are machine-readable blockers, not dry-run execution results; capture/reference videos, calibrated multiview inputs, import artifacts, and renderer bindings are still required |
| nonfatal renderer denoiser warning | SAPIEN smoke emits `OIDN Error: unsupported device type: CUDA` but still writes nonblank images and reports | render smoke is usable, but renderer warnings should stay visible in logs |
| broad generated-asset execution coverage remains gated | live discovery finds 123 RoboTwin objects; all eight AgenticSim placement aliases resolve to RoboTwin backends; Articraft-10K contributes 9996 searchable URDF archive entries; three sampled Articraft archives pass SAPIEN load/step and two fail collision geometry | existing RoboTwin/AgenticSim selection uses the live discovery catalog rather than the small fallback catalog, but broad Articraft scale/material/task suitability still needs per-asset import gates |

## Forge Fallback Boundary

The missing-asset branch may later call a video/RGB-D-to-sim forge such as `video2sim-forge`. That branch should emit:

- capture source;
- object prompts;
- masks;
- mesh paths;
- world-frame poses;
- URDF or equivalent articulated description;
- approximate physics metadata;
- provenance.

The forge output still must pass:

- import into RoboTwin/SAPIEN or selected adapter;
- scale and support checks;
- collision proxy checks;
- joint/articulation sanity checks;
- material/render binding checks;
- simulator smoke;
- visual semantic review.

Current typed blocker artifact: `artifacts/generation_fallback/blocked_drawer_mug_video2sim_forge.json`.

## Material Sidecar Boundary

NeuMaTeX-style material extraction belongs in a sidecar, not inside geometry. The sidecar should carry:

- textures;
- albedo;
- specular latent or equivalent material representation;
- uncertainty;
- lighting assumption;
- renderer binding;
- provenance.

Geometry, collision, joints, scale, and physical parameters remain separate gates.

Current typed blocker artifact: `artifacts/generation_fallback/blocked_neumatex_material_sidecar.json`.

## Current Simulator Status

The missing-root blocker is resolved: `external/RoboTwin` exists, required object assets are present, and three supported placements have `pass_asset_load_render` reports under `runs/smoke_asset_*`.

The original RTX 5090 planner-stack blocker is resolved for smoke: `robotwin-5090` uses `torch 2.11.0+cu128`, compiles CuRobo for `sm_120`, imports `Base_Task`, and runs three `runs/smoke_basetask_*` cases with status `pass`.

The remaining gap is successful randomized/cross-placement/cross-task learned-policy performance, held-out semantic review, broad Articraft scale/material/task-suitability validation, forge/material execution, and broader generated-task repair. Cross-placement `/evaluate` infrastructure and data repair are now proven, but both held-out ACT splits score 1/4; default wrapper compatibility is still not.

## Unsupported Task API Example

`runs/probe_static_drawer_mug_unified/scene_generation_summary.json` records `pass_static_scene_module`. Grounding finds:

```text
drawer -> 036_cabinet through the AgenticSim alias snapshot
mug -> 039_mug through the live RoboTwin catalog
```

`artifacts/selection2env/task_drawer_mug_blocker.json` still rejects the task because the current task adapter has no verified drawer-open action, interior placement binding, or containment success verifier. This is a task/API blocker, not an asset-catalog miss. A forge fallback is unnecessary for these two object identities unless a different asset geometry is required.
