# Codex Handoff: RoboTwin Tabletop Scene Generation

Date: 2026-07-06

This document lets another Codex account continue the project without relying on private chat history or account memory.

## Project Goal

The project generates tabletop scene/background assets for RoboTwin from natural language. It is inspired by SceneSmith-style scene construction and RoboTwin `code_gen` feedback loops, but it does not generate task `play_once()` policies as the main output.

The target output is a reusable RoboTwin scene module:

```text
generated_scenes/<case_name>_scene.py
```

with a loader such as:

```python
def load_scene(task, placement_spec=None):
    ...
```

Downstream RoboTwin tasks can import that scene and then define their own `play_once()` and `check_success()`.

## Current Architecture

```text
Natural-language scene prompt
-> Asset Grounding Agent
-> Prompt Case Catalog
-> Designer Agent creates TabletopPlacementSpec
-> Static Validator preflight
-> Scene Code Generator writes RoboTwin scene module
-> RoboTwin smoke render
-> Scene Critic reviews static + smoke + visual evidence
-> Orchestrator accepts, repairs, or reports blocker
```

Important code:

```text
generate_scene/asset_discovery.py
generate_scene/asset_grounding.py
generate_scene/model_providers.py
generate_scene/gpt_agent.py
generate_scene/moonshot_client.py
generate_scene/openai_client.py
generate_scene/observation_agent.py
generate_scene/schemas.py
generate_scene/scene_codegen.py
generate_scene/scene_critic.py
generate_scene/tools.py
generate_scene/run_scene_generation_pipeline.py
generate_scene/run_scene_batch.py
generate_scene/run_robotwin_placement_smoke.py
```

Prompt templates:

```text
generate_scene/prompts/asset_grounding_agent.md
generate_scene/prompts/designer_agent.md
generate_scene/prompts/observation_vlm_agent.md
generate_scene/prompts/orchestrator_agent.md
generate_scene/prompts/static_critic_agent.md
generate_scene/prompts/visual_repair_agent.md
```

## Environment

Known 5090 server paths:

```text
zhengye = /data/sdb/zhengye
project = /data/sdb/zhengye/robotwin-text2env-demo
RoboTwin = /data/sdb/zhengye/RoboTwin
RoboTwin python = /data/sdb/zhengye/miniconda3/envs/RoboTwin/bin/python
```

For another machine, adapt these paths. A common convention in docs is:

```text
RoboTwin = ~/RoboTwin
```

## API Keys

Do not commit real keys.

Supported providers:

```text
Moonshot/Kimi: generate_scene/moonshot_client.py
OpenAI: generate_scene/openai_client.py
```

Preferred setup:

```bash
export MOONSHOT_API_KEY="..."
export OPENAI_API_KEY="..."
```

Local-only alternative:

```text
generate_scene/local_config.py
```

This file is ignored by Git and must stay uncommitted.

## One-Command Example

On the 5090 server:

```bash
cd /data/sdb/zhengye/robotwin-text2env-demo

python3 generate_scene/run_scene_generation_pipeline.py \
  --prompt "a remote control next to the notebook" \
  --case-name remote_control_notebook_kimi \
  --robotwin-root /data/sdb/zhengye/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/remote_control_notebook_kimi \
  --discover-assets-from-robotwin \
  --model-provider moonshot \
  --run-smoke \
  --visual-review-mode moonshot \
  --visual-repair-attempts 1 \
  --python-executable /data/sdb/zhengye/miniconda3/envs/RoboTwin/bin/python
```

Batch diversity example:

```bash
python3 generate_scene/run_scene_batch.py \
  --prompt "a laptop is on the right side of a knife" \
  --batch-name laptop_right_of_knife \
  --num-scenes 5 \
  --max-candidates 8 \
  --robotwin-root /data/sdb/zhengye/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/batches/laptop_right_of_knife \
  --discover-assets-from-robotwin \
  --model-provider moonshot \
  --run-smoke \
  --visual-review-mode moonshot \
  --visual-repair-attempts 1 \
  --python-executable /data/sdb/zhengye/miniconda3/envs/RoboTwin/bin/python
```

## Current Evidence And Examples

Accepted or useful examples:

```text
generated_scenes/apple_plate_scene.py
generated_scenes/remote_control_notebook_flat_candidate_scene.py
previews/apple_plate_scene_module_smoke/
previews/remote_control_notebook_flat_candidate/
```

The remote-control/notebook run established an important rule: everyday flat objects should usually lie flat on the table. Different in-plane rotations are valid scene diversity unless the prompt requires a specific orientation.

## Recent Blocker: Laptop And Knife Batch

Prompt:

```text
a laptop is on the right side of a knife
```

What happened:

- Kimi/Moonshot could ground the assets and produce candidate placements.
- Static validation and scene codegen could run.
- Smoke render stalled before images were produced.
- The smoke process printed:

```text
kinematics_fused_cu not found, JIT compiling...
```

Interpretation:

The failure is likely an environment/prewarm issue from RoboTwin/CuRobo first-run JIT compilation on a busy server, not necessarily a semantic failure in Kimi or the placement.

Recommended next step:

1. Run a single minimal RoboTwin smoke/prewarm with a long timeout and no parallel batch.
2. Confirm `~/.cache/torch_extensions/.../kinematics_fused_cu.so` exists and imports cleanly.
3. Re-run the laptop/knife batch with a normal per-candidate smoke timeout.

Known asset-specific rules:

- `034_knife`: ordinary background scenes should place it flat and static.
- `015_laptop`: articulated URDF asset; load through the SAPIEN URDF path with fixed root.

## Rules Another Codex Should Preserve

- Do not push unless the user explicitly asks.
- Clean temporary test runs after use.
- Do not commit real API keys or dashboard tokens.
- Do not commit full `runs/`, HDF5 files, datasets, checkpoints, or RoboTwin assets.
- Always interpret spatial language from the robot or dual-arm first-person frame unless otherwise stated.
- Never mark a scene as PASS from smoke alone. Require visual/VLM/human review.
- Keep project direction focused on scene/background generation. Do not drift back into generating RoboTwin task `play_once()` as the main objective.

## Suggested First Prompt For A New Codex Account

```text
Please read AGENTS.md, docs/codex_handoff_20260706.md, and .agents/skills/generate-robotwin-tabletop-scene/SKILL.md. Then continue the RoboTwin tabletop scene generation project. Start by checking the current Git status and verifying that no secrets are tracked.
```
