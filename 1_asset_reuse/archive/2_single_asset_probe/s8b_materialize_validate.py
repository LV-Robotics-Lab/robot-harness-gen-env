#!/usr/bin/env python3
"""B line step 2 (env-gen-yuxin env): materialize the converted mug as a
RoboTwin-layout asset dir (301_cup), generate model_data0.json from measured
geometry, run a SAPIEN settle check, and register the AssetBundle.

RoboTwin layout produced under --library-dir/301_cup/:
  visual/base0.glb  collision/base0.glb  model_data0.json
"""

import argparse
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np
import sapien
import trimesh

parser = argparse.ArgumentParser()
parser.add_argument("--glb", required=True, help="converted mug_visual.glb")
parser.add_argument(
    "--source-dir", required=True, help="source mirror with SOURCE_MANIFEST.json"
)
parser.add_argument("--library-dir", required=True, help="data/asset_library root")
parser.add_argument("--out", required=True, help="results dir for validation evidence")
args = parser.parse_args()

glb = Path(args.glb)
lib = Path(args.library_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
asset_dir = lib / "301_cup"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- measure geometry; ensure RoboTwin convention (mesh +Y up) ----
scene = trimesh.load(str(glb))
lo, hi = scene.bounds
size = [float(b - a) for a, b in zip(lo, hi)]
# Rotation decided by the SOURCE USD's declared upAxis (recorded in the
# manifest by s8a): the converter preserves coordinates, RoboTwin GLBs are Y-up.
manifest_early = json.loads(
    (Path(args.source_dir) / "SOURCE_MANIFEST.json").read_text()
)
src_up = manifest_early.get("up_axis", "Y")
rotated = False
if src_up == "Z":
    m = trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
    scene = scene.copy()
    scene.apply_transform(m)
    lo, hi = scene.bounds
    size = [float(b - a) for a, b in zip(lo, hi)]
    rotated = True
# Normalize origin to RoboTwin convention: bottom center (origin_on_table).
cx = (lo[0] + hi[0]) / 2
cz = (lo[2] + hi[2]) / 2
tm = trimesh.transformations.translation_matrix([-cx, -float(lo[1]), -cz])
scene = scene.copy() if not rotated else scene
scene.apply_transform(tm)
lo, hi = scene.bounds
size = [float(b2 - a2) for a2, b2 in zip(lo, hi)]
print(f"mug bbox_m={['%.4f' % s for s in size]} rotated_z2y={rotated} origin=bottom-center")
if not (0.03 < size[1] < 0.3):
    print(f"FAIL s8b: implausible mug height {size[1]}")
    sys.exit(1)

# ---- materialize RoboTwin layout ----
if asset_dir.exists():
    shutil.rmtree(asset_dir)
(asset_dir / "visual").mkdir(parents=True)
(asset_dir / "collision").mkdir(parents=True)
vis_path = asset_dir / "visual" / "base0.glb"
col_path = asset_dir / "collision" / "base0.glb"
scene.export(str(vis_path))
shutil.copy(vis_path, col_path)  # convex hull of visual is fine for placement use

center = [float((a + b) / 2) for a, b in zip(lo, hi)]
model_data = {
    "center": center,
    "extents": size,
    "scale": [1.0, 1.0, 1.0],
    "transform_matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
    "target_pose": [],
    "contact_points_pose": [],
    "functional_matrix": [],
    "orientation_point": [],
    "contact_points_group": [],
    "contact_points_mask": [],
    "target_point_discription": [],
    "contact_points_discription": [],
    "functional_point_discription": [],
    "orientation_point_discription": [],
    "stable": True,
}
(asset_dir / "model_data0.json").write_text(json.dumps(model_data, indent=2))
print(f"materialized {asset_dir}")

# ---- SAPIEN settle check (direct load, fast feedback before e2e) ----
sc = sapien.Scene()
sc.set_timestep(1 / 100)
sc.add_ground(0)
sc.set_ambient_light([0.5, 0.5, 0.5])
sc.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])
b = sc.create_actor_builder()
b.add_multiple_convex_collisions_from_file(filename=str(col_path))
b.add_visual_from_file(filename=str(vis_path))
actor = b.build(name="mug")
z0 = 0.005  # origin normalized to bottom center
actor.set_pose(sapien.Pose(p=[0, 0, z0], q=[math.sqrt(0.5), math.sqrt(0.5), 0, 0]))
poses = []
for i in range(300):
    sc.step()
    if i % 50 == 0 or i == 299:
        poses.append(actor.get_pose())
