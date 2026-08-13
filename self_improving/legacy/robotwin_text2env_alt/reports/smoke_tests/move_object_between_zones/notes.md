# Smoke Test: move_object_between_zones

Date: 2026-06-24 Asia/Shanghai

## Result

Status: PASS

The generated RoboTwin2 task loads, plans, executes, saves instructions, writes HDF5 data, and exports a video.

## Command

```bash
cd ~/RoboTwin
source ~/miniconda3/etc/profile.d/conda.sh
conda activate RoboTwin
timeout 900 bash collect_data.sh move_object_between_zones demo_smoke 0 2>&1 | tee install_logs/smoke_collect_move_object_between_zones_step5_pass.log
```

## Remote Artifacts

```text
~/RoboTwin/envs/move_object_between_zones.py
~/RoboTwin/description/task_instruction/move_object_between_zones.json
~/RoboTwin/install_logs/smoke_collect_move_object_between_zones_step5_pass.log
~/RoboTwin/data/move_object_between_zones/demo_smoke/data/episode0.hdf5
~/RoboTwin/data/move_object_between_zones/demo_smoke/video/episode0.mp4
~/RoboTwin/data/move_object_between_zones/demo_smoke/instructions/episode0.json
~/RoboTwin/data/move_object_between_zones/demo_smoke/scene_info.json
~/RoboTwin/data/move_object_between_zones/demo_smoke/seed.txt
```

## Local Preview Artifacts

```text
robotwin_text2env_smoke/move_object_between_zones/episode0.mp4
robotwin_text2env_smoke/move_object_between_zones/episode0.json
robotwin_text2env_smoke/move_object_between_zones/scene_info.json
robotwin_text2env_smoke/move_object_between_zones/seed.txt
robotwin_text2env_smoke/move_object_between_zones/frame_start.jpg
robotwin_text2env_smoke/move_object_between_zones/frame_mid.jpg
robotwin_text2env_smoke/move_object_between_zones/frame_end.jpg
```

The HDF5 file is about 348 MB, so it was left on the 5090 instead of copied locally.

## Verification

- Single-seed debug before full smoke: all four generated moves returned `plan_success=True`, and final `check_success=True`.
- Full smoke generated one episode with seed `0`.
- Video: `2293` frames, `320x240`, `30 FPS`.
- HDF5 groups: `endpose`, `joint_action`, `observation`, `pointcloud`.
- HDF5 key shapes checked:
  - `joint_action/vector`: `(2293, 14)`, `float64`
  - `observation/head_camera/rgb`: `(2293,)`
  - `endpose/left_endpose`: `(2293, 7)`, `float64`
- Visual spot check:
  - Start frame: green block starts on the left side, bowl is on the right.
  - End frame: green block is on the green target zone, bowl remains on the right.

## Fixes Needed Before Pass

- Changed `blue_bowl.initial_pose.qpos` from `[1, 0, 0, 0]` to `[0.5, 0.5, 0.5, 0.5]` to avoid unstable bowl initialization.
- Moved the target zone closer to the tabletop centerline so the selected arm can place successfully.
- Moved the bowl farther from the target zone while keeping it as a visible distractor.
- Removed generated `add_prohibit_area()` calls for region marker boxes, so target zones are not treated as planner keep-out obstacles.

## Known Warnings

RoboTwin printed many `svulkan2 OIDN Error: invalid handle` messages during video/data export. They did not prevent HDF5, instruction JSON, or MP4 generation.
