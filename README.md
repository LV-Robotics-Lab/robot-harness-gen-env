# Robot Harness /gen-env

`/gen-env` compiles bounded natural-language requests into deterministic,
RoboTwin-loadable scene packages and validates them in SAPIEN before they can
enter the Robot Harness command loop.

```text
text
  -> typed SceneSpec
  -> RoboTwin asset grounding
  -> target-local support and containment solve
  -> hash-bound ResolvedSceneSpec package
  -> RoboTwin/SAPIEN replay
  -> contact, stability, containment, visibility, and video gates
```

This repository is the `/gen-env` subsystem. It does not implement robot task
policies, data collection, training, evaluation, or simulator transfer.

## What Is Enforced

- Complete source footprints must fit explicit support surfaces with a minimum
  margin. Outer object AABBs are not treated as stable support.
- Nested source objects are dynamic and must contact their declared target for
  at least 80% of the final sampling window.
- Contact candidates count only when at least one point is within 1 mm of the
  collision surface. Broad-phase pairs with positive clearance do not count.
- Nested objects must have zero active contact with undeclared support targets,
  including the table below a plate or container.
- Container placement uses target-local interior geometry and an explicit
  collision-floor offset.
- Fixed objects must remain within 20 mm and 5 degrees of the resolved pose.
  Dynamic objects are checked against final contact, containment, and spatial
  relations after settling rather than their released spawn pose.
- Objects must settle, remain visible, avoid penetration, and stay inside the
  declared workspace.
- A 120-frame runtime video must contain at least 30 distinct frames. The
  capture keeps 119 consecutive release frames plus the final settled frame.
- Static, contact-free acceptance is limited to fixed objects placed directly on
  the table. It cannot satisfy stacking or containment.

The bundled RoboTwin overrides currently include a measured 100 mm stable
surface for `003_plate`, an 8 mm support margin, and a measured 12 mm interior
floor offset for `110_basket` model 1.

## Supported Request Surface

The parser is deliberately bounded and bilingual. Current examples include:

```text
Place a can on top of a plate.
Put a cup inside a basket.
Place a half-open cabinet on the table.
Place a red can to the left of a plastic basket near the center.
把杯子放进篮子里。
```

Catalog misses can optionally produce deterministic geometric proxies. This is
not unrestricted text-to-3D generation.

For the runtime-unstable `004_fluted-block`, the compiler emits a deterministic
primitive proxy with source lineage. It preserves native dimensions on the
table and applies a uniformly scaled footprint only when a nested support
surface requires it. Automatic compatibility scaling is currently restricted
to block/cube categories that have passed real SAPIEN replay.

## Install And Test

Python 3.11 is the tested local version.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev,demo]'
.venv/bin/pytest -q
```

The test suite uses committed fixtures and does not require a RoboTwin checkout.
Real runtime validation requires RoboTwin and its SAPIEN environment.

## Build A Real Asset Catalog

```bash
python -m scene_gen.catalog \
  --robotwin-root /path/to/RoboTwin \
  --overrides scene_gen/asset_overrides.yml \
  --source-commit "$(git -C /path/to/RoboTwin rev-parse HEAD)" \
  --out data/scene_gen/asset_catalog.json \
  --missing-out data/scene_gen/missing_assets.json
```

The catalog records exact source files, dimensions, stable orientations,
support surfaces, container interiors, articulation limits, and availability.

## Compile `/gen-env`

```bash
python script/generate_scene.py \
  --prompt "Place a can on top of a plate." \
  --seed 42 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/generated_scenes
```

Each accepted package contains the request, typed scene spec, fully grounded
resolved scene, replay entrypoint, static validation report, and SHA-256 manifest.

## Run Real Physics

Use the Python interpreter from the RoboTwin environment:

```bash
python script/run_scene_runtime.py \
  --robotwin-root /path/to/RoboTwin \
  --resolved-scene data/generated_scenes/<scene-id>/resolved_scene.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-dir data/runtime/<scene-id> \
  --precheck-steps 0 \
  --settle-steps 900 \
  --contact-window-steps 120 \
  --video-frames 120 \
  --fps 12
```

Starting with zero precheck steps records the physical release and prevents an
unstable initial state from being hidden before evidence capture. The 900-step
window is required by the current apple-in-basket asset pair; it still had
measurable motion at 300 steps.

## Prompt Matrix

```bash
python script/run_prompt_matrix.py \
  --matrix tests/fixtures/prompt_matrix.json \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --generated-objects-root /path/to/RoboTwin/assets/objects \
  --out-root data/prompt_matrix \
  --report data/prompt_matrix/report.json \
  --runtime \
  --robotwin-root /path/to/RoboTwin