final = poses[-1]
late_drift = float(np.linalg.norm(np.array(final.p) - np.array(poses[-2].p)))


def quat_rotate(q, v):
    w, x, y, z = q
    qv = np.array([x, y, z])
    t = 2 * np.cross(qv, v)
    return np.array(v) + w * t + np.cross(qv, t)


up = quat_rotate(list(final.q), [0.0, 1.0, 0.0])
tilt = float(np.degrees(np.arccos(np.clip(up[2], -1, 1))))
cam = sc.add_camera("cam", 640, 480, np.deg2rad(60), 0.01, 10.0)
eye = np.array([0.22, -0.22, 0.18])
f = np.array([0, 0, size[1] / 2]) - eye
f /= np.linalg.norm(f)
left = np.cross([0, 0, 1], f)
left /= np.linalg.norm(left)
upv = np.cross(f, left)
mrot = np.column_stack([f, left, upv])
w = math.sqrt(max(0.0, 1 + mrot[0, 0] + mrot[1, 1] + mrot[2, 2])) / 2
q = [
    w,
    (mrot[2, 1] - mrot[1, 2]) / (4 * w),
    (mrot[0, 2] - mrot[2, 0]) / (4 * w),
    (mrot[1, 0] - mrot[0, 1]) / (4 * w),
]
try:
    cam.entity.set_pose(sapien.Pose(p=eye.tolist(), q=q))
except AttributeError:
    cam.set_local_pose(sapien.Pose(p=eye.tolist(), q=q))
sc.update_render()
cam.take_picture()
img = (np.clip(cam.get_picture("Color"), 0, 1) * 255).astype(np.uint8)[:, :, :3]
from PIL import Image

Image.fromarray(img).save(out / "sapien_mug.png")

checks = {
    "bbox_m": size,
    "rotated_z2y": rotated,
    "settled_late_drift_m": late_drift,
    "settled": late_drift < 0.002,
    "final_z_m": float(final.p[2]),
    "tilt_deg": tilt,
    "upright": tilt < 15.0,
    "screenshot_ok": bool(img.std() > 1),
}
checks["status"] = (
    "pass"
    if (checks["settled"] and checks["upright"] and checks["screenshot_ok"])
    else "fail"
)

# ---- AssetBundle registration ----
manifest = json.loads((Path(args.source_dir) / "SOURCE_MANIFEST.json").read_text())
bundle = {
    "asset_id": "external_301_cup_m0",
    "category": "cup",
    "representations": [
        {
            "format": "glb",
            "uri": str(vis_path),
            "backend": "sapien",
            "role": "visual",
            "sha256": sha256(vis_path),
            "size_bytes": vis_path.stat().st_size,
            "metadata": {
                "derived_from": "025_mug.usd via omni.kit.asset_converter",
                "rotated_z2y": rotated,
            },
        },
        {
            "format": "glb",
            "uri": str(col_path),
            "backend": "sapien",
            "role": "collision",
            "sha256": sha256(col_path),
            "size_bytes": col_path.stat().st_size,
            "metadata": {
                "note": "copy of visual; convex decomposition happens at load"
            },
        },
        {
            "format": "usd",
            "uri": str(Path(args.source_dir) / "025_mug.usd"),
            "backend": "isaacsim",
            "role": "visual_and_collision",
            "sha256": manifest["files"]["025_mug.usd"],
            "size_bytes": 0,
            "metadata": {
                "origin_url": "Isaac Assets 5.1 /Isaac/Props/YCB/Axis_Aligned/025_mug.usd"
            },
        },
    ],
    "source": {
        "library": "NVIDIA Isaac Assets 5.1 / YCB object set",
        "id": "025_mug",
        "license": "unknown (YCB dataset terms + NVIDIA asset EULA; verify before redistribution)",
        "manifest": manifest["files"],
    },
    "physical": {
        "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 0.1},
        "mesh_bbox_m": size,
        "scale": [1.0, 1.0, 1.0],
        "mesh_up_axis": "Y",
    },
    "articulation": {},
    "tags": ["rigid", "external", "smoke"],
}
(out / "mug_bundle.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
(out / "mug_sapien_validation.json").write_text(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print("PASS s8b" if checks["status"] == "pass" else "FAIL s8b")
sys.exit(0 if checks["status"] == "pass" else 1)
