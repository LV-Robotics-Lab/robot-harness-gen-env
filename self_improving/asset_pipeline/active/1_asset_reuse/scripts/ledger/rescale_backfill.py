#!/usr/bin/env python3
"""Dry-run planner for size-table plan C.

``--apply`` is deliberately disabled.  Publishing two meshes and their ledger
needs a durable crash-recovery transaction plus a fresh physical replay; this
module currently reports the eligible cohort only and must not be described as
an active writer.

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

The retained implementation sketch shows the intended per-model operation:
uniform-scale visual+collision GLBs, update the ledger, then rerun SAPIEN
settle.  It is unreachable from the CLI until that transaction is implemented;
a rescaled mesh would be a new artifact and every old receipt would be stale.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# scripts/ledger/rescale_backfill.py -> active root is three levels up
DEV = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DEV / "1_asset_reuse"))

import trimesh  # noqa: E402
import yaml  # noqa: E402
from lib import ledger as L  # noqa: E402
from lib import ledger_writes  # noqa: E402

RUN_ID = "rescale-planC-20260815"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_bytes(path, payload):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


class _FileTransaction:
    """Stage mesh replacements and make every published byte rollbackable."""

    def __init__(self):
        self._stages = {}
        self._originals = {}

    def stage(self, target):
        target = Path(target)
        fd, name = tempfile.mkstemp(
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".stage" + target.suffix,
        )
        os.close(fd)
        staged = Path(name)
        self._stages[target] = staged
        return staged

    def publish(self, target):
        target = Path(target)
        self._originals.setdefault(target, target.read_bytes())
        os.replace(self._stages[target], target)

    def rollback(self):
        for target, payload in self._originals.items():
            _atomic_write_bytes(target, payload)
        self.commit()

    def commit(self):
        for staged in self._stages.values():
            staged.unlink(missing_ok=True)
        self._stages.clear()
        self._originals.clear()


def _stage_scaled_meshes(asset_dir, model_id, factor, transaction):
    targets = []
    for sub in ("visual", "collision"):
        target = asset_dir / sub / f"base{model_id}.glb"
        staged = transaction.stage(target)
        mesh = trimesh.load(target)
        mesh.apply_transform(trimesh.transformations.scale_matrix(factor))
        mesh.export(staged)
        targets.append(target)
    for target in targets:
        transaction.publish(target)


def _refresh_representation(representation):
    path = L.resolve_uri(representation["uri"])
    representation["sha256"] = sha256(path)
    representation.pop("size_bytes", None)
    representation["files"] = ledger_writes.representation_files(path)


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
    ap.add_argument("--sizes", default=str(DEV / "1_asset_reuse/configs/category_sizes.yml"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if a.apply:
        print(
            "BLOCKED: --apply is disabled because rescaling two meshes and a ledger "
            "cannot be made crash-atomic; use dry-run until a durable transaction "
            "protocol exists",
            file=sys.stderr,
        )
        return 2

    cfg = yaml.safe_load(Path(a.sizes).read_text())
    tbl, view = cfg["sizes"], cfg["views"]["tabletop"]
    fit, cap, refuse = (
        float(view["fit_max_m"]),
        float(view["cap_to_m"]),
        float(view["refuse_over_m"]),
    )

    lib = Path(a.library)
    shadow = Path(a.shadow)
    n_done = n_fail = 0
    for asset_path in L.iter_assets(lib):
        lp = asset_path / "ledger.json"
        if not lp.is_file():
            continue
        led = json.loads(lp.read_text())
        if a.apply:
            existing_violations = L.validate_ledger(led, check_files=True)
            if existing_violations:
                first = existing_violations[0]
                print(
                    f"FAIL {led.get('asset_id', lp.parent.name)}: existing ledger invalid "
                    f"({first.path}:{first.code})"
                )
                n_fail += 1
                continue
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
        expected_ledger = json.loads(json.dumps(led))
        changed = False
        asset_failed = False
        transaction = _FileTransaction()
        staged_done = 0
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
            try:
                mesh0 = trimesh.load(vis_path)
                lo, hi = mesh0.bounds
            except Exception as exc:  # noqa: BLE001
                n_fail += 1
                asset_failed = True
                print(f"FAIL {led['asset_id']} m{m['model_id']}: mesh staging failed: {exc}")
                break
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
                try:
                    _stage_scaled_meshes(asset_dir, mid, factor, transaction)
                except Exception as exc:  # noqa: BLE001
                    transaction.rollback()
                    n_fail += 1
                    asset_failed = True
                    print(f"FAIL {tag}: mesh staging failed: {exc}")
                    break
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
                        _refresh_representation(rep)
            conv = m["physical"].get("conventions") or {}
            pose = next(
                (x for x in conv.get("stable_poses") or [] if x.get("is_default")),
                None,
            )
            try:
                res = settle_check(
                    shadow,
                    asset_dir.name,
                    mid,
                    (pose or {}).get("orientation_wxyz"),
                    conv.get("z_policy", "origin_on_table"),
                    m["physical"]["mesh_bbox_m"],
                )
                digest = L.reps_digest(m, "sapien")
            except Exception as exc:  # noqa: BLE001
                transaction.rollback()
                n_fail += 1
                asset_failed = True
                print(f"FAIL {tag}: settle transaction failed: {exc}")
                break
            if res["verdict"] == "pass" and pose is not None:
                pose["measured_against"] = {
                    "backend": "sapien",
                    "run_id": RUN_ID,
                }
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
                staged_done += 1
                print(f"ok   {tag}: {cur:.3f} -> {target} ({policy}) settle={res}")
            else:
                n_fail += 1
                asset_failed = True
                print(f"FAIL {tag}: settle after rescale: {res}")
            changed = True
        if changed and a.apply:
            violations = L.validate_ledger(led, check_files=True)
            if violations:
                n_fail += 1
                asset_failed = True
                v0 = violations[0]
                print(f"FAIL {led['asset_id']}: ledger invalid after rescale ({v0.path}:{v0.code})")
            if asset_failed:
                transaction.rollback()
                continue
            try:
                ledger_writes.write_validated(lp, led, expected=expected_ledger)
            except Exception as exc:  # noqa: BLE001
                transaction.rollback()
                n_fail += 1
                print(f"FAIL {led['asset_id']}: ledger transaction failed: {exc}")
                continue
            transaction.commit()
            n_done += staged_done
    print(f"\n{'DRY ' if not a.apply else ''}rescaled={n_done} failed={n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
