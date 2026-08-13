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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from runtime_config import ASSET_PIPELINE_ROOT  # noqa: E402

DEV = ASSET_PIPELINE_ROOT

# air kept under a spawned payload, on top of its measured resting height
FLOOR_MARGIN = 0.008


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
                # v3: MEASURE the cavity with a fine 5x5 in-cavity scan
                # instead of estimating it from footprint fractions -- the
                # fraction estimate on basket m3 declared an interior box
                # offset from the real cavity, and an apple physically inside
                # the basket failed the containment check (2026-08-13).
                fine = []
                for fx in (-0.35, -0.175, 0.0, 0.175, 0.35):
                    for fy in (-0.35, -0.175, 0.0, 0.175, 0.35):
                        pb = scene.create_actor_builder()
                        pb.add_box_collision(half_size=[0.01] * 3)
                        pb.add_box_visual(half_size=[0.01] * 3)
                        probe = pb.build(name="probe")
                        x0, y0 = fx * dx, fy * dy
                        probe.set_pose(sapien.Pose([x0, y0, dz + 0.04]))
                        for _ in range(250):
                            scene.step()
                        p = probe.get_pose().p
                        fine.append(
                            {
                                "xy": [round(float(p[0]), 4), round(float(p[1]), 4)],
                                "bottom_z": round(float(p[2]) - 0.01, 4),
                            }
                        )
                        probe.remove_from_scene()
                sunk = [f for f in fine if 0.003 < f["bottom_z"] < dz_ref - 0.03]
                row["verdict"] = "hollow"
                if sunk:
                    xs = [f["xy"][0] for f in sunk]
                    ys = [f["xy"][1] for f in sunk]
                    floor = min(f["bottom_z"] for f in sunk)
                    pad = 0.012  # probe half + settle scatter margin
                    # the upstream interior box is CENTRED on the asset
                    # origin; an off-centre cavity must be declared as its
                    # symmetric inscription or the box juts into the wall
                    hx = min(abs(min(xs)), abs(max(xs))) + pad
                    hy = min(abs(min(ys)), abs(max(ys))) + pad
                    row["interior"] = {
                        "floor_z_offset_m": round(floor, 4),
                        "dimensions_m": [
                            round(2 * hx, 4),
                            round(2 * hy, 4),
                            round(dz_ref - floor - 0.005, 4),
                        ],
                        "cavity_span_raw": [
                            round(min(xs), 4),
                            round(max(xs), 4),
                            round(min(ys), 4),
                            round(max(ys), 4),
                        ],
                        "method": "fine 5x5 in-cavity scan, symmetric inscription",
                    }
                else:
                    floor = min(r["bottom_z"] for r in hollow_cells)
                    row["interior"] = {
                        "floor_z_offset_m": round(floor, 4),
                        "dimensions_m": [
                            round(dx * 0.55, 4),
                            round(dy * 0.55, 4),
                            round(dz_ref - floor - 0.005, 4),
                        ],
                    }
                # v4 load test: an interior is only worth publishing if the
                # container survives RECEIVING an object. basket m3 is stable
                # empty and stable under a token 4 cm cube, but the block the
                # solver actually fits into its 12 cm cavity rolled it 71 deg
                # at runtime (B11, 2026-08-13) -- so the probe payload is
                # sized to the PUBLISHED cavity (80% of its smaller footprint
                # side), which is the largest thing the solver may put there.
                if row.get("interior"):
                    it = row["interior"]
                    idims = it["dimensions_m"]
                    half = max(0.02, min(0.4 * min(idims[0], idims[1]), 0.06))
                    scene2 = sapien.Scene()
                    scene2.set_timestep(1 / 250)
                    scene2.add_ground(0.0)
                    tgt = create_actor(
                        scene2,
                        pose=sapien.Pose([0, 0, pose_z], q),
                        modelname=aid,
                        model_id=mid,
                        convex=True,
                        is_static=False,
                    )
                    if tgt is not None:
                        for _ in range(120):
                            scene2.step()
                        q0 = np.array(tgt.get_pose().q)
                        pb = scene2.create_actor_builder()
                        pb.add_box_collision(half_size=[half] * 3)
                        pb.add_box_visual(half_size=[half] * 3)
                        load = pb.build(name="load")
                        # drop it from clear air above the cavity so where it
                        # STOPS is the honest resting height, not the height
                        # we guessed from the probe
                        load.set_pose(
                            sapien.Pose([0, 0, it["floor_z_offset_m"] + half + 0.02])
                        )
                        row["load_half_size_m"] = round(float(half), 4)
                        for _ in range(400):
                            scene2.step()
                        q1 = np.array(tgt.get_pose().q)
                        tilt = 2 * np.degrees(
                            np.arccos(min(1.0, abs(float(np.dot(q0, q1)))))
                        )
                        row["load_tilt_deg"] = round(float(tilt), 2)
                        # A 2 cm probe drops into gaps between the decomposed
                        # convex pieces (or through a wire basket's grid) and
                        # reports a floor no real payload can reach; spawning
                        # a block 3 cm below where it can actually rest made
                        # the runtime depenetration impulse lever the whole
                        # basket over (B11, 2026-08-13). The published floor
                        # is therefore where a CAVITY-SIZED payload rests,
                        # and never lower than the probe's answer.
                        payload_floor = float(load.get_pose().p[2]) - half
                        row["payload_floor_z_m"] = round(payload_floor, 4)
                        # FLOOR_MARGIN: the runtime's depenetration is far
                        # less forgiving than this rig -- a spawn that merely
                        # grazes geometry here levers the container over
                        # there, so the published floor keeps real air under
                        # the payload.
                        floor_pub = payload_floor + FLOOR_MARGIN
                        if floor_pub > it["floor_z_offset_m"]:
                            it["floor_z_offset_m"] = round(floor_pub, 4)
                            it["dimensions_m"][2] = round(dz_ref - floor_pub - 0.005, 4)
                            it["floor_basis"] = f"payload rest +{FLOOR_MARGIN}m margin"
                        if it["dimensions_m"][2] < 0.03:
                            sup = row.pop("interior")
                            sup["reason"] = "cavity under 3 cm once floor is honest"
                            row["interior_suppressed"] = sup
                            row["load_tilt_deg"] = round(float(tilt), 2)
                            counts[row["verdict"]] += 1
                            survey.setdefault(aid, {})[str(mid)] = row
                            print(f"{aid}/m{mid}: {row['verdict']} (interior dropped)")
                            continue
                        if tilt > 5.0:
                            sup = row.pop("interior")
                            sup["reason"] = f"load test tipped container {tilt:.1f} deg"
                            row["interior_suppressed"] = sup
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