```

The committed matrix has 11 English/Chinese cases and three seeds. Runtime is
run for the first seed of each positive case unless `--runtime-all-seeds` is
set. Expected solver rejections remain part of the aggregate pass/fail result.

## Acceptance Batches

```bash
python script/run_100_seed_acceptance.py \
  --prompt "Place a can on top of a plate." \
  --seed-count 100 \
  --asset-catalog data/scene_gen/asset_catalog.json \
  --out-root data/acceptance/can_plate \
  --report data/acceptance/can_plate.json
```

Add `--runtime --robotwin-root /path/to/RoboTwin` for SAPIEN replay. Only the
seeds listed by `--video-seeds` retain MP4 files; every runtime seed retains
structured physical evidence.

## Validated Physics Evidence

[COMPUTED] The 2026-07-17 RTX 5090 acceptance run passed 20/20 forced SAPIEN
replays: 10 seeds for can-on-plate and 10 seeds for cup-in-basket. No run used
resume data.

- Can-on-plate: minimum target-contact fraction 1.0, maximum unexpected-contact
  fraction 0.0, minimum final support margin 8.55 mm, maximum resolved
  translation error 4.08 mm, and maximum rotation error 1.043 degrees.
- Cup-in-basket: all 10 final states remained contained, minimum target-contact
  fraction 1.0, maximum unexpected-contact fraction 0.0, maximum resolved
  translation error 2.645 mm, and maximum rotation error 0.975 degrees.
- Both matrices reported zero penetration points, moving final states, and
  dropped nested objects.
- The native `004_fluted-block` footprint cannot satisfy the plate support
  margin. The current compiler derives a `0.597x` stable primitive proxy with
  dimensions `55.088 x 54.104 x 38.980 mm`; its real replay retained an
  `8.861 mm` support margin and continuous target contact.

[COMPUTED] The 2026-07-19 prompt matrix passed 33/33 compile outcomes and 10/10
real SAPIEN replays on the RTX 5090 host. Every 120-frame MP4 contained at least
100 distinct frames. The matrix includes stacking, containment, table support,
left/right/front/near relations, articulation state, bilingual equivalence,
derived scale adaptation, and an infeasible-region expected rejection.

The current commands, thresholds, report hashes, and per-case results are in
[the prompt-matrix acceptance note](docs/evidence/prompt-matrix-20260719.md) and
[its structured report](docs/evidence/prompt-matrix-runtime-20260719.json).
The earlier 20-seed baseline remains in
[the 2026-07-17 physics note](docs/evidence/physics-acceptance-20260717.md).

## Browser Demo

```bash
export ROBOTWIN_ROOT=/path/to/RoboTwin
export ROBOTWIN_PYTHON=/path/to/robotwin/python
export SCENE_ASSET_CATALOG=$PWD/data/scene_gen/asset_catalog.json
export SCENE_DEMO_JOBS_ROOT=$PWD/data/demo_jobs

python -m demo.app --host 0.0.0.0 --port 8765
```

The demo queues GPU work, accepts text and a seed, and exposes only registered
screenshots, video, manifests, and validation evidence from each job.

The current lab-network deployment is available at
[`http://100.64.0.6:8765`](http://100.64.0.6:8765).

## Optional Rendered Critic

```bash
pip install -e '.[vlm]'
python script/run_rendered_critic.py \
  --resolved-scene data/generated_scenes/<scene-id>/resolved_scene.json \
  --image data/runtime/<scene-id>/preview_head.png \
  --image data/runtime/<scene-id>/preview_world_left.png \
  --image data/runtime/<scene-id>/preview_world_right.png \
  --out data/runtime/<scene-id>/rendered_critic.json
```

The rendered critic checks visible semantics. Deterministic physics gates remain
authoritative for contacts, support, containment, drift, and articulation state.

## Layout

```text
scene_gen/          schemas, catalog, grounding, solver, replay, validators
script/             compile, runtime, batch acceptance, rendered critic
demo/               Flask API and browser interface
tests/fixtures/     self-contained catalog and prompt fixtures
tests/              parser, solver, validator, critic, demo, and attack tests
```

## Provenance

The work started from
[`yezheng04/robotwin-text2env-demo`](https://github.com/yezheng04/robotwin-text2env-demo)
and was narrowed into the Robot Harness `/gen-env` subsystem. RoboTwin assets are
referenced by path and are not redistributed here.

## License

Apache-2.0. See `LICENSE`.
