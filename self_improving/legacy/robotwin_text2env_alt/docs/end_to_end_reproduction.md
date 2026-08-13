# End-To-End Reproduction

This note describes the one-command path from a natural-language tabletop task to RoboTwin2 files and optional simulator evidence.

## What The E2E Runner Does

`scripts/reproduce_text2env_e2e.py` runs these stages:

1. Designer / Critic / Orchestrator agent flow from natural language.
2. Text2Env schema and local consistency check.
3. RoboTwin2 task scaffold generation.
4. Optional deployment into a local `~/RoboTwin` checkout.
5. Optional RoboTwin simulator smoke/data dry run.
6. Optional ACT policy train/eval hook.

The runner writes every prompt, intermediate JSON, command log, generated task file, and final summary under `--run-dir`.

## Fast Pipeline Test Without An LLM

Use the mock backend first to confirm the repository plumbing:

```bash
python scripts/reproduce_text2env_e2e.py \
  --backend mock \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/e2e/mock_move_object_between_zones
```

Expected key outputs:

```text
runs/e2e/mock_move_object_between_zones/final_text2env.json
runs/e2e/mock_move_object_between_zones/final_text2env.validation.json
runs/e2e/mock_move_object_between_zones/generated_robotwin_task/envs/move_object_between_zones.py
runs/e2e/mock_move_object_between_zones/generated_robotwin_task/description/task_instruction/move_object_between_zones.json
runs/e2e/mock_move_object_between_zones/e2e_summary.json
```

The mock backend does not prove model quality. It proves the local pipeline is runnable.

## Natural-Language Agent Run With Qwen

Serve an instruct model with an OpenAI-compatible API. One common option is vLLM:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct \
  --host 0.0.0.0 \
  --port 8000
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

The same flow also works with Ollama, LM Studio, or another OpenAI-compatible server if the model follows JSON instructions reliably.

## Deploy And Smoke-Test In RoboTwin

First confirm RoboTwin exists:

```bash
test -d ~/RoboTwin/envs
test -d ~/RoboTwin/description/task_instruction
```

Run the full agent-to-simulator path:

```bash
python scripts/reproduce_text2env_e2e.py \
  --backend openai-compatible \
  --api-base http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-14B-Instruct \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/e2e/qwen_smoke_move_object_between_zones \
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

The command log is saved to:

```text
runs/e2e/qwen_smoke_move_object_between_zones/logs/04_simulator_smoke.log
```

## Optional Policy Hook

After smoke succeeds, add `--run-policy-hook`:

```bash
python scripts/reproduce_text2env_e2e.py \
  --backend openai-compatible \
  --api-base http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-14B-Instruct \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/e2e/qwen_policy_move_object_between_zones \
  --robotwin-root ~/RoboTwin \
  --deploy \
  --run-smoke \
  --run-policy-hook \
  --gpu-id 0
```

This hook is intentionally a smoke-level check: collect two expert episodes, preprocess for ACT, train for one epoch, then start eval from the produced checkpoint.

## Reading Pass/Fail

Open:

```text
<run-dir>/e2e_summary.json
```

`status: pass` means every requested stage completed. If a stage fails, the same file records the failing step and the corresponding log path.
