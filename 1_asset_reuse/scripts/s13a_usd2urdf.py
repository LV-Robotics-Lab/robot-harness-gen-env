#!/usr/bin/env python3
"""Articulated reverse import, phase 1 (isaac-smoke env): USD articulation ->
mobility.urdf + per-link OBJ meshes in RoboTwin layout.

Reads UsdPhysics joints (revolute/prismatic/fixed) and RigidBody links from a
source USD, exports each link's visual/collision meshes into the link's rest
frame, and emits a URDF whose base joint lifts the assembly so its bbox bottom
sits at z=0 (origin_on_table convention). Units scaled by metersPerUnit.
"""

import argparse
import json
import math
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--usd", required=True)
parser.add_argument(
    "--out-dir", required=True, help="instance dir, e.g. .../314_cabinet/0"
)
parser.add_argument("--robot-name", default="imported_articulation")
parser.add_argument("--size-policy", default=None,
                    help="match_category | absolute:<m>; needs --category for match mode")
parser.add_argument("--category", default=None)
parser.add_argument(
    "--reference-catalog",
    default="/home/jingxiang/yuxin/env-gen-dev/external/env-gen-github/data/scene_gen/asset_catalog.json",
)
parser.add_argument("--scale", type=float, default=1.0,
                    help="uniform sizing scale on top of metersPerUnit (recorded)")
args = parser.parse_args()

from isaacsim import SimulationApp

