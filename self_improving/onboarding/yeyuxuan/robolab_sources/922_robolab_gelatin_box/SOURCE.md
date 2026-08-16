# Asset provenance

- Asset: YCB gelatin box
- Asset ID: `922_robolab_gelatin_box`
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: `97bc1e766300a8c95656067ecb81e267e7e11823`
- Source file: `assets/objects/ycb/jello.usd`
- Source file SHA-256: `12ed6c7cb45ae423970d340dd89e3b6e8f13622667855cd0f1bffa9e6283131d`
- Source texture: `assets/objects/ycb/textures/obj_000008.png`
- Source texture SHA-256: `5fd0de9bc41b3c77545d116febf7a72875c8b430e35efe974ab601342a942b74`
- License file: `assets/objects/ycb/LICENSE`
- License file SHA-256: `46ddc25f12283209928f70e03fbb7d659db7408fba3275fe9526fffd60e7d5ed`
- License: MIT; RoboLab states that the USD derives from YCB-Video and preserves
  the YCB-Video/BOP-YCB MIT notice.

## Source measurement

- Method: `UsdGeom.XformCache` world-space probe before conversion.
- Default prim `/jello`; one mesh `/jello/obj_000008_Mesh`.
- Stage frame: Z-up, `metersPerUnit=1.0`, identity world transform.
- Geometry: 8,268 vertices, 15,728 triangular faces, 8,268 vertex normals and UVs.
- World-space dimensions: `[0.0893550068, 0.1011089981, 0.0301200002]` m.
- Source physics: mass `0.2000000030` kg, static/dynamic friction `2.0/2.0`,
  restitution `0.1000000015`.
- Center of mass and inertia tensor are not usable authored facts; SAPIEN derives
  inertia from the collision geometry.

## Conversion and backend mapping

- Tool: `self_improving/onboarding/yeyuxuan/tools/migrate_robolab_asset.py`.
- Environment: isolated Python 3.11 under `local_data/envs/`.
- Source Z-up mesh is bottom-aligned to `z=0`; scale remains `[1, 1, 1]` and is
  baked into the SAPIEN Y-up GLB representation.
- Visual GLB embeds the 4096 x 4096 RGB texture.
- Collision: one watertight convex hull, 360 vertices and 716 triangles; cardboard
  seams and small surface details are simplified.
- Stable pose: identity quaternion `[1, 0, 0, 0]`, confirmed by real replay
  `922-seed42-identity-pass`.

## Validation

- Prompt: `Place a gelatin box on the table.`; seed 42.
- Resolved scene SHA-256: `542890572d7dd07f5849fbc126f43bfdb4284c6cb652067b84e9b0d082b58906`.
- Real RoboTwin/SAPIEN result: PASS after 900 steps; no failed runs preceded it.
- Translation drift: 1.459 mm; rotation drift: 0.101 degrees.
- Late-window translation/rotation: 0 m / 0 degrees; settled.
- Support contact fraction: 1.0 across 120 samples; unexpected contact: 0.0.
- Visible pixels: 1,402; penetration count: 0; video frames: 120 total / 100 unique.
- Runtime report SHA-256: `5aac9e799e4d114ca7813e48babcaa602675fa1d4e43230876f39a5de8549e1d`.
- Runtime evidence SHA-256: `1e64ad3c3758efd7774658a3f57cd486d339f4771e569a8d90a28b29efd88f5e`.
- Runtime video SHA-256: `9526b4e2f718b921c74879b0d98840c101a20bc8b7a00ed10bcc17a4c2077b4f`.
