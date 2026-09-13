# Replay adaptive-settle timeline evidence — 2026-08-31

This note records a targeted real RoboTwin/SAPIEN smoke for one bug: after adaptive settling extends
the simulation, the observer video must end at the real final physical step. It does **not** qualify a
formal 900-step replay, asset portability, or publication eligibility. No asset catalog was supplied,
so this run is not evidence for the real-asset-files gate.

## Reproduction

Use the `robotwin-5090` interpreter, the repository as `REPO_ROOT`, and the canonical RoboTwin checkout
as `ROBOTWIN_ROOT`:

```bash
PYTHONPATH="$REPO_ROOT" PYTHONUNBUFFERED=1 \
/home/jingxiang/miniconda3/envs/robotwin-5090/bin/python \
"$REPO_ROOT/script/run_scene_runtime.py" \
  --robotwin-root "$ROBOTWIN_ROOT" \
  --resolved-scene "$REPO_ROOT/self_improving/studies/ASPIRE/artifacts/acceptance_100_can_plate/scenes/seed_000000/place_a_can_on_top_of_a_plate_fe0b76e316/resolved_scene.json" \
  --out-dir /tmp/replay-timeline-adaptive \
  --settle-steps 30 \
  --settle-converge-max 300 \
  --contact-window-steps 30 \
  --video-frames 3 \
  --fps 3
```

The fixed-horizon control uses the same command with a different output directory and
`--settle-converge-max 0`.

## Observed result

| Measurement | Fixed horizon | Adaptive extension |
| --- | ---: | ---: |
| Base steps | 30 | 30 |
| Extra steps | 0 | 30 |
| Actual steps | 30 | 60 |
| Evidence / decoded MP4 frames | 3 / 3 | 3 / 3 |
| Sample indices | `0, 1, 29` | `0, 1, 59` |
| Can still moving | yes | no |
| Validator | fail (1 gate) | pass (0 fail, 0 not-run) |

The adaptive run kept the requested three-frame budget, changed the tail index to `59 = 60 - 1`, and
produced a different `observer_end.png` hash from the step-29 control. The validator's new
`observer_video_timeline` check passed. Full artifact sizes and hashes are frozen in
`replay-timeline-20260831.json`; the bulk PNG/MP4/runtime directories remain uncommitted.

Relevant runtime versions were Python 3.10.20, SAPIEN 3.0.0b1, NumPy 1.26.4, and Torch
2.11.0+cu128. The structured evidence is bound to resolved semantic digest
`61c03c8f9d3c2d7df3930a24fbde3fcb64d4ad621f4191d407e425369cc2d0b0`.