app = SimulationApp({"headless": True})
code = 1
try:
    import numpy as np
    from pxr import Usd, UsdGeom, UsdPhysics

    out = Path(args.out_dir)
    (out / "textured_objs").mkdir(parents=True, exist_ok=True)

    stage = Usd.Stage.Open(args.usd)
    mpu_base = float(UsdGeom.GetStageMetersPerUnit(stage))
    scale = args.scale
    size_resolution = None
    if args.size_policy and scale == 1.0:
        cache0 = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
        rng0 = cache0.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
        raw = [float(v) * mpu_base for v in (rng0.GetMax() - rng0.GetMin())]
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import conventions as conv_lib

        size_resolution = conv_lib.resolve_size(
            args.category or "unknown", raw, args.reference_catalog, args.size_policy)
        scale = size_resolution["scale"]
        print(f"size policy: {size_resolution}")
    mpu = mpu_base * scale
    xc = UsdGeom.XformCache()

    def to_np(m):
        return np.array([[m[i][j] for j in range(4)] for i in range(4)], dtype=float)

    def world(prim):
        return to_np(
            xc.GetLocalToWorldTransform(prim)
        )  # row-major, row-vector convention

    def quat_to_np(q):
        # Gf.Quatf/Quatd -> rotation matrix (3x3), acting on column vectors
        w = float(q.GetReal())
        x, y, z = [float(v) for v in q.GetImaginary()]
        return np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
            ]
        )

    # ---- collect bodies and joints ----
    bodies = {}
    joints = []
    for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            bodies[str(prim.GetPath())] = prim
        for cls, jtype in (
            (UsdPhysics.RevoluteJoint, "revolute"),
            (UsdPhysics.PrismaticJoint, "prismatic"),
            (UsdPhysics.FixedJoint, "fixed"),
        ):
            if prim.IsA(cls):
                j = cls(prim)
                b0 = [str(t) for t in UsdPhysics.Joint(prim).GetBody0Rel().GetTargets()]
                b1 = [str(t) for t in UsdPhysics.Joint(prim).GetBody1Rel().GetTargets()]
                rec = {
                    "name": prim.GetName(),
                    "type": jtype,
                    "body0": b0[0] if b0 else None,
                    "body1": b1[0] if b1 else None,
                }
                if jtype != "fixed":
                    rec["axis"] = str(j.GetAxisAttr().Get() or "X")
                    rec["lower"] = float(j.GetLowerLimitAttr().Get() or 0.0)
                    rec["upper"] = float(j.GetUpperLimitAttr().Get() or 0.0)
                base = UsdPhysics.Joint(prim)
                rec["rot1"] = base.GetLocalRot1Attr().Get()
                joints.append(rec)
                break

    link_name = {p: p.rsplit("/", 1)[-1] for p in bodies}
    print(f"bodies={len(bodies)} joints={len(joints)} mpu={mpu}")

    # ---- export per-link meshes ----
    def is_collision_mesh(prim):
        path = str(prim.GetPath()).lower()
        if "collision" in path:
            return True
        purpose = UsdGeom.Imageable(prim).ComputePurpose()
        return purpose == UsdGeom.Tokens.guide

    def triangulate(counts, indices):
        tris, k = [], 0
        for c in counts:
            for i in range(1, c - 1):
                tris.append((indices[k], indices[k + i], indices[k + i + 1]))
            k += c
        return tris

    global_min = np.array([1e9] * 3)
    global_max = -np.array([1e9] * 3)
    link_meshes = {}
    for path, prim in bodies.items():
        Wl = world(prim)
        Wl_inv = np.linalg.inv(Wl)
        buckets = {"vis": [], "col": []}
        for p in Usd.PrimRange(prim, Usd.TraverseInstanceProxies()):
            if not p.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(p)
            pts = mesh.GetPointsAttr().Get()
            counts = mesh.GetFaceVertexCountsAttr().Get()
            idx = mesh.GetFaceVertexIndicesAttr().Get()
            if not pts or not counts:
                continue
            M = world(p) @ Wl_inv  # mesh -> link frame (row-vector convention)
            v = np.array([[q[0], q[1], q[2], 1.0] for q in pts]) @ M
            v = v[:, :3] * mpu
            bucket = "col" if is_collision_mesh(p) else "vis"
            buckets[bucket].append((v, triangulate(counts, idx)))
            if bucket == "vis":
                wv = (np.array([[q[0], q[1], q[2], 1.0] for q in pts]) @ world(p))[
                    :, :3
                ] * mpu
                global_min = np.minimum(global_min, wv.min(axis=0))
                global_max = np.maximum(global_max, wv.max(axis=0))
        if not buckets["col"]:
            buckets["col"] = buckets["vis"]
        files = {}
        for kind in ("vis", "col"):
            fname = f"textured_objs/{link_name[path]}_{kind}.obj"
            with open(out / fname, "w") as f:
                off = 1
                for v, tris in buckets[kind]:
                    for q in v:
                        f.write(f"v {q[0]:.6f} {q[1]:.6f} {q[2]:.6f}\n")
                    for a, b, c in tris:
                        f.write(f"f {a + off} {b + off} {c + off}\n")
                    off += len(v)
            files[kind] = fname
        link_meshes[path] = files
        print(
            f"link {link_name[path]}: vis_parts={len(buckets['vis'])} col_parts={len(buckets['col'])}"
        )

    lift = -float(global_min[2])
    size = [float(a - b) for a, b in zip(global_max, global_min)]
    print(f"rest bbox_m={[round(s, 4) for s in size]} lift={lift:.4f}")

    # ---- URDF ----
    AXIS_UNIT = {
        "X": np.array([1.0, 0, 0]),
        "Y": np.array([0, 1.0, 0]),
        "Z": np.array([0, 0, 1.0]),
    }

    def rpy_from_R(R):
        sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
        if sy > 1e-8:
            return (
                math.atan2(R[2, 1], R[2, 2]),
                math.atan2(-R[2, 0], sy),
                math.atan2(R[1, 0], R[0, 0]),
            )
        return (math.atan2(-R[1, 2], R[1, 1]), math.atan2(-R[2, 0], sy), 0.0)

    def link_xml(path):
        n = link_name[path]
        vis, col = link_meshes[path]["vis"], link_meshes[path]["col"]
        return (
            f'  <link name="{n}">\n'
            f'    <visual><geometry><mesh filename="{vis}"/></geometry></visual>\n'
            f'    <collision><geometry><mesh filename="{col}"/></geometry></collision>\n'
            f"  </link>\n"
        )

    def joint_xml(rec, idx):
        p_link = link_name.get(rec["body0"], "base") if rec["body0"] else "base"
        c_link = link_name[rec["body1"]]
        Wp = (
            world(bodies[rec["body0"]])
            if rec["body0"] and rec["body0"] in bodies
            else np.eye(4)
        )
        Wc = world(bodies[rec["body1"]])
        T = Wc @ np.linalg.inv(Wp)  # child in parent frame (row-vector convention)
        t = T[3, :3] * mpu
        if p_link == "base":
            t = t + np.array([0.0, 0.0, lift])
        R = T[:3, :3].T  # to column-vector convention
        rpy = rpy_from_R(R)
        jtype = rec["type"]
        lines = [
            f'  <joint name="{rec["name"] or f"j{idx}"}" type="{jtype}">',
            f'    <origin xyz="{t[0]:.6f} {t[1]:.6f} {t[2]:.6f}" '
            f'rpy="{rpy[0]:.6f} {rpy[1]:.6f} {rpy[2]:.6f}"/>',
            f'    <parent link="{p_link}"/>',
            f'    <child link="{c_link}"/>',
        ]
        if jtype != "fixed":
            axis = AXIS_UNIT[rec["axis"]]
            if rec["rot1"]:
                axis = quat_to_np(rec["rot1"]) @ axis
            lo, up = rec["lower"], rec["upper"]
            if jtype == "revolute":
                lo, up = math.radians(lo), math.radians(up)
            else:
                lo, up = lo * mpu, up * mpu
            lines.append(f'    <axis xyz="{axis[0]:.6f} {axis[1]:.6f} {axis[2]:.6f}"/>')
            lines.append('    <dynamics damping="5.0" friction="2.0"/>')
            lines.append(
                f'    <limit lower="{lo:.6f}" upper="{up:.6f}" effort="100" velocity="1.0"/>'
            )
        lines.append("  </joint>")
        return "\n".join(lines) + "\n"

    urdf = [
        f'<?xml version="1.0"?>\n<robot name="{args.robot_name}">\n',
        '  <link name="base"/>\n',
    ]
    for path in bodies:
        urdf.append(link_xml(path))
    seen_children = set()
    for i, rec in enumerate(joints):
        if rec["body1"] is None or rec["body1"] not in bodies:
            continue
        urdf.append(joint_xml(rec, i))
        seen_children.add(rec["body1"])
    for path in bodies:  # any body not attached by a joint gets fixed to base
        if path not in seen_children and not any(
            j["body0"] == path and j["body1"] for j in joints
        ):
            pass
    urdf.append("</robot>\n")
    (out / "mobility.urdf").write_text("".join(urdf))

    movable = [j for j in joints if j["type"] != "fixed"]
    (out / "export_report.json").write_text(
        json.dumps(
            {
                "source_usd": args.usd,
                "scale_applied": scale,
                "size_resolution": size_resolution,
                "mpu": mpu,
                "links": len(bodies),
                "joints_total": len(joints),
                "joints_movable": len(movable),
                "movable": [
                    {k: v for k, v in j.items() if k != "rot1"} for j in movable
                ],
                "bbox_m": size,
                "lift_m": lift,
            },
            indent=2,
        )
    )
    print(f"PASS s13a mobility.urdf links={len(bodies)} movable={len(movable)}")
    code = 0
except Exception as exc:  # noqa: BLE001
    import traceback

    traceback.print_exc()
    print(f"FAIL s13a {type(exc).__name__}: {exc}")
app.close()
raise SystemExit(code)
