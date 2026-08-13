# SceneAgent Reproduction Commands

## Runtime

- Host: `jingxiang@100.64.0.6`
- Workspace: `/home/jingxiang/workspace/alchedata-self-improving-agents`
- RoboTwin environment: `/home/jingxiang/miniconda3/envs/robotwin-5090`
- RoboTwin root: `external/RoboTwin`

Run commands from the workspace root unless a command changes directory explicitly.

## Contract And Workspace Verification

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -q \
  tests/test_selection2env_contract.py \
  tests/test_official_rollout_video_recorder.py \
  tests/test_pose_conditioned_trajectory_policy.py \
  tests/test_report_delivery.py

PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify_workspace.py
```

Upstream selection/catalog tests:

```bash
cd external/robotwin-text2env-demo
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest -q tests/test_catalog_sources.py
```

## Fresh `/collect` Acceptance Runs

Apple/plate:

```bash
/home/jingxiang/miniconda3/bin/conda run -n robotwin-5090 --no-capture-output \
  python scripts/run_collect_dry_run.py \
  --robotwin-root external/RoboTwin \
  --placement runs/scene_task_decoupling/shared_apple_plate_scene.json \
  --out-dir runs/final_acceptance_20260715/collect_apple_plate \
  --task-id task_apple_plate \
  --steps 96 --capture-every 24 --fps 12
```

Laptop/knife:

```bash
/home/jingxiang/miniconda3/bin/conda run -n robotwin-5090 --no-capture-output \
  python scripts/run_collect_dry_run.py \
  --robotwin-root external/RoboTwin \
  --placement runs/probe_static_laptop_knife/final_placement.json \
  --out-dir runs/final_acceptance_20260715/collect_laptop_knife \
  --task-id task_laptop_knife \
  --steps 96 --capture-every 24 --fps 12
```

Each run records 97 consecutive simulator-step frames in its observer MP4 and keeps five observer/head PNG sample pairs for audit.

## Same Scene, Two Tasks

Primary task:

```bash
/home/jingxiang/miniconda3/bin/conda run -n robotwin-5090 --no-capture-output \
  python scripts/run_generated_selection2env_rollout_probe.py \
  --robotwin-root external/RoboTwin \
  --task-program-input artifacts/task_program_inputs/task_apple_plate.json \
  --out-dir runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate \
  --seed 0 --fps 12 --capture-stride 4 --min-video-frames 24
```

Alternate task over the same placement bytes:

```bash
/home/jingxiang/miniconda3/bin/conda run -n robotwin-5090 --no-capture-output \
  python scripts/run_generated_selection2env_rollout_probe.py \
  --robotwin-root external/RoboTwin \
  --task-program-input artifacts/task_program_inputs/task_apple_plate_to_left_front.json \
  --out-dir runs/final_acceptance_20260715/scene_task_decoupling/apple_to_left_front \
  --seed 0 --fps 12 --capture-stride 4 --min-video-frames 24
```

Rebuild the strict evidence record:

```bash
python3 scripts/build_scene_task_decoupling_report.py \
  --primary-task artifacts/task_program_inputs/task_apple_plate.json \
  --alternate-task artifacts/task_program_inputs/task_apple_plate_to_left_front.json \
  --primary-rollout runs/final_acceptance_20260715/scene_task_decoupling/apple_on_plate/rollout_report.json \
  --alternate-rollout runs/final_acceptance_20260715/scene_task_decoupling/apple_to_left_front/rollout_report.json \
  --out artifacts/scene_task_decoupling/apple_plate_two_tasks.json
```

## Public-Base Patch Replay

`external_sources.lock.json` locks the public base, patch SHA-256, and expected result tree. A clean replay must produce tree `68e84128bbf5f8702f918024f6ca0c6e056a478f`.

```bash
git clone https://github.com/yezheng04/robotwin-text2env-demo.git /tmp/robotwin-text2env-replay
git -C /tmp/robotwin-text2env-replay checkout 78bef41c136a0f4bf2c35ebec2793f3f4ad7dd75
git -C /tmp/robotwin-text2env-replay apply \
  "$PWD/vendor/patches/robotwin-text2env-selection2env.patch"
git -C /tmp/robotwin-text2env-replay add -A
git -C /tmp/robotwin-text2env-replay write-tree
```

## Report Verification And Delivery

```bash
python3 scripts/update_sceneagent_report_assets.py
python3 scripts/report_delivery.py --write-manifest sceneagent
python3 scripts/report_delivery.py \
  --sync-downloads --verify-downloads \
  --downloads-root /Users/boris/Downloads
```

Browser QA is run with `scripts/qa_static_reports.py --report sceneagent` in an environment containing Playwright and Chrome.
