#!/usr/bin/env python3
"""Runtime-load sweep (env-gen-yuxin env): every accepted external model is
loaded through RoboTwin's REAL create_actor path (the same code env-gen's
scene replay uses), settled, and checked — closing the gap between "loads in
raw SAPIEN" and "loads through the env-gen runtime".

Run with cwd = shadow root (create_actor resolves ./assets/objects/<name>).

After the sweep, each row is backfilled as a runtime_load verification entry
onto the swept model's authoritative per-asset ledger (--library-dir);
assets with no ledger yet (not backfilled to v1) are reported and skipped.
"""

import argparse
import datetime
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import ledger  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--shadow", required=True)
parser.add_argument("--catalog", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--library-dir", default="../data/asset_library")
args = parser.parse_args()

# Resolve before os.chdir(args.shadow) below -- the default is relative to
# the invocation cwd (repo convention: run from 1_asset_reuse/), not to the
# shadow root the script chdirs into for create_actor's relative lookups.
library_dir = Path(args.library_dir).resolve()

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

for row in rows:
    lp = ledger.ledger_path(library_dir, row["asset"])
    if not lp.exists():
        print(
            f"SKIP backfill {row['asset']} m{row['model']}: no ledger at {lp} "
            "(not yet backfilled to v1)"
        )
        continue
    led = json.loads(lp.read_text())
    model = next(
        (x for x in led["models"] if x["model_id"] == row["model"]), None
    )
    if model is None:
        print(
            f"SKIP backfill {row['asset']} m{row['model']}: "
            "no matching model_id in ledger"
        )
        continue
    ledger.append_verification(
        lp,
        row["model"],
        {
            "backend": "sapien",
            "check": "runtime_load",
            "verdict": row["status"],
            "run_id": out.stem,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "verified_digest": ledger.reps_digest(model, "sapien"),
            "report_path": str(out),
        },
    )

npass = sum(1 for r in rows if r["status"] == "pass")
print(f"SWEEP {npass}/{len(rows)} pass via RoboTwin create_actor")
sys.exit(0 if npass == len(rows) else 1)
