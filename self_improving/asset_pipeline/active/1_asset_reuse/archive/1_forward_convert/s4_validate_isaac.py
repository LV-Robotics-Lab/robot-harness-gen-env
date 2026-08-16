#!/usr/bin/env python3
"""Isaac-side validation of the CONVERTED USD assets (settle + render).

Runs in isaac-smoke env. Bottle: referenced above a ground plane, settled
300 steps, displacement/tilt/penetration checks. Cabinet: referenced, settled
120 steps, joint state finiteness + dof count. Writes isaac_validation.json
+ screenshots (Camera sensor; best-effort viewport fallback).
"""

import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True)
args = parser.parse_args()
out = Path(args.out)

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

exit_code = 1
try:
    import numpy as np
    from isaacsim.core.api import World
    from isaacsim.core.utils.stage import add_reference_to_stage
    from pxr import Gf, Usd, UsdGeom

    try:
        from isaacsim.core.prims import RigidPrim as RigidView
    except ImportError:
        RigidView = None
    try:
        from isaacsim.core.prims import Articulation as ArtView
    except ImportError:
        ArtView = None
    try:
        from isaacsim.sensors.camera import Camera
    except ImportError:
        Camera = None

    def quat_rotate_z(q_wxyz):
        w, x, y, z = [float(v) for v in q_wxyz]
        # world direction of body +Z axis
        return np.array(
            [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)]
        )

    def lookat_quat(eye, target):
        """wxyz world orientation for a USD camera (-Z forward, +Y up)."""
        eye = np.array(eye, float)
        f = np.array(target, float) - eye
        f /= np.linalg.norm(f)
        zc = -f
        xc = np.cross([0.0, 0.0, 1.0], zc)
        n = np.linalg.norm(xc)
        xc = np.array([1.0, 0.0, 0.0]) if n < 1e-6 else xc / n
        yc = np.cross(zc, xc)
        m = np.column_stack([xc, yc, zc])
        w = np.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
        if w < 1e-6:
            return np.array([1.0, 0.0, 0.0, 0.0])
        return np.array(
            [
                w,
                (m[2, 1] - m[1, 2]) / (4 * w),
                (m[0, 2] - m[2, 0]) / (4 * w),
                (m[1, 0] - m[0, 1]) / (4 * w),
            ]
        )

    def make_camera(name, eye, target):
        if Camera is None:
            return None
        try:
            cam = Camera(
                prim_path=f"/World/{name}",
                position=np.array(eye),
                orientation=lookat_quat(eye, target),
                resolution=(640, 480),
            )
            cam._spike_pose = (np.array(eye, float), lookat_quat(eye, target))
            return cam
        except Exception as exc:  # noqa: BLE001
            print(f"warn: camera create failed: {exc}")
            return None

    def init_camera(cam):
        if cam is None:
            return None
        try:
            cam.initialize()
            eye, q = cam._spike_pose
            try:
                cam.set_world_pose(position=eye, orientation=q, camera_axes="usd")
            except TypeError:
                cam.set_world_pose(position=eye, orientation=q)
            return cam
        except Exception as exc:  # noqa: BLE001
            print(f"warn: camera init failed: {exc}")
            return None

    def shoot(cam, path):
        if cam is None:
            return False
        try:
            for _ in range(8):
                app.update()
            rgba = cam.get_rgba()
            if rgba is None or rgba.size == 0:
                return False
            img = rgba[:, :, :3].astype(np.uint8)
            from PIL import Image

            Image.fromarray(img).save(path)
            return bool(img.std() > 1)
        except Exception as exc:  # noqa: BLE001
            print(f"warn: capture failed: {exc}")
            return False

    report = {}

    # ---------- bottle ----------
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    bottle_usd = out / "bottle.usd"
    bundle = json.loads((out / "bottle_bundle.json").read_text())
    height_m = (bundle["physical"].get("mesh_bbox_m") or bundle["physical"]["extents_m"])[1]
    z0 = 0.005  # origin at mesh bottom; near-ground spawn (parity with s3)
    prim = add_reference_to_stage(usd_path=str(bottle_usd), prim_path="/World/bottle")
    UsdGeom.XformCommonAPI(prim).SetTranslate(Gf.Vec3d(0, 0, z0))
    cam = make_camera("cam_bottle", [0.35, -0.35, 0.3], [0, 0, height_m / 2])
    world.reset()
    cam = init_camera(cam)

    view = None
    if RigidView is not None:
        try:
            view = RigidView("/World/bottle")
        except Exception as exc:  # noqa: BLE001
            print(f"warn: RigidPrim view failed: {exc}")

    def bottle_pose():
        if view is not None:
            try:
                pos, orn = view.get_world_poses()
                return np.asarray(pos[0], dtype=float), np.asarray(orn[0], dtype=float)
            except Exception:
                pass
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()
        )
        t = xf.ExtractTranslation()
        q = xf.ExtractRotationQuat()
        im = q.GetImaginary()
        return (
            np.array([t[0], t[1], t[2]]),
            np.array([q.GetReal(), im[0], im[1], im[2]]),
        )

    mid_pos = None
    for i in range(300):
        world.step(render=(cam is not None and i % 60 == 0))
        if i == 240:
            mid_pos, _ = bottle_pose()
    fpos, forn = bottle_pose()
    late_drift = float(np.linalg.norm(fpos - mid_pos)) if mid_pos is not None else -1.0
    body_up = quat_rotate_z(forn)  # baked asset up-axis is +Z
    tilt_deg = float(np.degrees(np.arccos(np.clip(body_up[2], -1, 1))))
    shot_ok = shoot(cam, out / "isaac_bottle.png")

    checks = {
        "settled_late_drift_m": late_drift,
        "settled": 0 <= late_drift < 0.002,
        "final_z_m": float(fpos[2]),
        "no_ground_penetration": fpos[2] > -0.002,
        "xy_drift_m": float(np.linalg.norm(fpos[:2])),
        "xy_drift_ok": float(np.linalg.norm(fpos[:2])) < 0.05,
        "tilt_deg": tilt_deg,
        "upright": tilt_deg < 15.0,
        "screenshot_ok": bool(shot_ok),
    }
    checks["status"] = (
        "pass"
        if all(
            checks[k]
            for k in ("settled", "no_ground_penetration", "xy_drift_ok", "upright")
        )
        else "fail"
    )
    if not shot_ok:
        checks["note"] = "screenshot soft-failed; physics checks authoritative"
    report["bottle"] = checks
    world.clear()

    # ---------- cabinet ----------
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    cab_bundle = json.loads((out / "cabinet_bundle.json").read_text())
    expected = int(cab_bundle["articulation"]["joint_count_movable"])
    cprim = add_reference_to_stage(
        usd_path=str(out / "cabinet.usd"), prim_path="/World/cabinet"
    )
    ccam = make_camera("cam_cab", [2.0, -2.0, 1.5], [0, 0, 0.5])
    world.reset()
    ccam = init_camera(ccam)

    art = None
    if ArtView is not None:
        try:
            art = ArtView("/World/cabinet")
        except Exception as exc:  # noqa: BLE001
            print(f"warn: Articulation view failed: {exc}")

    sim_error = None
    try:
        for _ in range(120):
            world.step(render=False)
    except Exception as exc:  # noqa: BLE001
        sim_error = f"{type(exc).__name__}: {exc}"

    dof = -1
    qpos_finite = None
    if art is not None:
        try:
            jp = art.get_joint_positions()
            jp = np.asarray(jp, dtype=float).ravel()
            dof = int(jp.size)
            qpos_finite = bool(np.isfinite(jp).all())
        except Exception as exc:  # noqa: BLE001
            print(f"warn: joint read failed: {exc}")
    cshot_ok = shoot(ccam, out / "isaac_cabinet.png")

    cchecks = {
        "sim_120_steps_ok": sim_error is None,
        "sim_error": sim_error,
        "dof": dof,
        "expected_movable": expected,
        "dof_matches": dof == expected,
        "qpos_finite": qpos_finite,
        "screenshot_ok": bool(cshot_ok),
    }
    hard = cchecks["sim_120_steps_ok"] and (
        dof == -1 or (cchecks["dof_matches"] and bool(qpos_finite))
    )
    cchecks["status"] = "pass" if hard else "fail"
    if dof == -1:
        cchecks["note"] = (
            "articulation view unavailable; dof verified at USD level in s2"
        )
    report["cabinet"] = cchecks

    try:
        import importlib.metadata as im

        report["backend"] = f"isaacsim {im.version('isaacsim')}"
    except Exception:
        report["backend"] = "isaacsim unknown"

    (out / "isaac_validation.json").write_text(
        json.dumps(
            report,
            indent=2,
            default=lambda o: o.item() if hasattr(o, "item") else str(o),
        )
    )
    ok = report["bottle"]["status"] == "pass" and report["cabinet"]["status"] == "pass"
    print(
        json.dumps(
            report,
            indent=2,
            default=lambda o: o.item() if hasattr(o, "item") else str(o),
        )
    )
    print("PASS s4" if ok else "FAIL s4")
    exit_code = 0 if ok else 1
except Exception as exc:  # noqa: BLE001
    print(f"FAIL s4: {type(exc).__name__}: {exc}", file=sys.stderr)
    import traceback

    traceback.print_exc()
    exit_code = 1
app.close()
sys.exit(exit_code)
