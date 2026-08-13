# Scene Generation Workflow

更新时间：2026-07-03

本项目当前目标：从自然语言生成 RoboTwin tabletop scene/background，而不是生成 RoboTwin task `play_once()`。

当前链路已经重构为 render-in-the-loop：

```text
Designer 先给出 Draft PlacementSpec
-> Static Validator 做 preflight gate
-> RoboTwin Scene Codegen
-> One-frame / short smoke render
-> Scene Critic 统一看 static + smoke + visual evidence
-> Orchestrator accept / repair / redesign
```

`Static Validator` 保留，但它不再是独立 agent；它是 `Scene Critic` 的 rule-based preflight 子模块。

## Main Command

```bash
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

## Batch Diversity Command

Generate five distinct accepted scenes for one prompt:

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

Output:

```text
runs/batches/<batch>/batch_summary.json
runs/batches/<batch>/diversity_context.json
runs/batches/<batch>/scene_00_candidate_00/
runs/batches/<batch>/scene_01_candidate_01/
...
```

Each accepted scene must preserve the prompt semantics while varying valid x/y positions, spacing, and in-plane yaw.

## Step Table

| Step | Input | Code | Output |
| --- | --- | --- | --- |
| 1. Asset discovery | `--robotwin-root` | `generate_scene/asset_discovery.py` | `runs/<case>/robotwin_discovered_asset_catalog.json` |
| 2. Asset grounding | prompt + discovered catalog | `generate_scene/gpt_agent.py`, `prompts/asset_grounding_agent.md` | `asset_grounding.json`, `prompt_case_catalog.json` |
| 3. Designer draft placement | prompt case catalog | `generate_scene/gpt_agent.py`, `prompts/designer_agent.md` | `designer_initial_placement.json`, `attempt_0_placement.json` |
| 4. Static preflight | draft placement + catalog | `generate_scene/schemas.py` | `attempt_0_static_validation.json`, `static_validation_initial.json` |
| 5. Draft scene codegen | `attempt_N_placement.json` | `generate_scene/scene_codegen.py` | `generated_scenes/<case>_scene.py` |
| 6. RoboTwin smoke render | generated scene module | `generate_scene/run_robotwin_placement_smoke.py` | `smoke_attempt_N/head_camera.png`, `observer_camera.png`, `smoke_report.json` |
| 7. Visual review | smoke images | `generate_scene/observation_agent.py`, `prompts/observation_vlm_agent.md` | `attempt_N_visual_review.json` |
| 8. Scene Critic | placement + static + smoke + visual | `generate_scene/scene_critic.py` | `attempt_N_scene_critic_review.json`, `scene_critic_review.json` |
| 9. Orchestrator repair loop | Scene Critic failure | `generate_scene/gpt_agent.py`, `prompts/visual_repair_agent.md` | `attempt_N+1_placement.json`, rerun codegen/smoke/review |
| 10. Final accepted scene | Scene Critic pass/pending | `generate_scene/run_scene_generation_pipeline.py` | `final_placement.json`, final scene module, `scene_generation_summary.json` |

## Core Files

```text
generate_scene/run_scene_generation_pipeline.py   # pipeline entry
generate_scene/run_scene_batch.py                 # batch diversity runner
generate_scene/asset_discovery.py                 # scan RoboTwin assets/objects
generate_scene/gpt_agent.py                       # text agents
generate_scene/observation_agent.py               # VLM review agent
generate_scene/scene_critic.py                    # unified Scene Critic report
generate_scene/moonshot_client.py                 # OpenAI-compatible Moonshot/Kimi client
generate_scene/openai_client.py                   # OpenAI Responses API client
generate_scene/scene_codegen.py                   # PlacementSpec -> scene module
generate_scene/schemas.py                         # Static Validator / preflight checks
generate_scene/run_robotwin_placement_smoke.py    # RoboTwin render evidence
```

## Prompt Specs

```text
generate_scene/prompts/asset_grounding_agent.md
generate_scene/prompts/designer_agent.md
generate_scene/prompts/observation_vlm_agent.md
generate_scene/prompts/visual_repair_agent.md
```

These markdown files define how external LLM/VLM models should behave.

## Orientation Lesson

Thin everyday objects such as:

```text
notebook, book, phone, remote control, cards
```

should normally be placed flat on the tabletop with the broad face down. A scene where such objects stand upright on a narrow edge should fail visual review unless the prompt explicitly requests upright/standing/leaning placement.

Once an object is physically flat, different in-plane yaw angles are acceptable scene diversity unless the prompt specifies a facing direction or alignment.

## Outputs To Review

For each run:

```text
runs/<case>/scene_generation_summary.json
runs/<case>/asset_grounding.json
runs/<case>/designer_initial_placement.json
runs/<case>/attempt_0_placement.json
runs/<case>/attempt_0_static_validation.json
runs/<case>/attempt_0_visual_review.json
runs/<case>/attempt_0_scene_critic_review.json
runs/<case>/final_placement.json
runs/<case>/scene_critic_review.json
runs/<case>/visual_review.json
runs/<case>/smoke/head_camera.png
runs/<case>/smoke/observer_camera.png
generated_scenes/<case>_scene.py
```

`final_placement.json` now means: the PlacementSpec accepted by the render-in-the-loop Scene Critic, not merely the static/orchestrator result.

For GitHub, keep only small curated examples under `previews/` and generated scene examples under `generated_scenes/`. Do not commit full `runs/`, HDF5 data, checkpoints, logs, or real API keys.
