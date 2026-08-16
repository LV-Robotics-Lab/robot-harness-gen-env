# Asset provenance

- Asset: YCB tomato soup can
- Asset ID: `921_robolab_tomato_soup_can`
- Source repository: https://github.com/NVLabs/RoboLab
- Source commit: `97bc1e766300a8c95656067ecb81e267e7e11823`
- Source file: `assets/objects/ycb/tomato_soup_can.usd`
- Source file SHA-256: `0cfa0c4ede7c269d4c7e4ecaed43e95bb8ab788ce9aa8b4504f811ffb7f9e3a3`
- Source texture: `assets/objects/ycb/textures/obj_000004.png`
- Source texture SHA-256: `33183a894eb75ae04ae5188a3ae689fe8d06ec6b9f9624da1bf853c2a25e70d2`
- License file: `assets/objects/ycb/LICENSE`
- License file SHA-256: `46ddc25f12283209928f70e03fbb7d659db7408fba3275fe9526fffd60e7d5ed`
- License: MIT; RoboLab states that the USD derives from YCB-Video and preserves
  the YCB-Video/BOP-YCB MIT notice.

## Source measurement

- Method: `UsdGeom.XformCache` world-space probe before conversion.
- Stage frame: Z-up, `metersPerUnit=1.0`.
- Mesh prim: `/tomato_soup_can/obj_000004_Mesh`.
- Geometry: 8,404 vertices, 15,728 triangular faces, 8,404 normals, 8,404 UVs.
- World-space dimensions: `[0.0678539947, 0.0677499995, 0.1020370051]` m.
- Source physics: mass `0.4499999881` kg, static friction `2.0`, dynamic friction
  `2.0`, restitution `0.1000000015`.
- Inertia: not authored as a usable tensor; SAPIEN derives it from the collision
  geometry. No center of mass or inertia tensor is claimed here.

## Conversion and backend mapping

- Tool: `self_improving/onboarding/yeyuxuan/tools/migrate_robolab_asset.py`.
- Environment: isolated Python 3.11 under `local_data/envs/`.
- Changes: source world transform applied, bottom aligned to `z=0`, texture embedded
  in `visual/base0.glb`, and one convex hull written to `collision/base0.glb`.
- Scale: `[1.0, 1.0, 1.0]`; scale is baked into the SAPIEN GLB representation.
- Origin: source-centered USD to bottom-aligned RoboTwin GLB.
- Collision: one convex hull, 658 vertices and 1,312 triangles. Lid seams and small
  surface details are simplified.
- Stable pose: identity quaternion `[1, 0, 0, 0]`, measured against the real SAPIEN
  replay `921-seed42-identity-pass`.

The initial X90 frame hypothesis was rejected by real replay
`921-seed42-x90-failed`: after 900 steps the object was still moving, with a
15.22 mm / 26.78 degree late-window change. That failed evidence remains under
`local_data/921_robolab_tomato_soup_can/runtime/seed42/` and was not overwritten.

## Validation

- Prompt: `Place a tomato soup can on the table.`; seed 42.
- Resolved scene SHA-256: `d395658f280e2512c52dfffe4bff22ebf702db81a5dab6e3e56acfffacc9abef`.
- Real RoboTwin/SAPIEN result: PASS after 900 steps.
- Translation drift: 1.226 mm; rotation drift: 0.673 degrees.
- Late-window translation/rotation: 0 m / 0 degrees; settled.
- Support contact fraction: 1.0; unexpected contact fraction: 0.0.
