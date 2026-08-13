# Prompt Matrix Physics Evidence: 2026-07-19

## Scope

[KNOWN] This run tests the bounded Robot Harness `/gen-env` compiler against
RoboTwin assets. It does not claim unrestricted language parsing, arbitrary
text-to-3D generation, or transfer to another simulator.

[KNOWN] Runtime host: `jingxiang-b850m-c` (`100.64.0.6`), NVIDIA GeForce RTX
5090, driver `580.159.03`. RoboTwin source commit:
`c3ddfa8b97d5519efa828b075999bd0006778e5e`.

## Acceptance Configuration

- [KNOWN] Three deterministic compile seeds: `7`, `31`, and `73`.
- [KNOWN] One real SAPIEN replay for each positive prompt; expected solver
  rejections are checked at all three seeds.
- [KNOWN] Each replay runs 900 simulation steps and samples support contact over
  the final 120 steps.
- [KNOWN] Each MP4 contains 120 frames: 119 consecutive release frames and one
  final settled frame. At least 30 frames must be pixel-distinct.
- [KNOWN] Dynamic objects are accepted by final support, containment, and
  relative-relation gates. Fixed objects retain exact resolved-pose gates.

## Results

| Case | Expected | Compile seeds | SAPIEN | Distinct frames |
| --- | --- | ---: | ---: | ---: |
| Red block on plate | pass | 3/3 | pass | 100/120 |
| Can on plate | pass | 3/3 | pass | 100/120 |
| Apple in basket | pass | 3/3 | pass | 120/120 |
| Cup in basket | pass | 3/3 | pass | 100/120 |
| Red can left of basket | pass | 3/3 | pass | 100/120 |
| Blue cup in front of and near block | pass | 3/3 | pass | 100/120 |
| Apple right of plate | pass | 3/3 | pass | 120/120 |
| Apple right of plate in infeasible back region | reject | 3/3 | not run | n/a |
| Half-open cabinet | pass | 3/3 | pass | 100/120 |
| Chinese red block on plate | pass | 3/3 | pass | 100/120 |
| Chinese apple in basket | pass | 3/3 | pass | 120/120 |

[COMPUTED] Aggregate result: `33/33` expected compile outcomes and `10/10`
positive SAPIEN replays. No accepted replay failed penetration, dropped-object,
settling, visibility, support-target, containment, or spatial-relation gates.

## Block Adaptation

[COMPUTED] The catalog `004_fluted-block` measures
`92.275 x 90.626 x 65.293 mm`. Its native footprint cannot satisfy the measured
plate support margin, and its catalog collision mesh was unstable in direct
SAPIEN replay.

[COMPUTED] The compiler generated a deterministic primitive proxy at uniform
scale `0.597`, with dimensions `55.088 x 54.104 x 38.980 mm`. The final replay
reported target-contact fraction `1.0`, support margin `8.861 mm`, zero
penetration, a settled state, and 881 visible pixels. The resolved package
records the source asset/model IDs, source dimensions, scale factor, generated
file hashes, and adaptation reasons.

[KNOWN] This adaptation is not arbitrary asset generation. Automatic scaling
is restricted to block/cube categories with a runtime-qualified primitive
proxy path. Other incompatible categories are rejected.

## Video And Settling

[COMPUTED] At 300 steps, both English and Chinese apple-in-basket replays still
moved `1.213 mm` and rotated `4.633 deg` during the final 30-step window. At 900
steps, both values reached zero while contact fraction remained `1.0`, the apple
remained contained, and penetration count remained zero.

[COMPUTED] Uniformly sampling the full 900-step interval produced only 15
pixel-distinct frames for early-settling scenes. The accepted capture policy
therefore records the release densely and appends the final settled frame; all
ten final videos contain at least 100 distinct frames.

## Evidence Identity

- [KNOWN] Prompt matrix SHA-256:
  `7af1f2d0207abdf558494c267954b75294aa268d7453d0ec323b4a3e5a7dc743`.
- [KNOWN] Canonical asset catalog SHA-256:
  `8ba15e8460d7c7b095bd4061b5bc0ce2a4fea4b7bb1293f8e3665ba27b4c5dbe`.
- [KNOWN] Structured report file SHA-256:
  `47c0bb75fe594d50cfdd6b393be3dc4d1b9a8fc83eeb7e397a4d6a98f3a87cd9`.
- [KNOWN] Structured report:
  [`prompt-matrix-runtime-20260719.json`](prompt-matrix-runtime-20260719.json).

## Reproduce

```bash
python script/run_prompt_matrix.py \
  --matrix tests/fixtures/prompt_matrix.json \
  --asset-catalog /path/to/asset_catalog.json \
  --generated-objects-root /path/to/RoboTwin/assets/objects \
  --out-root data/prompt_matrix_runtime \
  --report data/prompt_matrix_runtime/report.json \
  --runtime \
  --robotwin-root /path/to/RoboTwin \
  --settle-steps 900 \
  --contact-window-steps 120 \
  --video-frames 120 \
  --fps 12
```
