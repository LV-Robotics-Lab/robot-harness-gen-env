# Open-X-Sim four-workflow implementation

Open-X-Sim is the environment compiler and conformance layer inside AgenticSim.
It does not replace RoboVerse or MetaSim. MetaSim remains a supported backend.

## Shared contract

All workflows produce `agenticsim.environment_package.v1` with:

- `EnvSpec`: units, axes, gravity, workspace, robots, sensors, regions, objects.
- `AssetBundle`: provenance plus portable and backend representations.
- `TaskSpec`: reset, action, observation, plan, success, termination.
- `AnchorSpec`: decoded media evidence and uncertain semantic constraints.
- `ConformanceReport`: explicit L0-L4 pass/fail/not-evaluated checks.

USD, MJCF, URDF, RoboTwin models, and MetaSim config files are backend
representations. None is the canonical IR.

## Commands

```bash
python scripts/openxsim.py --output artifacts/openxsim text2env \
  --instruction "Move the red block onto the blue zone." \
  --backends isaacsim,mujoco,sapien,metasim,robotwin --strict

python scripts/openxsim.py --output artifacts/openxsim anchor2env \
  --instruction "Move the red block onto the blue zone." \
  --media reference.mp4 --annotations anchor_constraints.json \
  --sample-count 8 --backends isaacsim,sapien

python scripts/openxsim.py --output artifacts/openxsim asset-scout \
  --query "ceramic tabletop mug" --asset-id ceramic_mug \
  --github-discovery --github-repository-query "public robot assets" \
  --smoke-backends isaacsim,mujoco,sapien,metasim

python scripts/openxsim.py --output artifacts/openxsim transfer \
  --source configs/openxsim/existing_settle.xml --source-backend mujoco \
  --backends isaacsim,sapien,metasim --strict

python scripts/openxsim.py robotwin-evidence \
  --package artifacts/openxsim/place_cola_can_in_basket/text2env/environment_package.json \
  --task-program artifacts/openxsim/place_cola_can_in_basket/text2env/compiled/robotwin/task_program.json \
  --rollout-report artifacts/openxsim/place_cola_can_in_basket/text2env/compiled/robotwin/runtime/rollout_report.json \
  --minimum-video-frames 24
```

`asset-scout` also supports one or more public GitHub repositories through
`--github owner/repository`. `--github-discovery` first searches public
repositories and then searches their concrete trees, so a caller does not need
to provide `owner/repository`. Downloads are atomic and bounded, and every
result records its source page, URL, provider, stated license, SHA-256, byte
size, and conversion products. A saved `search_evidence.json` is accepted as a
catalog for offline retry after API or network failure.

For OBJ inputs, AssetCompiler preserves the downloaded source and emits a
centered, one-metre canonical visual mesh, a default material when source
materials are unavailable, an independent convex box collision proxy, and an
`asset_validation.json` with geometry, scale, material, collision, and
articulation checks. Runtime import is still required; generated files alone do
not constitute a pass.

## Native import coverage

`transfer` can read:

- MJCF XML, including an embedded AgenticSim task contract when present.
- `agenticsim.sapien_scene.v1` JSON without an AgenticSim sidecar.
- `agenticsim.metasim_scenario.v1` JSON without executing generated Python.
- the explicit Cube/external-Xform subset of ASCII Isaac USDA; task semantics
  remain unbound unless a canonical sidecar is available.
- any backend artifact with a digest-bound sibling `compile_manifest.json`.

Explicit `--source-backend` forces native parsing even when a sidecar exists.
This prevents a migration test from silently recovering the original package
instead of exercising the source importer.

## Conformance boundary

- L0: the target artifact exists and passes backend-specific static validation.
- L1: units, axes, object/asset/region IDs, static flags, and nominal poses match.
- L2: both runtimes reset and step, action and success evaluators are bound, the
  task-contract hashes match, and canonical observation keys agree.
- L3: both runtimes emit the same object/contact trajectory within tolerance.
- L4: both policy evaluations have enough episodes and their success rates agree
  within the declared tolerance.

Missing runtime, trajectory, or policy evidence is `not_evaluated`; a compiled
file cannot promote itself to L2-L4.

The native `configs/openxsim/existing_settle.xml` fixture is deliberately
minimal. It validates a real existing-environment import and zero-action physics
transfer. The same serialized scene can be imported from the SAPIEN JSON source
adapter and replayed in MuJoCo and Isaac Sim. This is an L3 rigid-body/common-
subset result, not articulated control, camera fidelity, RoboTwin policy
transfer, or L4 statistical behavior.

The RoboTwin backend compiles a hash-bound placement and task program. Its
runtime evidence adapter rejects endpoint-only videos, package/placement hash
mismatches, changed task bindings, and success checks that do not evaluate every
`TaskSpec.success` condition. Endpoint states are intentionally not promoted to
an L3 trajectory.
