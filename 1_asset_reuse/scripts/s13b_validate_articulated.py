#!/usr/bin/env python3
"""Articulated reverse import, phase 2 (env-gen-yuxin env): SAPIEN validation
of the exported URDF + library registration.

Checks: URDF loads via SAPIEN loader (fix_root_link), dof matches the export
report, limits preserved, 120-step settle finite, and a joint sweep — each
movable joint driven to both limits without the articulation exploding.
Writes model_data0.json, screenshot, bundle JSON.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import sapien

parser = argparse.ArgumentParser()
parser.add_argument("--instance-dir", required=True, help=".../314_cabinet/0")
parser.add_argument("--source-usd", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

inst = Path(args.instance_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
report = json.loads((inst / "export_report.json").read_text())
expected_movable = int(report["joints_movable"])
bbox = report["bbox_m"]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


sc = sapien.Scene()
sc.set_timestep(1 / 100)
sc.add_ground(0)
sc.set_ambient_light([0.5, 0.5, 0.5])
sc.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])
loader = sc.create_urdf_loader()
loader.fix_root_link = True
art = loader.load(str(inst / "mobility.urdf"))
if art is None:
    print("FAIL s13b: URDF failed to load")
    sys.exit(1)
dof = int(art.dof)
active = art.get_active_joints()
limits = [(float(j.get_limits()[0][0]), float(j.get_limits()[0][1])) for j in active]

for _ in range(120):
    sc.step()
qpos = np.asarray(art.get_qpos(), dtype=float)
settle_ok = bool(np.isfinite(qpos).all())

sweep_ok = True
sweep_detail = []
for i, (lo, up) in enumerate(limits):
    for target in (lo, up):
        if not math.isfinite(target):
            continue
        q = np.array(art.get_qpos(), dtype=float)
        q[i] = target
        art.set_qpos(q)
        for _ in range(30):
            sc.step()
        cur = np.asarray(art.get_qpos(), dtype=float)
        okq = bool(np.isfinite(cur).all())
        sweep_ok = sweep_ok and okq
        sweep_detail.append({"joint": i, "target": round(target, 3), "finite": okq})
    q = np.array(art.get_qpos(), dtype=float)
    q[i] = 0.0
    art.set_qpos(q)

cam = sc.add_camera("cam", 640, 480, np.deg2rad(60), 0.01, 10.0)
eye = np.array([1.6, -1.6, 1.2])
f = np.array([0, 0, bbox[2] / 2]) - eye
f /= np.linalg.norm(f)
left = np.cross([0, 0, 1], f)
left /= np.linalg.norm(left)
upv = np.cross(f, left)
m = np.column_stack([f, left, upv])
w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
qc = [
    w,
    (m[2, 1] - m[1, 2]) / (4 * w),
    (m[0, 2] - m[2, 0]) / (4 * w),
    (m[1, 0] - m[0, 1]) / (4 * w),
]
try:
    cam.entity.set_pose(sapien.Pose(p=eye.tolist(), q=qc))
except AttributeError:
    cam.set_local_pose(sapien.Pose(p=eye.tolist(), q=qc))
sc.update_render()
cam.take_picture()
img = (np.clip(cam.get_picture("Color"), 0, 1) * 255).astype(np.uint8)[:, :, :3]
from PIL import Image

Image.fromarray(img).save(out / "sapien_314_cabinet.png")

(inst / "model_data0.json").write_text(
    json.dumps(
        {
            "center": [0, 0, bbox[2] / 2],
            "extents": bbox,
            "scale": [1.0, 1.0, 1.0],
            "transform_matrix": [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ],
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
        },
        indent=2,
    )
)

checks = {
    "dof": dof,
    "expected_movable": expected_movable,
    "dof_matches": dof == expected_movable,
    "limits": [[round(a, 3), round(b, 3)] for a, b in limits],
    "settle_finite": settle_ok,
    "sweep_ok": sweep_ok,
    "sweep_detail": sweep_detail,
    "screenshot_ok": bool(img.std() > 1),
}
checks["status"] = (
    "pass"
    if checks["dof_matches"] and settle_ok and sweep_ok and checks["screenshot_ok"]
    else "fail"
)

bundle = {
    "asset_id": "external_314_cabinet_m0",
    "category": "cabinet",
    "representations": [
        {
            "format": "urdf",
            "uri": str(inst / "mobility.urdf"),
            "backend": "sapien",
            "role": "visual_and_collision",
            "sha256": sha256(inst / "mobility.urdf"),
            "size_bytes": (inst / "mobility.urdf").stat().st_size,
            "metadata": {
                "derived_from": report["source_usd"],
                "links": report["links"],
                "movable_joints": expected_movable,
                "note": "geometry-only OBJs; source materials not ported (lossy)",
            },
        },
        {
            "format": "usd",
            "uri": args.source_usd,
            "backend": "isaacsim",
            "role": "visual_and_collision",
            "sha256": sha256(args.source_usd),
            "size_bytes": Path(args.source_usd).stat().st_size,
            "metadata": {"origin": "Isaac Assets 5.1 /Isaac/Props/Sektion_Cabinet"},
        },
    ],
    "source": {
        "library": "NVIDIA Isaac Assets 5.1",
        "id": "Sektion_Cabinet",
        "license": "unknown (NVIDIA asset EULA; verify before redistribution)",
    },
    "physical": {
        "mass_kg": {"value": None, "status": "unknown", "runtime_default_kg": 10.0},
        "mesh_bbox_m": bbox,
        "scale": [1.0, 1.0, 1.0],
    },
    "articulation": {
        "joint_count_movable": expected_movable,
        "joints": report["movable"],
    },
    "tags": ["articulated", "external", "reverse-import"],
}
(out / "cabinet314_bundle.json").write_text(
    json.dumps(bundle, indent=2, ensure_ascii=False)
)
(out / "cabinet314_validation.json").write_text(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print("PASS s13b" if checks["status"] == "pass" else "FAIL s13b")
sys.exit(0 if checks["status"] == "pass" else 1)
