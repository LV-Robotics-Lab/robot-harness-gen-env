#!/usr/bin/env python3
"""001_bottle: GLB -> USD via omni.kit.asset_converter, then physics assembly.

Runs in isaac-smoke env. Output: bottle_visual_raw.usd + bottle_collision_raw.usd
+ assembled bottle.usd (rigid body, convex-decomposition collider, baked
scale 0.05 and Y-up -> Z-up rotation). Appends an isaacsim representation to
the bundle JSON.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", required=True)
parser.add_argument("--out-dir", required=True)
args = parser.parse_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})

try:
    from isaacsim.core.utils.extensions import enable_extension

    enable_extension("omni.kit.asset_converter")
    app.update()

    import omni.kit.asset_converter as ac
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    bundle_path = Path(args.bundle)
    bundle = json.loads(bundle_path.read_text())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def find_rep(role):
        for r in bundle["representations"]:
            if r["backend"] == "sapien" and r["role"] == role:
                return r
        raise SystemExit(f"FAIL missing sapien representation role={role}")

    vis_glb = find_rep("visual")["uri"]
    col_glb = find_rep("collision")["uri"]
    scale = [float(s) for s in bundle["physical"]["scale"]]
    mass_default = float(bundle["physical"]["mass_kg"]["runtime_default_kg"])

    async def convert(src, dst):
        ctx = ac.AssetConverterContext()
        task = ac.get_instance().create_converter_task(str(src), str(dst), None, ctx)
        ok = await task.wait_until_finished()
        if not ok:
            raise RuntimeError(
                f"convert failed {src}: {task.get_status()} {task.get_error_message()}"
            )

    loop = asyncio.get_event_loop()
    vis_usd = out / "bottle_visual_raw.usd"
    col_usd = out / "bottle_collision_raw.usd"
    loop.run_until_complete(convert(vis_glb, vis_usd))
    print(f"converted visual -> {vis_usd.name}")
    loop.run_until_complete(convert(col_glb, col_usd))
    print(f"converted collision -> {col_usd.name}")

    # Converter output units are its own business (observed: cm layer, 100x
    # geometry). References do NOT unit-convert, so calibrate: measure the
    # composed bbox of the converted visual and scale to the metric extents
    # recorded from RoboTwin model_data.
    def measured_size(usd_path):
        tmp = Usd.Stage.Open(str(usd_path))
        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), ["default", "render", "guide"]
        )
        dp = tmp.GetDefaultPrim()
        rng = cache.ComputeWorldBound(dp).ComputeAlignedRange()
        return [float(v) for v in (rng.GetMax() - rng.GetMin())]

    target_m = [float(v) for v in (bundle["physical"].get("mesh_bbox_m") or bundle["physical"]["extents_m"])]
    measured = measured_size(vis_usd)
    factor = target_m[1] / measured[1]  # height axis (Y in raw mesh frame)
    x_ratio = (target_m[0] / measured[0]) / factor if measured[0] else 1.0
    if abs(x_ratio - 1.0) > 0.05:
        print(f"warn: non-uniform unit ratio across axes (x/y ratio {x_ratio:.3f})")
    scale = [factor, factor, factor]
    print(
        f"calibrated scale: measured={['%.3f' % m for m in measured]} "
        f"target_m={['%.4f' % t for t in target_m]} factor={factor:.6f}"
    )

    asset_usd = out / "bottle.usd"
    if asset_usd.exists():
        asset_usd.unlink()
    stage = Usd.Stage.CreateNew(str(asset_usd))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/bottle")
    stage.SetDefaultPrim(root.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    mass_api = UsdPhysics.MassAPI.Apply(root.GetPrim())
    mass_api.CreateMassAttr(mass_default)

    def add_part(name, ref_usd, is_collision):
        # own clean Xform for our ops; the reference (which carries its own
        # xformOps from the converter) lives one level below
        xform = UsdGeom.Xform.Define(stage, f"/bottle/{name}")
        xf = UsdGeom.Xformable(xform.GetPrim())
        xf.AddRotateXOp().Set(90.0)  # GLB is Y-up, stage is Z-up
        xf.AddScaleOp().Set(Gf.Vec3f(*scale))
        inner = UsdGeom.Xform.Define(stage, f"/bottle/{name}/geom")
        prim = inner.GetPrim()
        prim.GetReferences().AddReference(f"./{ref_usd.name}")
        if is_collision:
            n_mesh = 0
            for p in Usd.PrimRange(prim):
                if p.IsA(UsdGeom.Mesh):
                    UsdPhysics.CollisionAPI.Apply(p)
                    mc = UsdPhysics.MeshCollisionAPI.Apply(p)
                    mc.CreateApproximationAttr("convexDecomposition")
                    n_mesh += 1
            UsdGeom.Imageable(prim).CreatePurposeAttr(UsdGeom.Tokens.guide)
            print(f"collision meshes tagged: {n_mesh}")
            if n_mesh == 0:
                raise RuntimeError("no meshes found under collision reference")
        return prim

    add_part("visual", vis_usd, is_collision=False)
    add_part("collision", col_usd, is_collision=True)
    stage.GetRootLayer().Save()

    def sha256(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    try:
        import importlib.metadata as im

        isaac_ver = im.version("isaacsim")
    except Exception:
        isaac_ver = "unknown"

    bundle["representations"].append(
        {
            "format": "usd",
            "uri": str(asset_usd),
            "backend": "isaacsim",
            "role": "visual_and_collision",
            "sha256": sha256(asset_usd),
            "size_bytes": asset_usd.stat().st_size,
            "metadata": {
                "converter": f"omni.kit.asset_converter (isaacsim {isaac_ver})",
                "scale_calibrated": scale,
                "scale_calibration": {
                    "measured_converted_bbox": measured,
                    "target_extents_m": target_m,
                },
                "rotate_x_deg": 90,
                "mass_default_kg": mass_default,
                "collision_approximation": "convexDecomposition",
                "requires_siblings": [vis_usd.name, col_usd.name],
                "source_sha256": {
                    "visual_glb": find_rep("visual")["sha256"],
                    "collision_glb": find_rep("collision")["sha256"],
                },
            },
        }
    )
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
    print(f"PASS s1 bottle.usd sha256={sha256(asset_usd)[:12]}")
except Exception as exc:  # noqa: BLE001
    print(f"FAIL s1: {type(exc).__name__}: {exc}", file=sys.stderr)
    app.close()
    sys.exit(1)
app.close()
