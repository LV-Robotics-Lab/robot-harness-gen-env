# Reproducing The Natural-Language Agent Flow

Updated: 2026-06-26 Asia/Shanghai

This document explains how another user can reproduce the SceneSmith-lite agent flow from a natural-language tabletop task.

The repository now supports two levels of reproduction:

1. Deterministic demo reproduction: use the checked-in Text2Env JSON and generated RoboTwin2 task program.
2. Agent-flow reproduction: run Designer -> Critic -> Orchestrator with an open-source LLM served through an OpenAI-compatible endpoint.

## What The Three Agents Do

```text
Natural-language task
  -> Designer LLM
  -> draft Text2Env JSON
  -> Critic LLM
  -> audit report
  -> Orchestrator LLM
  -> final Text2Env JSON
  -> local schema/v0 validator
  -> RoboTwin2 task scaffold generator
```

Prompt files:

```text
prompts/text2env_designer.md
prompts/text2env_critic.md
prompts/text2env_orchestrator.md
```

Runner:

```text
scripts/run_text2env_agents.py
```

## Recommended Open-Source Model Setup

The easiest backend is an OpenAI-compatible local server.

Good first models:

- `Qwen/Qwen2.5-14B-Instruct` for a single high-memory GPU.
- `Qwen/Qwen2.5-7B-Instruct` when memory is tighter.
- Larger Qwen/DeepSeek/Llama instruct models if a multi-GPU server is available.

This task is mostly structured JSON generation and robotics constraint checking. A VLM is not required for the first Text2Env JSON loop. Use a VLM later for screenshot/video critique.

## Option A: vLLM

Example server:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct \
  --host 0.0.0.0 \
  --port 8000
```

Run the three-agent flow:

```bash
python scripts/run_text2env_agents.py \
  --backend openai-compatible \
  --api-base http://localhost:8000/v1 \
  --model Qwen/Qwen2.5-14B-Instruct \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/text2env_agents/move_object_between_zones_qwen14b \
  --out examples/tabletop_tasks/generated_from_qwen.json
```

Then validate and generate the RoboTwin task:

```bash
python scripts/generate_text2env.py check examples/tabletop_tasks/generated_from_qwen.json
python scripts/generate_robotwin_task.py generate examples/tabletop_tasks/generated_from_qwen.json
```

## Option B: Ollama

Ollama also exposes an OpenAI-compatible endpoint at `http://localhost:11434/v1`.

Example:

```bash
ollama pull qwen2.5:14b
ollama serve
```

In another terminal:

```bash
python scripts/run_text2env_agents.py \
  --backend openai-compatible \
  --api-base http://localhost:11434/v1 \
  --model qwen2.5:14b \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/text2env_agents/move_object_between_zones_ollama \
  --out examples/tabletop_tasks/generated_from_ollama.json
```

## Plumbing Test Without A Model

Use the mock backend only to check that the local runner writes the expected files. It does not test model quality.

```bash
python scripts/run_text2env_agents.py \
  --backend mock \
  --instruction "Move the green block from the left zone to the right zone without moving the blue bowl." \
  --run-dir runs/text2env_agents/mock \
  --out examples/tabletop_tasks/generated_mock.json
```

Expected run artifacts:

```text
runs/text2env_agents/<run_name>/01_designer_prompt.json
runs/text2env_agents/<run_name>/02_designer_draft.json
runs/text2env_agents/<run_name>/03_critic_prompt.json
runs/text2env_agents/<run_name>/04_critic_report.json
runs/text2env_agents/<run_name>/05_orchestrator_prompt.json
runs/text2env_agents/<run_name>/06_orchestrator_result.json
examples/tabletop_tasks/generated_*.json
examples/tabletop_tasks/generated_*.validation.json
```

Generated run files under `runs/` are ignored by git.

## When The Model Output Fails

Common failures:

- JSON is wrapped in prose despite prompt instructions.
- `task_name` is not snake_case.
- A plan references a missing object or region.
- A success predicate uses an unsupported region/object id.
- The model marks an articulated task as `ready_for_scaffold`.
- It invents assets that are not in RoboTwin.

The runner writes every intermediate prompt/output so the failure can be repaired by editing the prompt, lowering temperature, changing the model, or manually patching the JSON.

## Suggested Reproduction Contract

For a real reproduced agent run, save:

- model name and serving backend,
- exact command,
- `runs/text2env_agents/<run_name>/`,
- final Text2Env JSON,
- validation report,
- generated RoboTwin task scaffold,
- smoke/data-collection logs.
