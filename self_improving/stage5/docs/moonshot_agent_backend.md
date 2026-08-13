# External Agent Backends: Moonshot/Kimi and OpenAI

更新时间：2026-07-03

这个 backend 用外部 LLM/VLM API 替代 Codex 手工扮演的 agent，让 scene generation 形成外部模型闭环。当前支持：

```text
moonshot / kimi     -> Moonshot/Kimi chat completions endpoint
openai              -> OpenAI Responses API endpoint
```

## 1. API key

不要把 API key 写进代码或 git。

Moonshot/Kimi:

```bash
export MOONSHOT_API_KEY="your_key"
```

OpenAI:

```bash
export OPENAI_API_KEY="your_key"
export OPENAI_TEXT_MODEL="gpt-5.5"
export OPENAI_VISION_MODEL="gpt-5.5"
```

也可以在本机创建一个不会提交到 git 的文件：

```python
# generate_scene/local_config.py
MOONSHOT_API_KEY = "your_key"
OPENAI_API_KEY = "your_key"
```

可以复制模板：

```bash
cp generate_scene/local_config.example.py generate_scene/local_config.py
```

可选配置：

```bash
export MOONSHOT_BASE_URL="https://api.moonshot.cn/v1"
export MOONSHOT_TEXT_MODEL="kimi-k2.5"
export MOONSHOT_VISION_MODEL="kimi-k2.5"
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_TEXT_MODEL="gpt-5.5"
export OPENAI_VISION_MODEL="gpt-5.5"
```

默认 endpoint 是 `https://api.moonshot.cn/v1`。如果你使用的是兼容域名，也可以把 `MOONSHOT_BASE_URL` 改成对应地址。

## 2. 代码文件

```text
generate_scene/moonshot_client.py      # OpenAI-compatible Moonshot HTTP client
generate_scene/openai_client.py        # OpenAI Responses API HTTP client
generate_scene/gpt_agent.py            # text agents: asset grounding / designer / critic / orchestrator
generate_scene/observation_agent.py    # visual reviewer: smoke preview -> visual review JSON
generate_scene/scene_critic.py         # unified static/smoke/visual Scene Critic report
generate_scene/prompts/*.md            # text/VLM agent output rules
```

## 3. Text Agent 闭环

启用 Kimi 做语义分解和 placement agent：

```bash
python generate_scene/run_scene_generation_pipeline.py \
  --prompt "an apple and a plate on the table" \
  --case-name apple_plate_kimi \
  --robotwin-root ~/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/apple_plate_kimi \
  --discover-assets-from-robotwin \
  --model-provider moonshot
```

此时 Moonshot/Kimi 接管：

```text
asset grounding
Designer placement
Scene Critic repair loop when repair is needed
```

启用 OpenAI 做同样的 text agent：

```bash
python generate_scene/run_scene_generation_pipeline.py \
  --prompt "an apple and a plate on the table" \
  --case-name apple_plate_openai \
  --robotwin-root ~/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/apple_plate_openai \
  --discover-assets-from-robotwin \
  --model-provider openai
```

`--discover-assets-from-robotwin` 会先扫描：

```text
~/RoboTwin/assets/objects
```

并在 run 目录生成：

```text
robotwin_discovered_asset_catalog.json
prompt_case_catalog.json
```

这样 prompt 里的物体不再只依赖手写的小样例 catalog，而是可以从 RoboTwin 的真实资产库中检索。

## 4. Vision Agent 闭环

启用 Kimi vision 读取 smoke 图片：

```bash
python generate_scene/run_scene_generation_pipeline.py \
  --prompt "an apple and a plate on the table" \
  --case-name apple_plate_kimi \
  --robotwin-root ~/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/apple_plate_kimi \
  --discover-assets-from-robotwin \
  --model-provider moonshot \
  --run-smoke \
  --visual-review-mode moonshot \
  --visual-repair-attempts 1
```

启用 OpenAI vision 读取 smoke 图片：

```bash
python generate_scene/run_scene_generation_pipeline.py \
  --prompt "an apple and a plate on the table" \
  --case-name apple_plate_openai \
  --robotwin-root ~/RoboTwin \
  --generated-scene-dir generated_scenes \
  --out-dir runs/apple_plate_openai \
  --discover-assets-from-robotwin \
  --model-provider openai \
  --run-smoke \
  --visual-review-mode openai \
  --visual-repair-attempts 1
```

也可以单独对已有 smoke preview 做视觉判定：

```bash
python generate_scene/observation_agent.py \
  --smoke-dir runs/apple_plate_kimi/smoke \
  --prompt "an apple and a plate on the table" \
  --placement runs/apple_plate_kimi/final_placement.json \
  --asset-grounding runs/apple_plate_kimi/asset_grounding.json \
  --model-provider openai \
  --out runs/apple_plate_kimi/visual_review.json
```

## 5. 输出

Kimi text agent 会输出：

```text
asset_grounding.json
designer_initial_placement.json
critic_review.json
final_placement.json
```

Kimi vision agent 会输出：

```text
visual_review.json
```

`visual_review.json` 的核心字段：

```text
status: pass | fail_visual_review | repair_required
checks: object identity / table contact / penetration / floating / orientation / spatial relation
issues: visual problems
repair_suggestions: concrete repair targets
```

## 6. 当前限制

现在已经能把外部 text/VLM agent 接进流程。当前 pipeline 会：

```text
generate draft -> static preflight -> codegen -> smoke -> visual review -> Scene Critic
```

如果设置 `--visual-repair-attempts N`，当 Scene Critic 返回 failure 或 repair_required 时，pipeline 会：

```text
scene_critic_review.json
-> Orchestrator repair
-> repaired placement
-> static validation
-> scene codegen
-> RoboTwin smoke rerun
-> visual review rerun
-> Scene Critic rerun
```

当前仍建议从 `N=1` 开始，避免一次任务产生太多 API 调用和仿真输出。
