# RoboTwin Code Gen Patterns

RoboTwin's `/code_gen` folder is a useful reference for agentic generation, validation, and repair loops. Reuse the patterns below for tabletop scene placement, but do not convert this project back into task-program generation.

## What Code Gen Does

`code_gen` turns a RoboTwin task description into executable task code:

```text
task_info.py
  task_name, task_description, actor_list

prompt.py
  coordinate conventions, available RoboTwin APIs, usage examples

task_generation.py
  LLM writes envs_gen/gpt_<task>.py::play_once()

test_gen_code.py
  simulator run, success_rate, error categories

task_generation_mm.py + observation_agent.py
  save step images, run VLM observation, feed visual failure back into repair
```

This project should apply the same loop to scene placement:

```text
placement prompt + prompt case catalog
-> Designer writes TabletopPlacementSpec
-> static validation
-> RoboTwin smoke render
-> visual/VLM review
-> Orchestrator repairs pose/qpos/loader/static/catalog defaults
-> rerun until pass or blocker
```

## Patterns To Reuse

Use a structured case input, similar to `task_info.py`:

- `prompt`
- `direction_frame`
- `selected_asset_ids`
- `asset_catalog`
- `visual_review_criteria`
- optional `known_asset_risks`

Keep simulator API and coordinate rules explicit, similar to `prompt.py`:

- RoboTwin world frame: x is right, y is front, z is up.
- Project semantic frame: natural-language directions use the robot / dual-arm first-person viewpoint unless the prompt states another frame.
- Placement outputs must stay in `TabletopPlacementSpec`, not free-form code.

Use simulator feedback, similar to `test_gen_code.py`:

- static validation status
- smoke return code
- smoke report
- preview image/video existence
- explicit visual/VLM review status
- categorized failure reason

Use visual feedback, similar to `task_generation_mm.py` and `observation_agent.py`:

- Save initial and final preview images when possible.
- Ask human/Codex visual reference/external VLM to check object identity, robot-centric spatial relations, orientation, table contact, penetration, occlusion, and prompt match.
- Feed visual failure text back to the Orchestrator as repair instructions.
- Record the attempted repair and rerun result.

## What Not To Reuse

- Do not generate `envs_gen/gpt_<task>.py` or `play_once()` for the scene placement MVP.
- Do not treat task success rate as the scene pass criterion.
- Do not expose API keys in repo files.
- Do not write generated simulator data, full `runs/`, HDF5 files, or large images into Git.
- Do not let a VLM pass replace static validation or smoke validation; it is one gate in the chain.

## Recommended Repair Record

Each failed visual/smoke loop should leave a compact record:

```json
{
  "attempt": 1,
  "failure_source": "visual_review",
  "failure_summary": "basket penetrates the table and appears sideways",
  "repair_target": "asset_catalogs/robotwin_tabletop_assets_master.json",
  "repair_action": "update 110_basket placement_defaults.qpos",
  "rerun_status": "pending"
}
```

Store durable fixes in the master catalog or prompt case. Store general lessons in the skill references.
