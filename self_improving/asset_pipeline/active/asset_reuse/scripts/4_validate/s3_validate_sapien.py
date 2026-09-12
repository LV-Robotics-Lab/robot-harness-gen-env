#!/usr/bin/env python3
"""SAPIEN-side validation of the ORIGINAL RoboTwin assets (settle + render).

Runs in env-gen-yuxin (sapien 3.0.0b1). Bottle: visual+collision GLB pair at
scale 0.05, rotated X+90 (Y-up mesh in Z-up world), dropped just above ground,
settled 300 steps. Cabinet: mobility.urdf via URDF loader, fixed base, settled
120 steps. Writes sapien_validation.json + screenshots.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import sapien

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

ROTX90 = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]  # w x y z


def look_at_quat(eye, target):
    """SAPIEN camera convention: x forward, y left, z up. Returns wxyz."""
    f = np.array(target, dtype=float) - np.array(eye, dtype=float)
    f /= np.linalg.norm(f)
    left = np.cross([0.0, 0.0, 1.0], f)
    left /= np.linalg.norm(left)
    up = np.cross(f, left)
    m = np.column_stack([f, left, up])
    w = math.sqrt(max(0.0, 1.0 + m[0, 0] + m[1, 1] + m[2, 2])) / 2.0
    if w < 1e-6:
        return [1.0, 0.0, 0.0, 0.0]
    x = (m[2, 1] - m[1, 2]) / (4 * w)
    y = (m[0, 2] - m[2, 0]) / (4 * w)
    z = (m[1, 0] - m[0, 1]) / (4 * w)
    return [w, x, y, z]


def save_shot(scene, path, eye, target):
    cam = scene.add_camera("cam", 640, 480, np.deg2rad(60), 0.01, 10.0)
    pose = sapien.Pose(p=eye, q=look_at_quat(eye, target))
    try:
        cam.entity.set_pose(pose)
    except AttributeError:
        cam.set_local_pose(pose)
    scene.update_render()
    cam.take_picture()
    rgba = cam.get_picture("Color")
    img = (np.clip(rgba, 0, 1) * 255).astype(np.uint8)[:, :, :3]
    from PIL import Image

    Image.fromarray(img).save(path)
    nonwhite = int((img.std(axis=2) > 1).sum() + (img.mean() < 250) * 1)
    return img.mean() > 1 and img.std() > 1


def quat_rotate(q, v):
    w, x, y, z = q
    qv = np.array([x, y, z])
    t = 2 * np.cross(qv, v)
    return np.array(v) + w * t + np.cross(qv, t)


def validate_bottle(bundle):
    scene = sapien.Scene()
    scene.set_timestep(1 / 100)
    scene.add_ground(0)
    scene.set_ambient_light([0.5, 0.5, 0.5])
    scene.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])

    reps = {
        r["role"]: r["uri"]
        for r in bundle["representations"]
        if r["backend"] == "sapien"
    }
    scale = bundle["physical"]["scale"]
    height_m = (bundle["physical"].get("mesh_bbox_m") or bundle["physical"]["extents_m"])[1]  # mesh Y -> world Z
    z0 = 0.005  # origin at mesh bottom; near-ground spawn tests standing stability, not drop survival

    b = scene.create_actor_builder()
    b.add_multiple_convex_collisions_from_file(filename=reps["collision"], scale=scale)
    b.add_visual_from_file(filename=reps["visual"], scale=scale)
    actor = b.build(name="bottle")
    start = sapien.Pose(p=[0, 0, z0], q=ROTX90)
    actor.set_pose(start)

    poses = []
    for i in range(300):
        scene.step()
        if i % 50 == 0 or i == 299:
            poses.append(actor.get_pose())
    final = poses[-1]
    late_drift = float(np.linalg.norm(np.array(final.p) - np.array(poses[-2].p)))
    xy_drift = float(np.linalg.norm(np.array(final.p[:2]) - np.array(start.p[:2])))
    up_world = quat_rotate(list(final.q), [0.0, 1.0, 0.0])  # mesh +Y should point up
    tilt_deg = float(np.degrees(np.arccos(np.clip(up_world[2], -1, 1))))
    shot_ok = save_shot(
        scene, out / "sapien_bottle.png", [0.3, -0.3, 0.25], [0, 0, height_m / 2]
    )

    checks = {
        "settled_late_drift_m": late_drift,
        "settled": late_drift < 0.002,
        "xy_drift_m": xy_drift,
        "xy_drift_ok": xy_drift < 0.05,
        "final_z_m": float(final.p[2]),
        "no_ground_penetration": final.p[2] > -0.002,
        "tilt_deg": tilt_deg,
        "upright": tilt_deg < 15.0,
        "screenshot_ok": bool(shot_ok),
    }
    checks["status"] = (
        "pass"
        if all(
            checks[k]
            for k in (
                "settled",
                "xy_drift_ok",
                "no_ground_penetration",
                "upright",
                "screenshot_ok",
            )
        )
        else "fail"
    )
    return checks


def validate_cabinet(bundle):
    scene = sapien.Scene()
    scene.set_timestep(1 / 100)
    scene.add_ground(0)
    scene.set_ambient_light([0.5, 0.5, 0.5])
    scene.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])

    urdf = bundle["representations"][0]["uri"]
    loader = scene.create_urdf_loader()
    loader.fix_root_link = True
    art = loader.load(str(urdf))
    dof = int(art.dof) if hasattr(art, "dof") else len(art.get_active_joints())
    expected = int(bundle["articulation"]["joint_count_movable"])

    for _ in range(120):
        scene.step()
    qpos = np.asarray(art.get_qpos(), dtype=float)
    root_p = np.asarray(
        art.get_pose().p if hasattr(art, "get_pose") else art.get_root_pose().p
    )
    shot_ok = save_shot(
        scene, out / "sapien_cabinet.png", [1.6, -1.6, 1.2], [0, 0, 0.4]
    )

    checks = {
        "dof": dof,
        "expected_movable": expected,
        "dof_matches": dof == expected,
        "qpos_finite": bool(np.isfinite(qpos).all()),
        "root_finite": bool(np.isfinite(root_p).all()),
        "screenshot_ok": bool(shot_ok),
    }
    checks["status"] = (
        "pass"
        if all(
            checks[k]
            for k in ("dof_matches", "qpos_finite", "root_finite", "screenshot_ok")
        )
        else "fail"
    )
    return checks


def jsonable(o):
    return o.item() if hasattr(o, "item") else str(o)


def main():
    bottle = json.loads((out / "bottle_bundle.json").read_text())
    cabinet = json.loads((out / "cabinet_bundle.json").read_text())
    report = {
        "backend": f"sapien {sapien.__version__}",
        "bottle": validate_bottle(bottle),
        "cabinet": validate_cabinet(cabinet),
    }
    (out / "sapien_validation.json").write_text(
        json.dumps(report, indent=2, default=jsonable)
    )
    ok = report["bottle"]["status"] == "pass" and report["cabinet"]["status"] == "pass"
    print(json.dumps(report, indent=2, default=jsonable))
    print("PASS s3" if ok else "FAIL s3")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
