#!/usr/bin/env python3
"""Isaac Sim physics-binding settle harness.

Closes the "static reference passes vacuously" hole E2 exposed (2026-08-15):
the compiled scene.usda mounts each asset as a plain USD reference with NO
physics schemas, so a smoke run reports reset_ok/step_ok while the object
never simulates (20-step displacement = 0.0). This harness produces a real
(isaacsim, settle) verification record per asset model:

  1. compile stage (run under the env-gen-yuxin python): resolved_scene ->
     import_env_gen -> enrich_from_ledgers -> IsaacSimCompiler -> scene.usda
     (the production chain, no patches);
  2. sim stage (this same file re-invoked with --sim under the isaac-smoke
     python): open the compiled stage, apply UsdPhysics.RigidBodyAPI to the
     target prim and CollisionAPI + convexHull MeshCollisionAPI to its meshes,
     drop a static support box directly under the asset's composed bbox
     bottom (objects spawn at table height z~=0.741 but the compiled stage
     has no table), fix the PhysicsScene gravity encoding, then step ~300
     physics steps and measure settle;
  3. verdict: last-50-step displacement < 2 mm AND up-axis tilt < 15 deg.

Evidence (poses, displacement, tilt, applied-schema lists proving the body is
NOT a bare static reference) goes to --out-dir; a facts.json compatible with
1_asset_reuse/scripts/ledger/writeback_verification.py is written alongside.

Usage (driver, env-gen-yuxin python):
  isaac_settle_check.py [--cases can box] [--out-dir DIR] [--run-id ID]
  isaac_settle_check.py --resolved-scene P --asset 302_can --model 3

The --sim mode is internal (invoked by the driver under isaac-smoke python).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")
SCENES_DIR = DEV / "data/scene_gen_ext/_admission_work/scenes"
LEDGER_DIRS = [DEV / "data/asset_library"]
ISAAC_PYTHON = "/home/jingxiang/miniconda3/envs/isaac-smoke/bin/python"
DEFAULT_OUT = Path("/home/jingxiang/isaac_settle_out")

# Built-in cases: scene -> (asset_dir, model_id) as resolved by the scene's
# own grounding (verified against resolved_scene.json objects[0]).
CASES = {
    "can": {
        "resolved_scene": SCENES_DIR
        / "place_a_can_on_the_table_acd20a6814/resolved_scene.json",
        "asset_dir": "302_can",
        "model_id": 3,
    },
    "box": {
        "resolved_scene": SCENES_DIR
        / "place_a_box_on_the_table_791fdb1a83/resolved_scene.json",
        "asset_dir": "303_box",
        "model_id": 0,
    },
}

WINDOW_STEPS = 50
WINDOW_DISP_MAX_M = 0.002
TILT_MAX_DEG = 15.0


# --------------------------------------------------------------------------
# compile stage (env-gen-yuxin python)
# --------------------------------------------------------------------------


def compile_scene(resolved_scene: Path, out_dir: Path) -> dict:
    """Production compile chain; returns manifest-ish dict incl. artifact."""
    import importlib.util

    sys.path.insert(0, str(DEV / "shared/openxsim/source/agenticsim"))
    sys.path.insert(0, str(DEV / "1_asset_reuse"))  # `lib` pkg (ledger)

    spec = importlib.util.spec_from_file_location(
        "usd_enrich_settle", str(DEV / "2_sim_migration/lib/usd_enrich.py")
    )
    ue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ue)

    from agenticsim.openxsim.env_gen import import_env_gen
    from agenticsim.openxsim.backends import IsaacSimCompiler

    pkg = import_env_gen(resolved_scene)
    enriched, report = ue.enrich_from_ledgers(pkg, LEDGER_DIRS)
    res = IsaacSimCompiler().compile(enriched, out_dir)
    return {
        "status": res.status,
        "blockers": list(res.blockers),
        "warnings": list(res.warnings),
        "artifact": res.artifact_path,
        "enrich_report": report,
        "asset_ids": list(res.metadata.get("asset_ids", [])),
        "object_ids": list(res.metadata.get("object_ids", [])),
    }


# --------------------------------------------------------------------------
# sim stage (isaac-smoke python, invoked with --sim)
# --------------------------------------------------------------------------


def _quat_wxyz_to_mat(q):
    import numpy as np

    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def run_sim(args) -> int:
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "hide_ui": True,
            "fast_shutdown": True,
            "active_gpu": 0,
            "physics_gpu": 0,
            "multi_gpu": False,
            "max_gpu_count": 1,
        }
    )
    evidence = {
        "run_id": args.run_id,
        "case": args.case,
        "asset_dir": args.asset_dir,
        "model_id": args.model_id,
        "check": "settle",
        "backend": "isaacsim",
        "stage_usd": args.stage_usd,
        "prim_path": args.prim_path,
        "physics": {"dt_s": 1.0 / args.hz, "hz": args.hz, "steps": args.steps},
        "criteria": {
            "window_steps": WINDOW_STEPS,
            "window_disp_lt_m": WINDOW_DISP_MAX_M,
            "up_tilt_lt_deg": TILT_MAX_DEG,
        },
    }
    code = 1
    try:
        import numpy as np
        import omni.usd
        from pxr import Usd, UsdGeom, UsdPhysics, Gf
        from isaacsim.core.api import World

        ctx = omni.usd.get_context()
        ok = ctx.open_stage(args.stage_usd)
        stage = ctx.get_stage()
        if not ok or stage is None:
            raise RuntimeError(f"failed to open stage {args.stage_usd}")
        prim = stage.GetPrimAtPath(args.prim_path)
        if not prim.IsValid():
            raise RuntimeError(f"prim not found: {args.prim_path}")

        # -- self-proof part 1: what the compiled stage carries BEFORE us ----
        evidence["binding"] = {
            "applied_schemas_before": [str(s) for s in prim.GetAppliedSchemas()],
            "preexisting_physics_prims": [
                str(p.GetPath())
                for p in Usd.PrimRange(prim)
                if any("Physics" in str(s) for s in p.GetAppliedSchemas())
            ],
        }

        # -- gravity: compiled stage encodes direction=(0,0,-9.81), mag=1;
        #    normalize to the USD Physics convention so gravity is 9.81 m/s2.
        ps_prim = stage.GetPrimAtPath("/World/PhysicsScene")
        if ps_prim.IsValid():
            ps = UsdPhysics.Scene(ps_prim)
            evidence["physics"]["gravity_original"] = {
                "direction": list(ps.GetGravityDirectionAttr().Get() or ()),
                "magnitude": ps.GetGravityMagnitudeAttr().Get(),
            }
            ps.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, 0.0, -1.0))
            ps.CreateGravityMagnitudeAttr().Set(9.81)
            evidence["physics"]["gravity_applied"] = {
                "direction": [0.0, 0.0, -1.0],
                "magnitude": 9.81,
            }

        # -- composed world bbox before physics: support goes at its bottom --
        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        )
        rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        bb_min, bb_max = rng.GetMin(), rng.GetMax()
        evidence["bbox_before_m"] = {
            "min": [float(v) for v in bb_min],
            "max": [float(v) for v in bb_max],
        }
        z_bottom = float(bb_min[2])
        cx = 0.5 * float(bb_min[0] + bb_max[0])
        cy = 0.5 * float(bb_min[1] + bb_max[1])

        # -- physics binding: rigid body on the asset root, convex-hull
        #    collision on every composed mesh under it ----------------------
        UsdPhysics.RigidBodyAPI.Apply(prim)
        collision_prims = []
        for p in Usd.PrimRange(prim):
            if p.IsA(UsdGeom.Mesh):
                UsdPhysics.CollisionAPI.Apply(p)
                mc = UsdPhysics.MeshCollisionAPI.Apply(p)
                mc.CreateApproximationAttr().Set(UsdPhysics.Tokens.convexHull)
                collision_prims.append(
                    {
                        "path": str(p.GetPath()),
                        "approximation": str(mc.GetApproximationAttr().Get()),
                        "applied_schemas": [str(s) for s in p.GetAppliedSchemas()],
                    }
                )
        if not collision_prims:
            raise RuntimeError(f"no UsdGeom.Mesh under {args.prim_path}")

        # -- static support box: top face 0.5 mm below the asset bottom -----
        thickness = 0.05
        gap = 0.0005
        support = UsdGeom.Cube.Define(stage, "/World/SettleSupport")
        support.CreateSizeAttr(1.0)
        xf = UsdGeom.Xformable(support)
        xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, z_bottom - gap - thickness / 2.0))
        xf.AddScaleOp().Set(Gf.Vec3d(1.0, 1.0, thickness))
        UsdPhysics.CollisionAPI.Apply(support.GetPrim())  # static: no RigidBody
        evidence["binding"]["support_box"] = {
            "path": "/World/SettleSupport",
            "top_z_m": z_bottom - gap,
            "size_m": [1.0, 1.0, thickness],
        }

        # -- self-proof part 2: the body is a real rigid body now -----------
        evidence["binding"]["applied_schemas_after"] = [
            str(s) for s in prim.GetAppliedSchemas()
        ]
        evidence["binding"]["collision_prims"] = collision_prims

        if args.export_stage:
            stage.Export(args.export_stage)
            evidence["bound_stage_export"] = args.export_stage

        # -- simulate --------------------------------------------------------
        world = World(
            physics_dt=1.0 / args.hz,
            rendering_dt=1.0 / args.hz,
            stage_units_in_meters=1.0,
        )
        world.reset()

        body = None
        pose_source = "usd_xform"
        try:
            from isaacsim.core.prims import SingleRigidPrim

            body = SingleRigidPrim(prim_path=args.prim_path, name="settle_target")
            try:
                body.initialize(world.physics_sim_view)
            except TypeError:
                body.initialize()
            body.get_world_pose()  # probe
            pose_source = "SingleRigidPrim"
        except Exception:
            evidence["pose_source_fallback_tb"] = traceback.format_exc(limit=3)
            body = None

        def get_pose():
            if body is not None:
                pos, quat = body.get_world_pose()
                return (
                    np.asarray(pos, dtype=float),
                    np.asarray(quat, dtype=float),
                )
            m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
                Usd.TimeCode.Default()
            )
            t = m.ExtractTranslation()
            q = m.ExtractRotationQuat()
            im = q.GetImaginary()
            return (
                np.array([t[0], t[1], t[2]], dtype=float),
                np.array([q.GetReal(), im[0], im[1], im[2]], dtype=float),
            )

        evidence["pose_source"] = pose_source
        p0, q0 = get_pose()
        positions = [p0]
        for _ in range(args.steps):
            world.step(render=False)
            positions.append(get_pose()[0])
        pf, qf = get_pose()

        # cross-check final pose straight from the USD stage
        m = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        t = m.ExtractTranslation()
        evidence["usd_pose_final_crosscheck_m"] = [t[0], t[1], t[2]]

        # -- metrics ---------------------------------------------------------
        total_disp = float(np.linalg.norm(pf - p0))
        window_disp = float(np.linalg.norm(pf - positions[-1 - WINDOW_STEPS]))
        max_disp = float(max(np.linalg.norm(p - p0) for p in positions))
        r0, rf = _quat_wxyz_to_mat(q0), _quat_wxyz_to_mat(qf)
        up = np.array([0.0, 0.0, 1.0])
        body_up = r0.T @ up  # body-frame axis that pointed up at t0
        cos_tilt = float(np.clip(np.dot(rf @ body_up, up), -1.0, 1.0))
        up_tilt_deg = math.degrees(math.acos(cos_tilt))
        # total relative rotation (incl. harmless yaw), for the record
        qc = np.array([q0[0], -q0[1], -q0[2], -q0[3]])
        w_rel = abs(qf[0] * qc[0] - qf[1] * qc[1] - qf[2] * qc[2] - qf[3] * qc[3])
        rel_rot_deg = math.degrees(2.0 * math.acos(min(1.0, w_rel)))

        evidence["pose_initial"] = {
            "position_m": [float(v) for v in p0],
            "quat_wxyz": [float(v) for v in q0],
        }
        evidence["pose_final"] = {
            "position_m": [float(v) for v in pf],
            "quat_wxyz": [float(v) for v in qf],
        }
        evidence["metrics"] = {
            "total_displacement_m": total_disp,
            "max_displacement_m": max_disp,
            f"window_last{WINDOW_STEPS}_displacement_m": window_disp,
            "up_tilt_deg": up_tilt_deg,
            "rel_rotation_deg": rel_rot_deg,
        }

        fails = []
        if window_disp >= WINDOW_DISP_MAX_M:
            fails.append(
                f"window displacement {window_disp:.6f} m >= {WINDOW_DISP_MAX_M} m"
            )
        if up_tilt_deg >= TILT_MAX_DEG:
            fails.append(f"up tilt {up_tilt_deg:.2f} deg >= {TILT_MAX_DEG} deg")
        evidence["fail_reasons"] = fails
        evidence["verdict"] = "pass" if not fails else "fail"
        code = 0
    except Exception:
        evidence["verdict"] = "error"
        evidence["error"] = traceback.format_exc()
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n")
        try:
            app.close()
        except Exception:
            pass
    return code


# --------------------------------------------------------------------------
# driver (env-gen-yuxin python)
# --------------------------------------------------------------------------


def run_case(name: str, cfg: dict, args) -> dict:
    out = Path(args.out_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "settle_report.json"
    fact = {
        "asset_dir": cfg["asset_dir"],
        "model_id": cfg["model_id"],
        "check": "settle",
        "verdict": "error",
        "run_id": args.run_id,
        "report_path": str(report_path),
    }

    comp = compile_scene(Path(cfg["resolved_scene"]), out / "compile")
    print(
        f"[{name}] compile status={comp['status']} assets={comp['asset_ids']} "
        f"objects={comp['object_ids']} blockers={comp['blockers']}"
    )
    expected = f"{cfg['asset_dir']}_m{cfg['model_id']}"
    if comp["blockers"] or not comp["artifact"]:
        report_path.write_text(
            json.dumps({"error": "compile blocked", **comp}, indent=2)
        )
        return fact
    if not any(expected in a for a in comp["asset_ids"]):
        report_path.write_text(
            json.dumps(
                {
                    "error": f"scene resolved to {comp['asset_ids']}, expected *{expected}",
                    **comp,
                },
                indent=2,
            )
        )
        return fact

    prim_path = f"/World/Objects/{comp['object_ids'][0]}"
    cmd = [
        args.isaac_python,
        str(Path(__file__).resolve()),
        "--sim",
        "--stage-usd",
        comp["artifact"],
        "--prim-path",
        prim_path,
        "--evidence",
        str(report_path),
        "--export-stage",
        str(out / "settle_scene_bound.usda"),
        "--steps",
        str(args.steps),
        "--hz",
        str(args.hz),
        "--run-id",
        args.run_id,
        "--case",
        name,
        "--asset-dir",
        cfg["asset_dir"],
        "--model-id",
        str(cfg["model_id"]),
    ]
    log = out / "sim_stdout.log"
    with open(log, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=1200)
    if report_path.is_file():
        ev = json.loads(report_path.read_text())
        ev["compile"] = comp
        report_path.write_text(json.dumps(ev, indent=2) + "\n")
        fact["verdict"] = ev.get("verdict", "error")
        m = ev.get("metrics", {})
        print(
            f"[{name}] verdict={fact['verdict']} "
            f"total={m.get('total_displacement_m')} "
            f"window={m.get(f'window_last{WINDOW_STEPS}_displacement_m')} "
            f"tilt={m.get('up_tilt_deg')} (sim exit={proc.returncode}, log={log})"
        )
    else:
        print(f"[{name}] sim produced no evidence (exit={proc.returncode}); see {log}")
    return fact


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", nargs="+", default=list(CASES), choices=list(CASES))
    ap.add_argument("--resolved-scene", help="custom scene json (with --asset/--model)")
    ap.add_argument("--asset", help="asset dir name, e.g. 302_can")
    ap.add_argument("--model", type=int, help="ledger model_id")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--run-id", default="isaac-settle-20260816")
    ap.add_argument("--isaac-python", default=ISAAC_PYTHON)
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--hz", type=float, default=120.0)
    # internal sim mode
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--stage-usd")
    ap.add_argument("--prim-path")
    ap.add_argument("--evidence")
    ap.add_argument("--export-stage")
    ap.add_argument("--case")
    ap.add_argument("--asset-dir")
    ap.add_argument("--model-id", type=int)
    args = ap.parse_args()

    if args.sim:
        return run_sim(args)

    if args.resolved_scene:
        if not (args.asset and args.model is not None):
            ap.error("--resolved-scene requires --asset and --model")
        cases = {
            "custom": {
                "resolved_scene": args.resolved_scene,
                "asset_dir": args.asset,
                "model_id": args.model,
            }
        }
    else:
        cases = {k: CASES[k] for k in args.cases}

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    facts = [run_case(name, cfg, args) for name, cfg in cases.items()]
    facts_path = Path(args.out_dir) / "facts.json"
    facts_path.write_text(json.dumps(facts, indent=2) + "\n")
    print(f"\nfacts -> {facts_path}")
    for f in facts:
        print(f"  {f['asset_dir']} m{f['model_id']}: {f['verdict']}")
    return 0 if all(f["verdict"] in ("pass", "fail") for f in facts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
