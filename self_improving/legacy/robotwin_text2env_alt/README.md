# RoboTwin2 Text2Env Demo

Minimal SceneSmith-lite pipeline for turning a natural-language tabletop task into a RoboTwin2 task.

Smoke-tested task:

```text
Move the green block from the left zone to the right zone without moving the blue bowl.
```

## What This Repo Proves

This demo shows an end-to-end path:

```text
natural language
-> Text2Env JSON
-> RoboTwin2 task program
-> RoboTwin smoke/data collection
-> optional ACT policy hook
```

The passing RoboTwin task is `move_object_between_zones`.

## Key Files

```text
examples/tabletop_tasks/move_object_between_zones.json
generated/robotwin_tasks/move_object_between_zones/
scripts/reproduce_text2env_e2e.py
scripts/run_text2env_agents.py
scripts/generate_robotwin_task.py
reports/smoke_tests/move_object_between_zones/notes.md
robotwin_text2env_smoke/move_object_between_zones/
```

RoboTwin itself is not vendored here. Install or clone RoboTwin separately, usually at:

```text
~/RoboTwin
```

## Quick Reproduce

First check that RoboTwin exists:

```bash
test -d ~/RoboTwin/envs
test -d ~/RoboTwin/description/task_instruction
```

Run the full lightweight pipeline with the mock agent:

```bash
python scripts/reproduce_text2env_e2e.py \
  --backend mock \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/e2e/mock_move_object_between_zones \
  --robotwin-root ~/RoboTwin \
  --deploy \
  --run-smoke \
  --gpu-id 0
```

Expected RoboTwin outputs:

```text
~/RoboTwin/data/move_object_between_zones/demo_smoke/data/episode0.hdf5
~/RoboTwin/data/move_object_between_zones/demo_smoke/video/episode0.mp4
~/RoboTwin/data/move_object_between_zones/demo_smoke/instructions/episode0.json
~/RoboTwin/data/move_object_between_zones/demo_smoke/scene_info.json
~/RoboTwin/data/move_object_between_zones/demo_smoke/seed.txt
```

The HDF5 file is not tracked because it is large.

## Run With A Real LLM

Serve an OpenAI-compatible model, for example Qwen with vLLM:

```bash
vllm serve Qwen/Qwen2.5-14B-Instruct --host 0.0.0.0 --port 8000
```

Then run:

```bash
python scripts/reproduce_text2env_e2e.py \
  --backend openai-compatible \
  --api-base http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-14B-Instruct \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/e2e/qwen_move_object_between_zones
```

For GPU memory reasons, it is usually better to generate the JSON first, stop vLLM, then run RoboTwin smoke.

## Useful Docs

- Full E2E reproduction: `docs/end_to_end_reproduction.md`
- Open-source agent reproduction: `docs/open_source_agent_reproduction.md`
- Policy hook notes: `docs/policy_hook_note.md`
- Smoke report: `reports/smoke_tests/move_object_between_zones/notes.md`
- Project plan and future asset-adapter direction: `robotwin2_text2env_scenesmith_lite_plan.md`

## Current Limitations

- v0 uses RoboTwin existing assets or simple geometry; generated assets are planned but not implemented yet.
- Task A, `Put the red cup into the drawer`, is still blocked by articulated/container asset handling.
- Region grounding needs improvement: phrases such as `from the left zone` should become explicit initial-state constraints.
