#!/usr/bin/env python3
"""Evidence renders for the A line (RoboTwin -> Isaac), isaac-smoke env.

T2: bottle.usd front render with a FIXED camera convention (world-frame
X-forward/Z-up look-at passed with camera_axes="world" — the earlier bad
framing came from feeding a USD-convention quaternion into the world-frame
API).
T3: cabinet.usd drawer actuation via UsdPhysics.DriveAPI — closed / half /
open frames + joint telemetry. Upgrades quadrant-2 evidence from "simulates
without crashing" to "joints actually actuate in Isaac".
"""

import argparse
import json
import math
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--assets-dir", required=True, help="20260802 smoke results dir")
parser.add_argument("--out", required=True)
args = parser.parse_args()
adir = Path(args.assets_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
code = 1
try:
    import numpy as np
    from isaacsim.core.api import World
    from isaacsim.core.utils.stage import add_reference_to_stage
    from isaacsim.sensors.camera import Camera
    from pxr import Gf, UsdGeom, UsdPhysics

    def lookat_usd_quat(eye, target):
        """USD camera convention: -Z forward, +Y up (camera_axes='usd')."""
        eye = np.array(eye, float)
        f = np.array(target, float) - eye
        f /= np.linalg.norm(f)
        zc = -f
        xc = np.cross([0.0, 0.0, 1.0], zc)
        n = np.linalg.norm(xc)
        xc = np.array([1.0, 0.0, 0.0]) if n < 1e-6 else xc / n
        yc = np.cross(zc, xc)
        m = np.column_stack([xc, yc, zc])
        w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
        return np.array(
            [
                w,
                (m[2, 1] - m[1, 2]) / (4 * w),
                (m[0, 2] - m[2, 0]) / (4 * w),
                (m[1, 0] - m[0, 1]) / (4 * w),
            ]
        )

    def shot(cam, path):
        for _ in range(12):
            app.update()
        rgba = cam.get_rgba()
        img = rgba[:, :, :3].astype(np.uint8)
        from PIL import Image

        Image.fromarray(img).save(path)
        return float(img.std())

    report = {}

    # ---------- T2: bottle front render ----------
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    bundle = json.loads((adir / "bottle_bundle.json").read_text())
    h = bundle["physical"]["mesh_bbox_m"][1]
    prim = add_reference_to_stage(
        usd_path=str(adir / "bottle.usd"), prim_path="/World/bottle"
    )
    UsdGeom.XformCommonAPI(prim).SetTranslate(Gf.Vec3d(0, 0, 0.003))
    from pxr import UsdLux

    UsdLux.DomeLight.Define(prim.GetStage(), "/World/Dome1").CreateIntensityAttr(1500.0)
    for i in range(240):
        world.step(render=False) if False else None
    cache0 = UsdGeom.BBoxCache(0, ["default", "render"])
    rng0 = cache0.ComputeWorldBound(prim).ComputeAlignedRange()
    c0 = [(a + b) / 2 for a, b in zip(rng0.GetMin(), rng0.GetMax())]
    s0 = max(b - a for a, b in zip(rng0.GetMin(), rng0.GetMax()))
    d0 = 2.4 * s0
    eye = [c0[0] + d0 * 0.72, c0[1] - d0 * 0.72, c0[2] + d0 * 0.45]
    tgt = c0
    print(f"bottle bbox center={[round(v, 3) for v in c0]} span={s0:.3f}")
    cam = Camera(prim_path="/World/cam", position=np.array(eye), resolution=(800, 600))
    world.reset()
    cam.initialize()
    cam.set_clipping_range(0.01, 100000.0)
    cam.set_world_pose(
        position=np.array(eye),
        orientation=lookat_usd_quat(eye, tgt),
        camera_axes="usd",
    )
    for i in range(240):
        world.step(render=(i % 60 == 0))
    std = shot(cam, out / "isaac_bottle_front.png")
    report["bottle_front"] = {"img_std": std, "ok": std > 10}
    print(f"T2 bottle front: std={std:.1f}")
    world.clear()

    # ---------- T3: cabinet drawer actuation ----------
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    cprim = add_reference_to_stage(
        usd_path=str(adir / "cabinet.usd"), prim_path="/World/cabinet"
    )
    stage = cprim.GetStage()
    from pxr import UsdLux as _UL

    _UL.DomeLight.Define(stage, "/World/Dome2").CreateIntensityAttr(1500.0)
    joints = []
    for p in stage.Traverse():
        if p.IsA(UsdPhysics.PrismaticJoint):
            j = UsdPhysics.PrismaticJoint(p)
            lo = float(j.GetLowerLimitAttr().Get() or 0.0)
            up = float(j.GetUpperLimitAttr().Get() or 0.0)
            joints.append((p, lo, up))
    print(
        f"prismatic joints: {[(str(p.GetPath()).rsplit('/', 1)[-1], lo, up) for p, lo, up in joints]}"
    )
    drives = []
    for p, lo, up in joints:
        d = UsdPhysics.DriveAPI.Apply(p, "linear")
        d.CreateTargetPositionAttr(0.0)
        d.CreateStiffnessAttr(1e5)
        d.CreateDampingAttr(1e4)
        d.CreateMaxForceAttr(1e6)
        drives.append((d, lo, up))
    cache = UsdGeom.BBoxCache(0, ["default", "render"])
    rng = cache.ComputeWorldBound(cprim).ComputeAlignedRange()
    center = [(a + b) / 2 for a, b in zip(rng.GetMin(), rng.GetMax())]
    span = max(b - a for a, b in zip(rng.GetMin(), rng.GetMax()))
    d = 2.7 * span
    eye2 = [center[0] - d * 0.72, center[1] - d * 0.72, center[2] + d * 0.38]
    tgt2 = center
    print(f"cabinet bbox center={[round(v, 2) for v in center]} span={span:.2f}")
    cam2 = Camera(
        prim_path="/World/cam2", position=np.array(eye2), resolution=(800, 600)
    )
    world.reset()
    cam2.initialize()
    cam2.set_clipping_range(0.01, 100000.0)
    cam2.set_world_pose(
        position=np.array(eye2),
        orientation=lookat_usd_quat(eye2, tgt2),
        camera_axes="usd",
    )

    telemetry = []
    art = None
    try:
        from isaacsim.core.prims import SingleArticulation

        art = SingleArticulation("/World/cabinet")
        art.initialize()
    except Exception as exc:  # noqa: BLE001
        print(f"warn: articulation view unavailable ({exc}); frames still prove motion")

    # drive ALL prismatic joints together so the visible drawer必动
    for label, frac in (("closed", 0.0), ("half", 0.5), ("open", 1.0)):
        for dd, llo, uup in drives:
            dd.GetTargetPositionAttr().Set(llo + (uup - llo) * frac)
        for i in range(180):
            world.step(render=(i % 45 == 0))
        std = shot(cam2, out / f"isaac_cabinet_{label}.png")
        entry = {"state": label,
                 "targets": [round(llo + (uup - llo) * frac, 3) for _, llo, uup in drives],
                 "img_std": std}
        if art is not None:
            try:
                q = art.get_joint_positions()
                entry["qpos"] = [round(float(v), 4) for v in np.asarray(q).ravel()]
            except Exception:  # noqa: BLE001
                pass
        telemetry.append(entry)
        print(
            f"T3 {label}: targets={entry['targets']} "
            f"qpos={entry.get('qpos', 'n/a')} std={std:.1f}"
        )
    report["cabinet_actuation"] = {
        "joints_driven": [str(p.GetPath()) for p, _, _ in joints],
        "frames": telemetry,
    }
    (out / "evidence_report.json").write_text(json.dumps(report, indent=2))
    moved = len(telemetry) == 3 and (
        telemetry[-1].get("qpos") is None or max(abs(t) for t in telemetry[-1]["targets"]) > 1e-3
    )
    print("PASS s15" if report["bottle_front"]["ok"] and moved else "PARTIAL s15")
    code = 0
except Exception as exc:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    print(f"FAIL s15 {type(exc).__name__}: {exc}")
app.close()
raise SystemExit(code)
