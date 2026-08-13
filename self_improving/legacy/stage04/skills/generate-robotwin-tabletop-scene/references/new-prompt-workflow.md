# New Prompt Workflow

Use this workflow for a prompt that has not been run before.

## 1. Understand The Prompt

Extract:

- object mentions
- spatial relations: left/right/near/on/inside/behind/in-front
- whether objects are scene background/reference objects or likely manipulands
- whether the prompt is only scene placement, not a RoboTwin task program

Interpret all natural-language directions from the robot / dual-arm first-person viewpoint. Unless the prompt explicitly says otherwise, `left`, `right`, `front`, and `back` mean the robot's left, right, front, and back, not the external observer camera's screen direction.

Do not use object mention order as a placement rule.

## 2. Ground Assets

Search in this order:

```bash
grep -RIn "<object term>" robotwin_asset_inventory.md asset_catalogs
find ~/RoboTwin/assets/objects -maxdepth 1 -type d | grep -Ei "<object term>"
```

Run the scene-generation harness. It performs deterministic asset grounding, writes `asset_grounding.json`, and creates or reuses `asset_catalogs/prompt_cases/<short_prompt>.json`.

## 3. Static Scene Generation

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<short_prompt>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name>
```

Expected status: `PASS_STATIC`.

Inspect:

- `asset_grounding.json`
- `prompt_case.json`
- `final_placement.json`
- `static_validation_final.json`
- `scene_codegen_report.json`
- `generated_scenes/<short_prompt>_scene.py`
- relations in `final_placement.json`

## 4. Smoke Render Generated Scene

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<short_prompt>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name> \
  --run-smoke
```

Expected status without visual report: `pending_visual_review`.

Inspect:

- `runs/<run_name>/smoke/smoke_report.json`
- `runs/<run_name>/smoke/observer_camera.png`
- `runs/<run_name>/smoke/head_camera.png`
- `runs/<run_name>/smoke/observer_camera.mp4`

## 5. Visual Review

Use human/Codex visual reference/external VLM. Write a report like:

```json
{
  "schema_version": "robotwin.tabletop_visual_review.v0",
  "prompt": "<prompt>",
  "status": "pass",
  "reviewer": {
    "type": "codex_visual_reference",
    "reviewed_at": "YYYY-MM-DD",
    "source_image": "previews/<run_name>/observer_camera.png"
  },
  "checks": [
    {"name": "object_presence", "status": "pass", "evidence": "..."},
    {"name": "spatial_relation", "status": "pass", "evidence": "..."},
    {"name": "table_contact_and_penetration", "status": "pass", "evidence": "..."},
    {"name": "prompt_match", "status": "pass", "evidence": "..."}
  ],
  "limitations": [
    "Codex visual reference review is not a reproducible external VLM API call."
  ]
}
```

## 6. Full Pass

```bash
python harness/run_scene_generation_pipeline.py \
  --prompt "<prompt>" \
  --master-catalog asset_catalogs/robotwin_tabletop_assets_master.json \
  --case-name "<short_prompt>" \
  --robotwin-root ~/RoboTwin \
  --model-provider codex_reference \
  --out-dir runs/<run_name>_visual_pass \
  --run-smoke \
  --visual-review-report previews/<run_name>/visual_review_codex_reference.json
```

Expected status: `pass`.

## 7. Close The Loop

Update:

- asset catalog if a qpos/loader/scale/static fix was needed
- relevant skill lessons if a new pitfall was discovered
- small preview files if useful

Do not commit full `runs/` or large simulator data.
