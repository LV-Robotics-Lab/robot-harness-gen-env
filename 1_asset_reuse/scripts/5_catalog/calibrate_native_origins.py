#!/usr/bin/env python3
"""One-off (work/oneoff/): measure every rigid RoboTwin native's TRUE loaded
geometry in SAPIEN and derive placement metadata the solver can trust.

Why this exists (measured 2026-08-12): the natives' mesh origins are not one
convention -- 110_basket loads with its origin at the mesh BOTTOM, 035_apple
with its origin at the mesh CENTER -- while the upstream overrides declare
`origin_on_table` for both. A center-origin apple placed origin-on-plane
spawns half-sunk; alone on a table PhysX pops it out gently and the check
passes, but INSIDE a basket the same error buries it through the basket floor
AND the tabletop at once, and the resolver impulse ejects it 1.4 m ("the
apple flies out"). Separately, ~110 natives have NO override entry at all, so
the catalog marks them unusable and a dumbbell-rack request walks to an
external-search blocker while 013_dumbbell-rack sits in the library.

Method, per model: load via RoboTwin's own create_actor (convex=True -- the
exact runtime path), drop onto a ground plane from 2 cm, settle 800 steps,
then REVERIFY by respawning at the derived pose and requiring it to stay put
(<1 cm drift, <3 deg tilt, 300 steps). Only reverified entries are accepted.
Output: data/scene_gen_ext/native_origin_calibration.json, consumed by
s9_build_shadow_root when it generates asset_overrides_ext.yml.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")


def quat_angle_deg(q1, q2):
    d = abs(float(np.dot(np.asarray(q1), np.asarray(q2))))
    return float(np.degrees(2 * np.arccos(min(1.0, d))))


def shape_points(actor):
    import sapien.core as sapien

    body = actor.find_component_by_type(
        sapien.pysapien.physx.PhysxRigidDynamicComponent
    )
    pts = []
    for sh in body.get_collision_shapes():
        local = sh.get_local_pose().to_transformation_matrix()
        v = np.asarray(sh.get_vertices()) * np.asarray(sh.get_scale())
        pts.append((local[:3, :3] @ v.T).T + local[:3, 3])
    return np.vstack(pts)


def world_aabb(actor):
    T = actor.get_pose().to_transformation_matrix()
    w = (T[:3, :3] @ shape_points(actor).T).T + T[:3, 3]
    return w.min(0), w.max(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--catalog", default=str(DEV / "data/scene_gen_ext/asset_catalog.json")
    )
    ap.add_argument("--shadow", default=str(DEV / "data/robotwin_shadow"))
    ap.add_argument(
        "--out", default=str(DEV / "data/scene_gen_ext/native_origin_calibration.json")
    )
    ap.add_argument(
        "--only",
        nargs="*",
        help="asset_ids to (re)calibrate; default all rigid natives",
    )
    a = ap.parse_args()

    os.chdir(a.shadow)
    sys.path.insert(0, a.shadow)
    import sapien.core as sapien
    from envs.utils import create_actor

    import yaml

    ov_path = DEV / "data/scene_gen_ext/asset_overrides_ext.yml"
    _raw = yaml.safe_load(ov_path.read_text()) if ov_path.exists() else {}
    # upstream schema nests entries under "assets"; earlier ext files were flat
    overrides = {
        **(_raw.get("assets") or {}),
        **{k: v for k, v in _raw.items() if k not in ("schema_version", "assets")},
    }

    cat = json.load(open(a.catalog))
    entries = cat["entries"] if isinstance(cat, dict) else cat
    todo = []
    for e in entries:
        aid = e["asset_id"]
        # default domain is rigid NATIVES (<300): externals get their pose
        # measured at import. But import settle proved looser than the
        # upstream runtime window (336/339/343/359, 2026-08-13 sweep), so an
        # explicit --only may name externals for the same measurement.
        if not a.only and not (aid[:3].isdigit() and int(aid[:3]) < 300):
            continue
        if a.only and aid not in a.only:
            continue
        for m in e.get("models", []):
            if m.get("load_type") == "urdf" or m.get("urdf_path"):
                continue
            todo.append((aid, m["model_id"]))
    todo = sorted(set(todo))
    print(f"待校准: {len(todo)} 个刚体模型")

    engine = sapien.Engine()

    def settle(scene, act, max_steps):
        """Step until velocities stay tiny for 50 consecutive steps. Thin
        objects (trays) bounce and rock well past a fixed 800-step budget --
        the tray class failed reverify at 36-77 deg purely from unfinished
        rocking (measured 2026-08-13)."""
        body = act.find_component_by_type(
            sapien.pysapien.physx.PhysxRigidDynamicComponent
        )
        quiet = 0
        for i in range(max_steps):
            scene.step()
            v = np.linalg.norm(body.get_linear_velocity())
            w = np.linalg.norm(body.get_angular_velocity())
            quiet = quiet + 1 if (v < 1e-3 and w < 0.02) else 0
            if quiet >= 50:
                return True
        return False

    def attempt(aid, mid, q0):
        scene = engine.create_scene()
        scene.set_timestep(1 / 250)
        scene.add_ground(0.0)
        actor0 = create_actor(
            scene,
            pose=sapien.Pose([0, 0, 0], q0),
            modelname=aid,
            model_id=mid,
            convex=True,
        )
        if actor0 is None:
            raise RuntimeError("create_actor returned None")
        act = actor0.actor
        lo, _hi = world_aabb(act)
        act.set_pose(sapien.Pose([0, 0, -float(lo[2]) + 0.005], q0))
        settle(scene, act, 2000)
        p, q_rest = act.get_pose().p, act.get_pose().q
        lo, hi = world_aabb(act)
        dims = [float(hi[i] - lo[i]) for i in range(3)]
        h = float(p[2])
        half = dims[2] / 2
        if abs(h) < 0.008:
            zp = "origin_on_table"
        elif abs(h - half) < max(0.008, 0.15 * half):
            zp = "center_on_table"
        else:
            zp = None
        row = dict(
            origin_height_m=round(h, 4),
            dims_m=[round(d, 4) for d in dims],
            rest_orientation_wxyz=[round(float(x), 6) for x in q_rest],
            center_offset_xy_m=[
                round(float((lo[0] + hi[0]) / 2 - p[0]), 4),
                round(float((lo[1] + hi[1]) / 2 - p[1]), 4),
            ],
        )
        if max(dims) > 0.8:
            row["verdict"] = (
                f"oversized_needs_scale (dims={[round(d, 2) for d in dims]})"
            )
            return row
        if zp is None:
            row["verdict"] = f"unsupported_origin (h={h:.3f}, half={half:.3f})"
            return row
        spawn_z = 0.001 if zp == "origin_on_table" else half + 0.001
        act.set_pose(sapien.Pose([0, 0, spawn_z], q_rest))
        body = act.find_component_by_type(
            sapien.pysapien.physx.PhysxRigidDynamicComponent
        )
        body.set_linear_velocity([0, 0, 0])
        body.set_angular_velocity([0, 0, 0])
        settle(scene, act, 600)
        p2, q2 = act.get_pose().p, act.get_pose().q
        drift = float(np.linalg.norm(np.asarray(p2) - [0, 0, spawn_z]))
        tilt = quat_angle_deg(q2, q_rest)
        row.update(reverify_drift_m=round(drift, 4), reverify_tilt_deg=round(tilt, 2))
        if drift < 0.02 and tilt < 3.0:
            row.update(verdict="ok", z_policy=zp)
        else:
            row["verdict"] = f"reverify_failed (drift={drift:.3f}, tilt={tilt:.1f})"
        return row

    # Start orientations: the declared one first, then axis flips. A Y-up
    # tray at identity starts standing on edge; where it topples from there
    # is not a stable pose measurement. One of the flips usually starts the
    # object already lying on its natural face.
    R2 = 0.7071067811865476
    CANDIDATE_Q = [
        [1, 0, 0, 0],
        [R2, R2, 0, 0],
        [R2, -R2, 0, 0],
        [R2, 0, R2, 0],
        [R2, 0, -R2, 0],
    ]

    result = {}
    n_ok = 0
    for aid, mid in todo:
        decl = (overrides.get(aid) or {}).get("models", {}).get(str(mid), {})
        declared_q = decl.get("stable_orientation_wxyz")
        tries = ([declared_q] if declared_q else []) + [
            q for q in CANDIDATE_Q if q != declared_q
        ]
        row = {"declared_z_policy": decl.get("z_policy"), "had_override": bool(decl)}
        best = None
        try:
            # Declared metadata gets NO free pass: place the model exactly as
            # the solver would from its declaration and require it to stay
            # put. 020_hammer's upstream "lie_flat + origin_on_table" left
            # its origin hovering 3.5 cm up; a bare "Place a hammer" scene
            # toppled at runtime while the declaration was trusted
            # (measured 2026-08-13, prompt-matrix campaign).
            if decl.get("z_policy"):
                dr = attempt(aid, mid, declared_q or [1, 0, 0, 0])
                row["declared_reverify"] = {
                    k: dr.get(k)
                    for k in (
                        "verdict",
                        "reverify_drift_m",
                        "reverify_tilt_deg",
                        "origin_height_m",
                    )
                }
                row["declared_trusted"] = dr.get("verdict") == "ok" and dr.get(
                    "z_policy"
                ) == decl.get("z_policy")

            for i, q0 in enumerate(tries):
                r = attempt(aid, mid, q0)
                if best is None:
                    best = r
                if r.get("verdict") == "ok":
                    best = r
                    best["start_orientation_attempt"] = i
                    break
        except Exception as ex:  # noqa: BLE001 -- recorded per model
            best = {"verdict": f"error: {str(ex)[:120]}"}
        row.update(best or {})
        if row.get("verdict") == "ok":
            n_ok += 1
        result.setdefault(aid, {})[str(mid)] = row
        flag = "OK " if row.get("verdict") == "ok" else "-- "
        print(
            f"{flag}{aid}/m{mid}: {row.get('verdict')} zp={row.get('z_policy')} h={row.get('origin_height_m')}",
            flush=True,
        )

    out = {
        "schema": "envgen.native_origin_calibration.v1",
        "method": "sapien drop + velocity-converged settle (<=2000 steps), multi start-orientation retry, reverify at derived pose (create_actor convex=True)",
        "measured_at": date.today().isoformat(),
        "accepted": n_ok,
        "models": result,
    }
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"\n校准完成: {n_ok}/{len(todo)} accepted -> {a.out}")


if __name__ == "__main__":
    main()
