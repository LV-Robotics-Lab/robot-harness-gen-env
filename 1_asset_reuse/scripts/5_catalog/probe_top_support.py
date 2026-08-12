#!/usr/bin/env python3
"""Survey every usable model's TOP: can it actually support another object?

Why (measured 2026-08-13): the solver treats any object's bbox top as a flat
platform. Reality disagrees in two ways that both ended as runtime failures
her Studio surfaced:

  hollow tops     "put a duck on the cup" -- the newly admitted native cup's
                  bbox top "fits" the duck statically, but the top is a rim
                  around a cavity; the duck topples off within 600 steps.
  split/tilted    "put a mug on the laptop" -- the laptop spawns with its lid
                  open, so the bbox top is the screen edge; the mug slides
                  straight off. (The usable deck is off-centre, which the
                  upstream schema cannot express -- see the alignment list.)

Method, per usable rigid model: spawn it STATIC at its solver pose on a
ground plane, drop a 2 cm probe cube on a 3x3 grid over its top bbox, settle
each 250 steps, classify by where probes come to rest:

  flat            >=7/9 probes rest at the top plane -> bbox-top support is
                  accurate; declare nothing.
  hollow          any probe sinks >=3 cm below the top while staying inside
                  the footprint -> container: record measured interior
                  (floor offset + conservative dims) and mark the top
                  unsupportive.
  unsupportive    everything else (probes slide off / uneven) -> mark the
                  top unsupportive so the solver refuses honestly at solve
                  time instead of the runtime check failing after the fact.

"Unsupportive" is written as a 2x2 mm support surface -- no real footprint
passes the margin check against it, which is exactly the point. Existing
hand-declared support/interior data always wins over this survey.

Output: data/scene_gen_ext/top_support_survey.json, merged by
s9_build_shadow_root into asset_overrides_ext.yml.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np

DEV = Path("/home/jingxiang/yuxin/env-gen-dev")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--catalog", default=str(DEV / "data/scene_gen_ext/asset_catalog.json")
    )
    ap.add_argument("--shadow", default=str(DEV / "data/robotwin_shadow"))
    ap.add_argument(
        "--out", default=str(DEV / "data/scene_gen_ext/top_support_survey.json")
    )
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()

    os.chdir(a.shadow)
    sys.path.insert(0, a.shadow)
    import sapien.core as sapien
    import yaml
    from envs.utils import create_actor

    _raw = yaml.safe_load(
        (DEV / "data/scene_gen_ext/asset_overrides_ext.yml").read_text()
    )
    overrides = _raw.get("assets") or {}

    cat = json.load(open(a.catalog))
    entries = cat["entries"] if isinstance(cat, dict) else cat
    todo = []
    for e in entries:
        aid = e["asset_id"]
        if a.only and aid not in a.only:
            continue
        for m in e.get("models", []):
            if (
                not m.get("usable")
                or m.get("load_type") == "urdf"
                or m.get("urdf_path")
            ):
                continue
            todo.append((aid, m["model_id"], tuple(m.get("dimensions_m") or ())))
    todo = sorted(set(todo))
    print(f"待探测: {len(todo)} 个可用刚体模型")

    engine = sapien.Engine()
    survey = {}
    counts = {"flat": 0, "hollow": 0, "unsupportive": 0, "error": 0}
    for aid, mid, dims in todo:
        decl = (overrides.get(aid) or {}).get("models", {}).get(str(mid), {})
        q = decl.get("stable_orientation_wxyz", [1, 0, 0, 0])
        zp = decl.get("z_policy", "origin_on_table")
        row = {"z_policy": zp}
        try:
            if not dims or len(dims) != 3:
                raise RuntimeError("no dimensions in catalog")
            dx, dy, dz = dims
            scene = sapien.Scene()
            scene.set_timestep(1 / 250)
            scene.add_ground(0.0)
            pose_z = 0.0 if zp == "origin_on_table" else dz / 2
            target = create_actor(
                scene,
                pose=sapien.Pose([0, 0, pose_z], q),
                modelname=aid,
                model_id=mid,
                convex=True,
                is_static=True,
            )
            if target is None:
                raise RuntimeError("create_actor returned None")
            rests = []
            for gx in (-0.3, 0.0, 0.3):
                for gy in (-0.3, 0.0, 0.3):
                    pb = scene.create_actor_builder()
                    pb.add_box_collision(half_size=[0.01] * 3)
                    pb.add_box_visual(half_size=[0.01] * 3)
                    probe = pb.build(name="probe")
                    x0, y0 = gx * dx, gy * dy
                    probe.set_pose(sapien.Pose([x0, y0, dz + 0.04]))
                    for _ in range(250):
                        scene.step()
                    p = probe.get_pose().p
                    rests.append(
                        {
                            "cell": [gx, gy],
                            "bottom_z": round(float(p[2]) - 0.01, 4),
                            "xy_drift": round(float(np.hypot(p[0] - x0, p[1] - y0)), 4),
                        }
                    )
                    probe.remove_from_scene()
            top_cells = [
                r
                for r in rests
                if abs(r["bottom_z"] - dz) < 0.012 and r["xy_drift"] < 0.03
            ]
            # Reference plane = the highest place a probe actually RESTED,
            # not the declared bbox top: a lie-flat hammer whose declared
            # dims are its upright bbox (0.178 m) read as "hollow" against
            # that phantom top and was assigned an interior it does not have
            # (measured 2026-08-13).
            resting = [
                r for r in rests if r["xy_drift"] < 0.03 and r["bottom_z"] > 0.003
            ]
            dz_ref = max((r["bottom_z"] for r in resting), default=dz)
            if abs(dz_ref - dz) < 0.012:
                top_cells = [r for r in resting if abs(r["bottom_z"] - dz_ref) < 0.012]
            hollow_cells = [r for r in resting if r["bottom_z"] < dz_ref - 0.03]
            row["cells"] = rests
            row["dz_ref"] = round(float(dz_ref), 4)
            if len(top_cells) >= 7:
                row["verdict"] = "flat"
            elif hollow_cells:
                floor = min(r["bottom_z"] for r in hollow_cells)
                # interior extent from where probes actually sank: a +-0.3
                # ring cell sinking too means the cavity spans ~0.75 of the
                # footprint; centre-only sinking keeps a conservative 0.55.
                # (A flat 0.5 rejected a 6.6 cm apple from a 13 cm bowl whose
                # real cavity is ~9 cm -- measured 2026-08-13.)
                ring = any(
                    max(abs(r["cell"][0]), abs(r["cell"][1])) >= 0.3
                    for r in hollow_cells
                )
                k = 0.75 if ring else 0.55
                row["verdict"] = "hollow"
                row["interior"] = {
                    "floor_z_offset_m": round(floor, 4),
                    "dimensions_m": [
                        round(dx * k, 4),
                        round(dy * k, 4),
                        round(dz_ref - floor - 0.005, 4),
                    ],
                }
            else:
                row["verdict"] = "unsupportive"
            counts[row["verdict"]] += 1
        except Exception as ex:  # noqa: BLE001
            row["verdict"] = f"error: {str(ex)[:100]}"
            counts["error"] += 1
        survey.setdefault(aid, {})[str(mid)] = row
        print(f"{aid}/m{mid}: {row['verdict']}")

    Path(a.out).write_text(
        json.dumps(
            {
                "schema": "envgen.top_support_survey.v1",
                "method": "3x3 probe-cube drop on static target, 250 steps each",
                "measured_at": date.today().isoformat(),
                "counts": counts,
                "models": survey,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"\n探测完成 {counts} -> {a.out}")


if __name__ == "__main__":
    main()
