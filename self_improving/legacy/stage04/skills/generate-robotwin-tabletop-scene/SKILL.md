---
name: generate-robotwin-tabletop-scene
description: Generate and validate RoboTwin tabletop placement scenes from new natural-language prompts. Use when Codex needs to turn a prompt such as "a laptop is on the right side of a knife" into a RoboTwin scene preview by creating or updating an asset catalog, running the harness, performing smoke validation, requiring visual/VLM review, repairing placement failures, and recording new lessons back into skills.
---

# Generate RoboTwin Tabletop Scene

Use this skill as the top-level workflow for a new tabletop placement prompt. It coordinates the repo's existing components:

- `harness/` for prompt-to-placement pipeline.
- `mcp_lite/` for validation, smoke, artifact, and visual-review tools.
- `scripts/run_robotwin_placement_smoke.py` for RoboTwin loading and preview rendering.
- `asset_catalogs/` for prompt-specific asset metadata and placement defaults.
- The focused skills under `skills/` for Designer, Critic, Orchestrator, asset grounding, and smoke review behavior.
- RoboTwin's `code_gen/` pattern as inspiration for structured inputs, simulator feedback, visual observation, and iterative repair.

This skill assumes the agent is working in the `robotwin-text2env-demo` repo and the user has a RoboTwin install such as `~/RoboTwin`. If the repo components are missing, ask for the repo or clone it before running.

## Required Workflow

1. Run asset grounding from the user's prompt against the master catalog.
2. Create or reuse a prompt case catalog for the selected assets.
3. Generate and validate `TabletopPlacementSpec`.
4. Generate a reusable `generated_scenes/<case>_scene.py` module.
5. Run RoboTwin smoke with `--run-smoke`.
6. Inspect preview image/video with human, Codex visual reference, or external VLM.
7. If visual review fails, repair catalog defaults, placement logic, or generated scene code and rerun.
8. Only mark full pass when static validation, scene module generation, smoke, and explicit visual review all pass.
9. Record any newly discovered pitfall in the relevant skill and catalog.

## References

Read these only as needed:

- `references/new-prompt-workflow.md`: exact end-to-end commands and artifact expectations.
- `references/asset-catalog-rules.md`: how to build prompt-specific catalogs and handle rigid/articulated assets.
- `references/visual-review-gate.md`: how to decide pass/pending/fail and write visual review JSON.
- `references/robotwin-code-gen-patterns.md`: which RoboTwin `code_gen/` ideas to reuse and which task-code-generation parts to avoid.
- `references/known-pitfalls.md`: lessons learned from apple/plate, vegetable/basket, laptop/knife runs.

## Status Rules

- `PASS_STATIC_ONLY` / `pass_static_scene_module`: static JSON/catalog validation passed and scene module was generated; no RoboTwin render yet.
- `REVIEW_REQUIRED` / `pending_visual_review`: smoke rendered artifacts, but no valid visual report has passed.
- `PASS`: static validation, smoke, and explicit visual review report all passed.
- `FAIL`: static validation, smoke, or visual review failed.

Never treat smoke success as visual success.

## Standard Commands

Static-only:

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<placement prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<case_name>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name>
```

Smoke render:

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<placement prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<case_name>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name> \
  --run-smoke
```

Full pass after visual review:

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<placement prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<case_name>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name>_visual_pass \
  --run-smoke \
  --visual-review-report previews/<run_name>/visual_review_codex_reference.json
```

## Non-Negotiables

- Do not invent RoboTwin asset ids or model ids.
- Do not rely on object mention order for spatial relations.
- Interpret natural-language directions from the robot / dual-arm first-person viewpoint. Left, right, front, and back mean the robot's left/right/front/back unless the prompt explicitly names another frame of reference.
- Do not mark a scene pass without visual review.
- Do not commit large generated data such as HDF5, full `runs/`, checkpoints, or RoboTwin assets.
- Do keep small preview evidence and prompt-specific asset catalogs when useful.
- Do update focused skills with new lessons after each new prompt.
