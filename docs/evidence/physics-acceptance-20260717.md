# Physics Acceptance Evidence: 2026-07-17

## Scope

[KNOWN] This note covers the bounded Robot Harness `/gen-env` implementation on
RoboTwin assets. It does not claim arbitrary text-to-3D generation or policy
execution.

[KNOWN] Runtime host: `jingxiang-b850m-c` (`100.64.0.6`), NVIDIA GeForce RTX
5090, driver `580.159.03`. RoboTwin source commit:
`c3ddfa8b97d5519efa828b075999bd0006778e5e`. Asset catalog SHA-256:
`8ba15e8460d7c7b095bd4061b5bc0ce2a4fea4b7bb1293f8e3665ba27b4c5dbe`.

## Required Gates

- [KNOWN] Solve the complete source footprint inside the declared support
  surface or target-local container interior.
- [KNOWN] Run 300 SAPIEN settle steps followed by a 120-step contact window.
- [KNOWN] Count a pair only when a reported contact point has separation at or
  below 1 mm. Broad-phase pairs with positive clearance remain in raw evidence
  but do not count as support.
- [KNOWN] Require at least 80% contact with the declared support target and zero
  active contact with undeclared support targets.
- [KNOWN] Require no penetration, no dropped object, no still-moving final
  state, no containment failure, no more than 20 mm resolved translation error,
  and no more than 5 degrees resolved rotation error.
- [KNOWN] Video evidence uses 120 sequential frames at 12 fps. MP4 output uses
  H.264/yuv420p and `+faststart` for browser streaming.

## Matrix Results

| Prompt | Seeds | Pass | Min target contact | Max unexpected contact | Worst translation | Worst rotation | Geometry gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `Place a can on top of a plate.` | 10 | 10 | 1.000 | 0.000 | 4.08 mm | 1.043 deg | min margin 8.55 mm |
| `Put a cup inside a basket.` | 10 | 10 | 1.000 | 0.000 | 2.645 mm | 0.975 deg | 10/10 contained |

[COMPUTED] Across the 20 forced runtime replays, penetration count, dropped
count, and still-moving count were all zero. Each matrix retained 10 unique
resolved-scene hashes and complete per-seed evidence.

## Demo Result

[COMPUTED] Demo job `438c46c081da4033`, seed 45, completed compile, RoboTwin
runtime, and local Qwen rendered-scene critic with return code 0 for every
stage. Its can had target-contact fraction 1.0, unexpected-contact fraction
0.0, support margin 11.33 mm, resolved translation error 4.02 mm, and resolved
rotation error 0.258 degrees.

[COMPUTED] The generated Demo MP4 contains exactly 120 decodable frames, 100
unique frames, and 10 seconds of video. Browser validation reached
`readyState=4`, advanced `currentTime`, returned one successful HTTP 206 media
response, and had no horizontal overflow at 1440 px or 390 px viewport width.

## Expected Rejection

[COMPUTED] `Place a red block on top of a plate.` is rejected by the bounded
solver. The block footprint cannot fit the measured stable plate surface while
preserving the configured support margin. This prevents the previous class of
edge or physically unsupported placements from becoming runtime candidates.

## Reproduce

```bash
python script/run_100_seed_acceptance.py \
  --prompt "Place a can on top of a plate." \
  --seed-start 0 \
  --seed-count 10 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/support_matrix_v2/can_plate \
  --report data/support_matrix_v2/can_plate_report.json \
  --minimum-pass-rate 1.0 \
  --runtime \
  --robotwin-root /path/to/RoboTwin \
  --precheck-steps 0 \
  --settle-steps 300 \
  --contact-window-steps 120 \
  --video-seeds 0 \
  --video-frames 120 \
  --no-resume
```

Run the same command with `--prompt "Put a cup inside a basket."` and a separate
output root for the containment matrix.

## Interpretation

[INFERRED] The evidence closes the reported can-on-plate edge-placement failure
for the measured RoboTwin plate and the tested bounded parser surface. It does
not establish correctness for arbitrary unseen meshes, simulators, or natural
language outside the supported grammar.
