#!/usr/bin/env python3
"""Runtime-load sweep (env-gen-yuxin env): every accepted external model is
loaded through RoboTwin's REAL create_actor path (the same code env-gen's
scene replay uses), settled, and checked — closing the gap between "loads in
raw SAPIEN" and "loads through the env-gen runtime".

Run with cwd = shadow root (create_actor resolves ./assets/objects/<name>).
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--shadow", required=True)
parser.add_argument("--catalog", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()

os.chdir(args.shadow)
sys.path.insert(0, args.shadow)

import sapien

from envs.utils import create_actor, create_sapien_urdf_obj  # RoboTwin's real loaders

STABLE_Q = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]
cat = json.loads(Path(args.catalog).read_text())
targets = [
    e
    for e in cat["entries"]
    if e["asset_id"].startswith("3")
    and e["asset_id"][0] == "3"
    and e["asset_id"][:3].isdigit()
    and int(e["asset_id"][:3]) >= 301
]

rows = []
for entry in targets:
    for m in entry["models"]:
        if not m.get("usable"):
            continue
        aid, mid = entry["asset_id"], m["model_id"]
        row = {"asset": aid, "model": mid}
        try:
            sc = sapien.Scene()
            sc.set_timestep(1 / 100)
            sc.add_ground(0)
            if m.get("urdf_path"):
                actor = create_sapien_urdf_obj(
                    sc, sapien.Pose(p=[0, 0, 0.005], q=[1, 0, 0, 0]),
                    modelname=aid, modelid=mid, fix_root_link=True)
            else:
                actor = create_actor(
                    sc, sapien.Pose(p=[0, 0, 0.005], q=STABLE_Q),
                    modelname=aid, convex=True, is_static=False, model_id=mid)
            if actor is None:
                raise RuntimeError("create_actor returned None")
            ent = getattr(actor, "actor", actor)
            p0 = np.array(ent.get_pose().p)
            mid_p = None
            for i in range(200):
                sc.step()
                if i == 150:
                    mid_p = np.array(ent.get_pose().p)
            pf = np.array(ent.get_pose().p)
            drift = float(np.linalg.norm(pf - mid_p))
            row.update(
                final_z_m=float(pf[2]),
                late_drift_m=drift,
                finite=bool(np.isfinite(pf).all()),
                settled=drift < 0.002,
                no_penetration=float(pf[2]) > -0.005,
            )
            row["status"] = (
                "pass"
                if row["finite"] and row["settled"] and row["no_penetration"]
                else "fail"
            )
        except Exception as exc:  # noqa: BLE001
            row.update(status="fail", error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
        print(
            f"{row['status'].upper()} {aid} m{mid} "
            f"z={row.get('final_z_m', float('nan')):.4f} "
            f"{row.get('error', '')}"
        )

out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(rows, indent=2))
npass = sum(1 for r in rows if r["status"] == "pass")
print(f"SWEEP {npass}/{len(rows)} pass via RoboTwin create_actor")
sys.exit(0 if npass == len(rows) else 1)
