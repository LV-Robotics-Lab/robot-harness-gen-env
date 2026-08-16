#!/usr/bin/env python3
"""One-shot backfill (size-table plan C): rescale the absolute:0.25 cohort.

Scope rule, and why it is narrow on purpose:
  * ONLY models whose size_resolution.mode startswith "absolute:" -- their
    current size is a known-arbitrary normalization, so replacing it with the
    category-typical size strictly increases truth.
  * Models sized by evidence (match_category against YCB/native precedents)
    are NOT touched: overwriting a measured size with a class-typical one
    would destroy information.
  * Categories the tabletop view refuses (sofa, shelf, ...) are NOT touched
    either -- the view excludes them at s9 time; the asset keeps whatever
    size it has until a real-size re-acquisition for some future view.

Per (asset, model): uniform-scale visual+collision GLBs in place, update
ledger (mesh_bbox_m, size_resolution, representations[].sha256), then re-run
a SAPIEN settle verification at the declared pose and append it to
verification[] -- a rescaled mesh is a new artifact and the old settle pass
is stale by digest, exactly as the contract intends.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

# scripts/ledger/rescale_backfill.py -> active root is three levels up
DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

import trimesh  # noqa: E402
import yaml  # noqa: E402

from lib import ledger as L  # noqa: E402

RUN_ID = "rescale-planC-20260815"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def settle_check(shadow: Path, asset_dir_name: str, model_id: int, q, z_policy, dims):
    """Drop at declared pose on a ground plane, settle, measure drift/tilt.
    Same standard the import gate applies (create_actor convex=True)."""
    import os

    cwd = Path.cwd()
    os.chdir(shadow)
    sys.path.insert(0, str(shadow))
    try:
        import numpy as np
        import sapien.core as sapien
        from envs.utils import create_actor

        scene = sapien.Scene()
        scene.set_timestep(1 / 250)
        scene.add_ground(0.0)
        z0 = 0.0 if z_policy == "origin_on_table" else dims[2] / 2
        a = create_actor(
            scene,
            pose=sapien.Pose([0, 0, z0 + 0.002], q or [1, 0, 0, 0]),
            modelname=asset_dir_name,
            model_id=model_id,
            convex=True,
        )
        if a is None:
            return {"verdict": "fail", "error": "create_actor None"}
        ent = a.actor if hasattr(a, "actor") else a
        q0 = np.array(ent.get_pose().q)
        for _ in range(600):
            scene.step()
        q1 = np.array(ent.get_pose().q)
        p1 = ent.get_pose().p
        tilt = float(2 * np.degrees(np.arccos(min(1.0, abs(float(np.dot(q0, q1)))))))
        drift = float(np.linalg.norm(np.array(p1[:2])))
        ok = tilt < 10.0 and drift < 0.05
        return {
            "verdict": "pass" if ok else "fail",
            "tilt_deg": round(tilt, 2),
            "xy_drift_m": round(drift, 4),
        }
    finally:
        os.chdir(cwd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default=str(DEV / "data/asset_library"))
    ap.add_argument("--shadow", default=str(DEV / "data/robotwin_shadow"))
    ap.add_argument(
        "--sizes", default=str(DEV / "1_asset_reuse/configs/category_sizes.yml")
    )
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.sizes).read_text())
    tbl, view = cfg["sizes"], cfg["views"]["tabletop"]
    fit, cap, refuse = (
        float(view["fit_max_m"]),
        float(view["cap_to_m"]),
        float(view["refuse_over_m"]),
    )

    lib = Path(a.library)
    shadow = Path(a.shadow)
    n_done = n_skip = n_fail = 0
    for lp in sorted(lib.glob("*/ledger.json")):
        led = json.loads(lp.read_text())
        cat = led.get("category")
        row = tbl.get(cat)
        if not row:
            continue
        typical = float(row["size_m"])
        if typical > refuse:
            continue  # view excludes; asset untouched
        target = typical if typical <= fit else cap
        policy = f"category:{typical}" if typical <= fit else f"capped:{cap}"
        asset_dir = lp.parent
        changed = False
        for m in led.get("models", []):
            sr = (m.get("physical") or {}).get("size_resolution") or {}
            if not str(sr.get("mode", "")).startswith("absolute:"):
                continue  # evidence-sized or already migrated: hands off
            # measure the mesh ON DISK: the first apply run rewrote GLBs
            # before ledger validation failed, so ledger bbox and file no
            # longer agree -- disk truth makes the script idempotent (an
            # already-rescaled file yields factor~1 and only the ledger
            # bookkeeping completes).
            vis_path = asset_dir / "visual" / f"base{m['model_id']}.glb"
            mesh0 = trimesh.load(vis_path)
            lo, hi = mesh0.bounds
            cur = float(max(b - a2_ for a2_, b in zip(lo, hi)))
            factor = target / cur
            mid = m["model_id"]
            tag = f"{led['asset_id']} m{mid}"
            need_mesh = abs(factor - 1) >= 0.05
            # factor~1 with mode still absolute: = a half-finished earlier
            # run (mesh rescaled, ledger not) -- fall through to complete
            # the ledger bookkeeping without touching the mesh again.
            if not a.apply:
                print(
                    f"DRY  {tag}: {cur:.3f} -> {target} ({policy})"
                    + ("" if need_mesh else "  [ledger-only]")
                )
                n_done += 1
                continue
            if need_mesh:
                for sub in ("visual", "collision"):
                    gp = asset_dir / sub / f"base{mid}.glb"
                    mesh = trimesh.load(gp)
                    mesh.apply_transform(trimesh.transformations.scale_matrix(factor))
                    mesh.export(gp)
            new_bounds = [round(float((b - a2_) * factor), 6) for a2_, b in zip(lo, hi)]
            m["physical"]["mesh_bbox_m"] = new_bounds
            sr["mode"] = policy
            # satisfy the size invariant EXACTLY (max(bbox) == actual*scale):
            # deriving scale from the invariant instead of multiplying the
            # old one keeps half-finished earlier runs repairable.
            actual = float(sr.get("actual_max_dim_m") or 0) or None
            if actual:
                sr["scale"] = max(new_bounds) / actual
            else:
                sr["scale"] = float(sr.get("scale", 1.0)) * factor
            sr["reference_max_dim_m"] = target
            sr["verdict"] = "scaled"
            sr["rescaled_by"] = RUN_ID
            for rep in m.get("representations", []):
                uri = rep.get("uri") or ""
                if uri.endswith(f"base{mid}.glb"):
                    p = L.resolve_uri(uri)
                    if p.is_file():
                        rep["sha256"] = sha256(p)
            conv = m["physical"].get("conventions") or {}
            pose = next(
                (x for x in conv.get("stable_poses") or [] if x.get("is_default")),
                None,
            )
            res = settle_check(
                shadow,
                asset_dir.name,
                mid,
                (pose or {}).get("orientation_wxyz"),
                conv.get("z_policy", "origin_on_table"),
                m["physical"]["mesh_bbox_m"],
            )
            digest = L.reps_digest(m, "sapien")
            m.setdefault("verification", []).append(
                {
                    "backend": "sapien",
                    "check": "settle",
                    "verdict": res["verdict"],
                    "run_id": RUN_ID,
                    # canonical 19-char naive form the validator enforces
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "verified_digest": digest,
                }
            )
            if res["verdict"] == "pass":
                n_done += 1
                print(f"ok   {tag}: {cur:.3f} -> {target} ({policy}) settle={res}")
            else:
                n_fail += 1
                print(f"FAIL {tag}: settle after rescale: {res}")
            changed = True
        if changed and a.apply:
            violations = [
                v
                for v in L.validate_ledger(led, check_files=False)
                if v.code != "profile_requirement_unmet"
            ]
            if violations:
                n_fail += 1
                v0 = violations[0]
                print(
                    f"FAIL {led['asset_id']}: ledger invalid after rescale "
                    f"({v0.path}:{v0.code})"
                )
                continue
            lp.write_text(json.dumps(led, indent=2) + "\n")
    print(f"\n{'DRY ' if not a.apply else ''}rescaled={n_done} failed={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
