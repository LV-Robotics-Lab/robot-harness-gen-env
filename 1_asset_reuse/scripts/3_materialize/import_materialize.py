#!/usr/bin/env python3
"""Batch external import, phase 2 (env-gen-yuxin env).

Reads staging_manifest.json, normalizes every converted GLB to RoboTwin
conventions (upAxis-driven Z->Y rotation, origin at bottom center), materializes
multi-model asset dirs under data/asset_library/, runs a SAPIEN settle check per
model, writes per-model AssetBundles, an import matrix, and the overrides
fragment consumed by the catalog builder.

Gates per model: settled (<2mm late drift), no ground penetration (>-5mm),
tilt < 15 deg (45 deg for 'flat' items). Failures are recorded with reasons and
excluded from the overrides fragment (= excluded from the catalog).
"""

import argparse
import datetime as dt
import hashlib
import sys
import json
import math
import shutil
from pathlib import Path

import numpy as np
import sapien
import trimesh

# lib/ is two levels up from scripts/3_materialize/ (parents[2]); gen_fragment.py
# lives in scripts/ledger/ (parents[1] / "ledger"). Both inserts must land before
# any `from lib import ...` below (fix I-3: an earlier `import conventions as
# conv_lib` used a separate, miscalculated `parent.parent / "lib"` insert that
# pointed at a nonexistent scripts/lib and broke both production entry points;
# folded into one correct set of inserts + `from lib import conventions` so
# there's exactly one loaded copy of lib.conventions, not a second shadow module
# under a bare "conventions" name).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ledger"))
from lib import conventions as conv_lib  # noqa: E402
from lib import ledger as ledger_mod  # noqa: E402
from lib.ledger import (  # noqa: E402
    ledger_path,
    new_model_entry,
    reps_digest,
    to_ir_bundles,
    upsert_model,
    validate_ledger,
    unknown_inertial,
)
import gen_fragment  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--staging", required=True)
parser.add_argument("--library-dir", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--overrides-fragment", required=True)
parser.add_argument(
    "--identity-basis",
    required=True,
    choices=("manifest_human", "requested_by_acquire", "vlm"),
    help="where these assets' category/aliases came from: hand-written into "
    "a manifest, or asserted as a retrieval query by acquire_batch",
)
parser.add_argument(
    "--identity-evidence",
    default=None,
    help="path to the manifest / selection evidence backing that claim",
)
parser.add_argument(
    "--reference-catalog",
    default="/home/jingxiang/yuxin/env-gen-dev/external/env-gen-github/data/scene_gen/asset_catalog.json",
    help="catalog used for convention inheritance and category sizing",
)
parser.add_argument(
    "--only-index",
    type=int,
    default=None,
    help="worker mode: process a single record (crash isolation)",
)
args = parser.parse_args()


IDENTITY = {
    "basis": args.identity_basis,
    "evidence": args.identity_evidence,
    # basis=vlm means the acquire-side gate already looked at the source
    # thumbnail and answered positively; hardcoding False here contradicted
    # the evidence file the record itself points to.
    "verified": args.identity_basis == "vlm",
}
staging = Path(args.staging)
lib = Path(args.library_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
(out / "shots").mkdir(exist_ok=True)
records = json.loads((staging / "staging_manifest.json").read_text())

ROTX90 = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def quat_rotate(q, v):
    w, x, y, z = q
    qv = np.array([x, y, z])
    t = 2 * np.cross(qv, v)
    return np.array(v) + w * t + np.cross(qv, t)


def quat_to_mat(q):
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def probe_rest_quat(vis, col):
    """Drop from the standard spawn and return the settled orientation (wxyz).

    900 steps, same horizon as settle_check: a probe shorter than the gate
    captures MID-FALL poses as "rest". Field case (2026-08-12): the Khronos
    street lantern tips slowly -- at 300 steps the probe caught a partial
    lean, the rescue baked that unstable pose, and the object simply kept
    tipping back to 62.6 deg in the 900-step recheck, making the bake an
    expensive no-op that reproduced the original number to five decimals."""
    sc = sapien.Scene()
    sc.set_timestep(1 / 100)
    sc.add_ground(0)
    b = sc.create_actor_builder()
    b.add_multiple_convex_collisions_from_file(filename=str(col))
    b.add_visual_from_file(filename=str(vis))
    actor = b.build(name="probe")
    actor.set_pose(sapien.Pose(p=[0, 0, 0.005], q=ROTX90))
    for _ in range(900):
        sc.step()
    return [float(v) for v in actor.get_pose().q]


def settle_check(vis, col, height, flat, sample_pts=None):
    """Import-time physics gate, 900 steps -- aligned with the scene runtime.

    It was 300, and a gate weaker than the runtime's doesn't protect
    anything, it just moves the failure somewhere more expensive: both
    beaker_500ml (8.0 deg) and P_Glassware_Short (11.7 deg) imported
    cleanly at 300 steps and then crept past the drift limit in the
    900-step scene replay. Failing HERE instead also puts slow-creep
    assets within reach of the auto-reorient rescue, which can still bake
    their true rest pose at this stage."""
    sc = sapien.Scene()
    sc.set_timestep(1 / 100)
    sc.add_ground(0)
    sc.set_ambient_light([0.5, 0.5, 0.5])
    sc.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])
    b = sc.create_actor_builder()
    b.add_multiple_convex_collisions_from_file(filename=str(col))
    b.add_visual_from_file(filename=str(vis))
    actor = b.build(name="obj")
    actor.set_pose(sapien.Pose(p=[0, 0, 0.005], q=ROTX90))
    poses = []
    for i in range(900):
        sc.step()
        if i % 50 == 0 or i == 899:
            poses.append(actor.get_pose())
    final = poses[-1]
    drift = float(np.linalg.norm(np.array(final.p) - np.array(poses[-2].p)))
    up = quat_rotate(list(final.q), [0.0, 1.0, 0.0])
    tilt = float(np.degrees(np.arccos(np.clip(up[2], -1, 1))))
    min_corner_z = float(final.p[2])
    if sample_pts is not None and len(sample_pts):
        R = quat_to_mat(list(final.q))
        world = np.asarray(sample_pts) @ R.T + np.array(final.p)
        min_corner_z = float(world[:, 2].min())
    cam = sc.add_camera("cam", 320, 240, np.deg2rad(60), 0.01, 10.0)
    eye = np.array([0.3, -0.3, 0.25])
    f = np.array([0, 0, height / 2]) - eye
    f /= np.linalg.norm(f)
    left = np.cross([0, 0, 1], f)
    left /= np.linalg.norm(left)
    upv = np.cross(f, left)
    m = np.column_stack([f, left, upv])
    w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
    q = [
        w,
        (m[2, 1] - m[1, 2]) / (4 * w),
        (m[0, 2] - m[2, 0]) / (4 * w),
        (m[1, 0] - m[0, 1]) / (4 * w),
    ]
    try:
        cam.entity.set_pose(sapien.Pose(p=eye.tolist(), q=q))
    except AttributeError:
        cam.set_local_pose(sapien.Pose(p=eye.tolist(), q=q))
    sc.update_render()
    cam.take_picture()
    img = (np.clip(cam.get_picture("Color"), 0, 1) * 255).astype(np.uint8)[:, :, :3]
    tilt_lim = 45.0 if flat else 15.0
    checks = {
        "late_drift_m": drift,
        "settled": drift < 0.002,
        "final_z_m": float(final.p[2]),
        "min_corner_z_m": min_corner_z,
        "no_penetration": min_corner_z > -0.005,
        "tilt_deg": tilt,
        "tilt_ok": tilt < tilt_lim,
        "tilt_limit": tilt_lim,
    }
    checks["pass"] = (
        checks["settled"] and checks["no_penetration"] and checks["tilt_ok"]
    )
    return checks, img


# Licenses only ever auto-declare from this allowlist: permissive terms whose
# scope needs no per-asset judgment. Anything else (SCEA, EULAs, ...) keeps
# status=unknown with the fetched SPDX + evidence recorded, and waits for a
# human decision -- an automated pipeline asserting "declared" about terms it
# cannot read would be exactly the kind of invented provenance the ledger
# contract forbids.
AUTO_DECLARE_SPDX = {"CC0-1.0", "CC-BY-4.0", "MIT", "BSD-3-Clause", "Apache-2.0"}


def _web_license(r):
    spdx = r.get("license_spdx")
    if not spdx:
        return {
            "spdx": None,
            "status": "unknown",
            "terms_note": r.get("source_license", "unknown (web source)"),
        }
    declared = spdx in AUTO_DECLARE_SPDX
    return {
        "spdx": spdx,
        "status": "declared" if declared else "unknown",
        "terms_note": (
            f"{r.get('license_text') or spdx}; owner: {r.get('license_owner')}"
            + (
                ""
                if declared
                else " -- SPDX auto-fetched from repo metadata; terms scope needs human sign-off"
            )
        ),
        "evidence_url": r.get("license_metadata_url"),
        "checked_date": dt.date.today().isoformat(),
        "checked_by": (
            "auto-declared from repo metadata.json (allowlisted permissive SPDX)"
            if declared
            else "auto-fetched from repo metadata.json; awaiting human sign-off"
        ),
    }


# fill per-item defaults from the first item of the same asset
meta_by_asset = {}
for r in records:
    a = r["asset"]
    if a not in meta_by_asset and "category" in r:
        meta_by_asset[a] = {
            k: r[k]
            for k in (
                "category",
                "aliases",
                "colors",
                "footprint",
                "flat",
                "size_policy",
                "collision",
                "reorient",
            )
            if k in r
        }

bundles_dir = out / "bundles"
bundles_dir.mkdir(exist_ok=True)
rows_dir = out / "rows"
rows_dir.mkdir(exist_ok=True)

if args.only_index is None:
    # driver: pre-wipe asset dirs, then one crash-isolated subprocess per record
    import subprocess
    import sys as _sys

    # M-1 fix-round-2 (Critical): no pre-deletion at all anymore, only
    # directory creation. An upfront bulk delete -- even at model
    # granularity, one record at a time -- still races: if this run's
    # `records` reprocesses MULTIPLE models of the same asset (e.g. a
    # straight re-run of the same manifest), deleting every one of them
    # before ANY worker runs still leaves an as-yet-unprocessed sibling's
    # file missing when an earlier-processed model's validation looks at it.
    # There is no functional need to delete-then-recreate:
    # scene.export()/shutil.copy()/write_text() below all overwrite their
    # target in place, so a model's own file is never *missing* mid-run.
    # This does NOT make a sibling model's on-disk file always
    # ledger-consistent, though (fix-round-3, harness 4): a model can
    # legitimately re-export new bytes and then fail its OWN settle check,
    # in which case its ledger entry is never rewritten -- disk now has new
    # content, the ledger still has the old digest. That combination used
    # to leak into a DIFFERENT model's admission decision because
    # validate_ledger(check_files=True) walked every representation in the
    # whole merged ledger, siblings included. Fixed at the validation call
    # site below (see the `single_led` two-layer check) rather than here --
    # this file-management section only needs to guarantee "never
    # transiently missing", not "always digest-fresh"; the latter is what
    # the split validation now handles per-model. Anything left over from a
    # model that ends up rejected this run is removed by quarantine at the
    # end (below), not up front.
    matrix = []
    for i, r in enumerate(records):
        row_file = rows_dir / f"row_{i}.json"
        if row_file.exists():
            row_file.unlink()
        cmd = [
            _sys.executable,
            __file__,
            "--staging",
            args.staging,
            "--library-dir",
            args.library_dir,
            "--out",
            args.out,
            "--overrides-fragment",
            args.overrides_fragment,
            # The worker re-parses THIS script's own argparse, so every
            # required argument the driver received must be forwarded, or the
            # worker dies inside argparse before writing its row file and the
            # driver can only report "native crash" -- which is exactly how
            # the omission of --identity-basis (added as required 2026-08-10)
            # surfaced: not as an argparse error anywhere visible, but as a
            # phantom crash with no traceback. reference-catalog rides along
            # for the same reason, so a driver override reaches the worker.
            "--identity-basis",
            args.identity_basis,
            *(
                ["--identity-evidence", str(args.identity_evidence)]
                if args.identity_evidence
                else []
            ),
            "--reference-catalog",
            args.reference_catalog,
            "--only-index",
            str(i),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=420)
            tail = [
                ln
                for ln in (proc.stdout + proc.stderr).splitlines()
                if ln.startswith(("ACCEPTED", "REJECTED"))
            ]
            if tail:
                print(tail[-1])
        except subprocess.TimeoutExpired:
            pass
        if row_file.exists():
            matrix.append(json.loads(row_file.read_text()))
        else:
            matrix.append(
                {
                    "asset": r["asset"],
                    "model": r["model"],
                    "usd": r["usd"],
                    "category": r.get("category"),
                    "status": "rejected",
                    "reasons": ["native crash or timeout during processing"],
                }
            )
            print(f"REJECTED {r['asset']} m{r['model']} (native crash/timeout)")
    worker_records = []
else:
    worker_records = [(args.only_index, records[args.only_index])]
    matrix = []

# Both modes need the target directories: the driver creates them for its
# workers, but a worker also runs standalone (--only-index, crash repro) and
# scene.export() does not create parents -- exporting into a missing
# visual/ dir raises FileNotFoundError with a message that reads like the
# GLB itself vanished.
for asset_name in {r["asset"] for r in records}:
    adir = lib / asset_name
    (adir / "visual").mkdir(parents=True, exist_ok=True)
    (adir / "collision").mkdir(parents=True, exist_ok=True)

for idx, r in worker_records:
    asset, model = r["asset"], r["model"]
    meta = {
        **meta_by_asset.get(asset, {}),
        **{
            k: r[k]
            for k in (
                "category",
                "aliases",
                "colors",
                "footprint",
                "flat",
                "size_policy",
                "collision",
                "reorient",
            )
            if k in r
        },
    }
    row = {
        "asset": asset,
        "model": model,
        "usd": r["usd"],
        "category": meta.get("category"),
    }

    def emit_row(row_dict):
        (rows_dir / f"row_{idx}.json").write_text(
            json.dumps(
                row_dict, default=lambda o: o.item() if hasattr(o, "item") else str(o)
            )
        )
        print(
            f"{row_dict['status'].upper()} {row_dict['asset']} m{row_dict['model']} "
            f"({row_dict.get('tilt_deg', -1):.1f}deg, "
            f"{'/'.join(row_dict.get('reasons', [])) or 'ok'})"
        )

    if r["status"] != "converted":
        row.update(status="rejected", reasons=[r.get("error", "conversion failed")])
        emit_row(row)
        continue
    try:
        scene = trimesh.load(r["glb"])
        rotated = False
        if r.get("up_axis") == "Z":
            scene.apply_transform(
                trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
            )
            rotated = True
        lo, hi = scene.bounds
        scene.apply_transform(
            trimesh.transformations.translation_matrix(
                [-(lo[0] + hi[0]) / 2, -float(lo[1]), -(lo[2] + hi[2]) / 2]
            )
        )
        lo, hi = scene.bounds
        size = [float(b - a) for a, b in zip(lo, hi)]
        size_res = conv_lib.resolve_size(
            meta.get("category", "unknown"),
            size,
            args.reference_catalog,
            meta.get("size_policy", "match_category"),
        )
        if size_res["scale"] != 1.0:
            scene.apply_transform(
                trimesh.transformations.scale_matrix(size_res["scale"])
            )
            lo, hi = scene.bounds
            size = [float(b - a) for a, b in zip(lo, hi)]
        conv = conv_lib.inherit_conventions(
            meta.get("category", "unknown"), args.reference_catalog
        )
        if not (0.01 < size[1] < 1.0):
            raise ValueError(f"implausible height {size[1]:.3f}m")

        vis = lib / asset / "visual" / f"base{model}.glb"
        col = lib / asset / "collision" / f"base{model}.glb"

        reorient = r.get("reorient") or meta.get("reorient")
        if reorient == "settle":
            scene.export(str(vis))
            shutil.copy(vis, col)
            qf = probe_rest_quat(vis, col)
            delta = quat_to_mat(ROTX90).T @ quat_to_mat(qf)
            T = np.eye(4)
            T[:3, :3] = delta
            scene.apply_transform(T)
            lo, hi = scene.bounds
            scene.apply_transform(
                trimesh.transformations.translation_matrix(
                    [-(lo[0] + hi[0]) / 2, -float(lo[1]), -(lo[2] + hi[2]) / 2]
                )
            )
            lo, hi = scene.bounds
            size = [float(b - a) for a, b in zip(lo, hi)]
            row["reorient_baked_quat"] = [round(float(v), 5) for v in qf]
            # The bake rotates the mesh, so its bbox -- and therefore
            # max(mesh_bbox_m) -- changes AFTER the sizing decision was taken.
            # actual_max_dim_m is contractually the pre-scale dimension of the
            # FINAL mesh (the v2 size invariant enforces exactly that), so
            # recompute it from the post-bake bounds; pure arithmetic, same
            # repair the v1->v2 migration applied to backfill_upstream's rows.
            if size_res.get("scale"):
                size_res["actual_max_dim_m"] = max(size) / size_res["scale"]

        collision_mode = r.get("collision") or meta.get("collision") or "copy"
        _phys_dir = out / "physcheck"
        _phys_dir.mkdir(exist_ok=True)
        _attempt = {"n": 0}

        def _export_and_check(cur_lo, cur_hi, cur_size):
            """Export the current scene, (re)build its collision, write
            model_data and run the settle gates. Factored so the auto-reorient
            rescue below can re-run the EXACT same procedure on the baked mesh
            -- including rebuilding coacd from the baked geometry, which is
            what keeps probe collision and gate collision consistent."""
            scene.export(str(vis))
            if collision_mode == "coacd":
                import coacd

                merged = (
                    scene.dump(concatenate=True)
                    if isinstance(scene, trimesh.Scene)
                    else scene
                )
                cmesh = coacd.Mesh(
                    np.asarray(merged.vertices), np.asarray(merged.faces)
                )
                parts = coacd.run_coacd(cmesh, threshold=0.05)
                cs = trimesh.Scene()
                for vs, fs in parts:
                    cs.add_geometry(trimesh.Trimesh(np.asarray(vs), np.asarray(fs)))
                cs.export(str(col))
                row["collision_mode"] = f"coacd:{len(parts)}parts"
            else:
                shutil.copy(vis, col)
                row["collision_mode"] = "copy"
            (lib / asset / f"model_data{model}.json").write_text(
                json.dumps(
                    {
                        "center": [float((a + b) / 2) for a, b in zip(cur_lo, cur_hi)],
                        "extents": cur_size,
                        "scale": [1.0, 1.0, 1.0],
                        "transform_matrix": [
                            [1, 0, 0, 0],
                            [0, 1, 0, 0],
                            [0, 0, 1, 0],
                            [0, 0, 0, 1],
                        ],
                        "target_pose": [],
                        "contact_points_pose": [],
                        "functional_matrix": [],
                        "orientation_point": [],
                        "contact_points_group": [],
                        "contact_points_mask": [],
                        "target_point_discription": [],
                        "contact_points_discription": [],
                        "functional_point_discription": [],
                        "orientation_point_discription": [],
                        "stable": True,
                    },
                    indent=2,
                )
            )
            merged_v = (
                scene.dump(concatenate=True)
                if isinstance(scene, trimesh.Scene)
                else scene
            ).vertices
            step = max(1, len(merged_v) // 3000)
            # SAPIEN caches loaded meshes BY FILENAME within a process, so a
            # mesh re-exported to the same path settles as its PREVIOUS
            # content -- measured: the lantern rescue re-tumbled to exactly
            # 62.582593540038985 deg, 15 identical decimals, because the
            # recheck never saw the baked geometry; the same file under a fresh
            # name settled at 0.0 deg. Every physics check therefore runs on
            # a unique-named copy of the current export.
            _attempt["n"] += 1
            vis_p = _phys_dir / f"{asset}_m{model}_a{_attempt['n']}.glb"
            col_p = _phys_dir / f"{asset}_m{model}_a{_attempt['n']}.col.glb"
            shutil.copy(vis, vis_p)
            shutil.copy(col, col_p)
            return settle_check(
                vis_p,
                col_p,
                cur_size[1],
                bool(meta.get("flat")),
                sample_pts=np.asarray(merged_v)[::step],
            )

        checks, img = _export_and_check(lo, hi, size)

        # ---- auto-reorient rescue --------------------------------------
        # An object that fails the physics gates in the STANDARD upright
        # pose is not thereby unusable -- it may simply rest differently
        # (a leaning beaker, a lying lantern). Same idea as pose iteration
        # on the generation side: probe how the object actually comes to
        # rest, bake THAT pose into the mesh, rebuild the collision from
        # the baked geometry, and judge it again. Two honesty rules:
        #   - probe with the REAL collision (col at this point is the final
        #     coacd/copy) -- probing with a convex hull and then gating with
        #     coacd is the inconsistency that sank the beaker;
        #   - both attempts stay in the row, so a rescued asset is visibly
        #     rescued, not silently normal.
        _PHYSICS_GATES = ("settled", "no_penetration", "tilt_ok")
        failed_phys = [k for k in _PHYSICS_GATES if not checks[k]]
        if not checks["pass"] and failed_phys:
            first_attempt = {k: checks[k] for k in _PHYSICS_GATES}
            first_attempt["tilt_deg"] = checks["tilt_deg"]
            qf = probe_rest_quat(vis, col)
            delta = quat_to_mat(ROTX90).T @ quat_to_mat(qf)
            T = np.eye(4)
            T[:3, :3] = delta
            scene.apply_transform(T)
            b_lo, b_hi = scene.bounds
            scene.apply_transform(
                trimesh.transformations.translation_matrix(
                    [
                        -(b_lo[0] + b_hi[0]) / 2,
                        -float(b_lo[1]),
                        -(b_lo[2] + b_hi[2]) / 2,
                    ]
                )
            )
            lo, hi = scene.bounds
            size = [float(b - a) for a, b in zip(lo, hi)]
            if size_res.get("scale"):
                # same post-bake repair as the manual reorient branch: the
                # v2 size invariant pins actual_max_dim_m to the FINAL mesh
                size_res["actual_max_dim_m"] = max(size) / size_res["scale"]
            checks, img = _export_and_check(lo, hi, size)
            row["auto_reorient"] = {
                "first_attempt": first_attempt,
                "baked_quat": [round(float(v), 5) for v in qf],
                "recovered": bool(checks["pass"]),
            }

        from PIL import Image

        Image.fromarray(img).save(out / "shots" / f"{asset}_m{model}.png")
        row.update(
            bbox_m=size,
            rotated_z2y=rotated,
            scale_applied=size_res["scale"],
            size_verdict=size_res["verdict"],
            conventions_inherited_from=conv["precedent"],
            **checks,
        )
        row["status"] = "accepted" if checks["pass"] else "rejected"
        if not checks["pass"]:
            row["reasons"] = [
                k for k in ("settled", "no_penetration", "tilt_ok") if not checks[k]
            ]

        # ---- post-render identity check -------------------------------
        # A web-sourced candidate had no picture before download, so the
        # pre-download gate could only wave it through as "unverifiable".
        # But the settle shot above IS a picture of the thing that would
        # enter the pool -- so this is where "nothing to look at" stops
        # being a free pass. A match upgrades the identity claim to
        # basis=vlm/verified=true (same standard the NVIDIA path meets);
        # a mismatch rejects the model outright; model trouble (unreadable)
        # keeps the honest degraded claim rather than blocking on infra.
        identity_final = dict(IDENTITY)
        if checks["pass"] and args.identity_basis == "requested_by_acquire":
            from lib import a6_verify as a6

            shot = out / "shots" / f"{asset}_m{model}.png"
            verdict = a6.verify_image(
                shot, meta.get("category"), aliases=meta.get("aliases")
            )
            vpath = out / f"identity_post_render_{asset}_m{model}.json"
            vpath.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
            row["identity_post_render"] = verdict.get("verdict")
            if verdict["verdict"] == a6.MATCH:
                identity_final = {
                    "basis": "vlm",
                    "evidence": str(vpath),
                    "verified": True,
                }
            elif verdict["verdict"] == a6.MISMATCH:
                checks["pass"] = False
                row["status"] = "rejected"
                row["reasons"] = [
                    f"identity_mismatch_post_render:{verdict.get('seen_as')}"
                ]

        aliases = meta.get("aliases", [])
        colors = meta.get("colors", [])

        reps = [
            {
                "format": "glb",
                "uri": str(vis),
                "backend": "sapien",
                "role": "visual",
                "sha256": sha256(vis),
                "size_bytes": vis.stat().st_size,
                "metadata": {
                    "derived_from": r["usd"],
                    "converter": "omni.kit.asset_converter@isaac-5.1",
                    "conversion_params": {"rotated_z2y": rotated},
                },
            },
            {
                "format": "glb",
                "uri": str(col),
                "backend": "sapien",
                "role": "collision",
                "sha256": sha256(col),
                "size_bytes": col.stat().st_size,
                "metadata": {
                    "collision_mode": row["collision_mode"],
                    "note": (
                        "offline convex decomposition by coacd (threshold=0.05)"
                        if row["collision_mode"].startswith("coacd")
                        else "copy of visual; convex decomposition at load"
                    ),
                },
            },
            {
                # NVIDIA sources ship an Isaac-loadable USD; a web source is a
                # GLB, which no Isaac backend can consume as-is. Claiming it
                # as isaacsim would make the asset read as cross-backend-ready
                # when it is not -- register it as the portable source instead
                # and let the profile say sapien_only honestly.
                "format": "usd" if not r["group"].startswith("web_") else "glb",
                "uri": r["usd_local"],
                "backend": (
                    "isaacsim" if not r["group"].startswith("web_") else "portable"
                ),
                "role": "visual_and_collision",
                "sha256": r["usd_sha256"],
                "size_bytes": Path(r["usd_local"]).stat().st_size,
                "metadata": {"origin_prefix": r["group"]},
            },
        ]

        # snapshot representation (owner decision #2): reuse the front-view
        # render settle_check() already took (img, above) rather than
        # re-render a second SAPIEN scene -- same camera segment as
        # s3_validate_sapien.py's save_shot, captured before that scene's
        # teardown. Only recorded once settle passes; a failure here must
        # not block ingestion (snapshot representation is optional).
        if checks["pass"]:
            try:
                snap_dir = lib / asset / "snapshots"
                snap_dir.mkdir(parents=True, exist_ok=True)
                snap_path = snap_dir / f"m{model}_default.png"
                Image.fromarray(img).save(snap_path)
                reps.append(
                    {
                        "format": "png",
                        "uri": str(snap_path),
                        "backend": "portable",
                        "role": "snapshot",
                        "sha256": sha256(snap_path),
                        "size_bytes": snap_path.stat().st_size,
                        "metadata": {
                            "yaw_deg": 0,
                            "camera": "front-default",
                            "renderer": "sapien-3.0.0b1",
                        },
                    }
                )
            except Exception as snap_exc:  # noqa: BLE001
                print(
                    f"WARNING: snapshot render failed for {asset} m{model}: "
                    f"{type(snap_exc).__name__}: {snap_exc}",
                    file=sys.stderr,
                )

        # Follow where phase 1 actually mirrored the source. The staging record's
        # usd_local is <source-root>/<group>/<file>, so its parent is that group's
        # mirror dir. Deriving this from `lib` instead silently assumed
        # --source-root == <library-dir>/_source -- an undocumented coupling
        # between the two stages' CLIs. When they diverged, source_manifest_path
        # (required AND not-nullable) came out None and the asset was rejected as
        # schema_violation:missing. Falls back to the old derivation only when a
        # record carries no usd_local at all.
        usd_local = r.get("usd_local")
        manifest_path = (
            Path(usd_local).parent / "SOURCE_MANIFEST.json"
            if usd_local
            else lib / "_source" / r["group"] / "SOURCE_MANIFEST.json"
        )
        is_web = r["group"].startswith("web_")
        if is_web:
            # A web candidate was fetched into a run-scoped cache, which is
            # not a source mirror: the pool's provenance contract (source
            # retained under _source/, hashed manifest, so conversions can be
            # re-derived and licences re-checked) applies to every retrieved
            # asset regardless of where it came from. Mirror it now.
            web_src_dir = lib / "_source" / r["group"]
            web_src_dir.mkdir(parents=True, exist_ok=True)
            mirrored = web_src_dir / Path(r["usd_local"]).name
            if not mirrored.exists():
                shutil.copy(r["usd_local"], mirrored)
            manifest_path = web_src_dir / "SOURCE_MANIFEST.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "files": {mirrored.name: r["usd_sha256"]},
                        "source_url": r.get("source_url"),
                        "source_page": r.get("source_page"),
                        "provider": r.get("source_provider"),
                    },
                    indent=2,
                )
                + "\n"
            )
            r["usd_local"] = str(mirrored)
            source_v1 = {
                "kind": "retrieved",
                "library": f"web ({r.get('source_provider', 'github')})",
                "group": r["group"],
                "file": r["usd"],
                "url": r.get("source_url"),
                "license": _web_license(r),
                "retrieved_at": dt.date.fromtimestamp(
                    (staging / "staging_manifest.json").stat().st_mtime
                ).isoformat(),
                "source_manifest_path": str(manifest_path.resolve()),
            }
        else:
            source_v1 = {
                "kind": "retrieved",
                "library": "NVIDIA Isaac Assets 5.1",
                "group": r["group"],
                "file": r["usd"],
                "license": {
                    "spdx": None,
                    "status": "unknown",
                    "terms_note": "NVIDIA asset EULA; YCB dataset terms for ycb group",
                },
                "retrieved_at": dt.date.fromtimestamp(
                    (staging / "staging_manifest.json").stat().st_mtime
                ).isoformat(),
                "source_manifest_path": (
                    str(manifest_path.resolve()) if manifest_path.exists() else None
                ),
            }

        # thresholds mirror settle_check()'s real gate constants above
        # (0.002 late-drift, -0.005 no-penetration floor -- note this is
        # -5mm, not the -2mm the module docstring states; tilt 15/45deg).
        settle_entry = {
            "backend": "sapien",
            "check": "settle",
            "verdict": "pass" if checks["pass"] else "fail",
            "run_id": out.name,
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
            "verified_digest": "",  # backfilled below once `entry` is assembled
            "report_path": str(out / "import_matrix.json"),
            "thresholds": {
                "settle_disp_m": 0.002,
                "z_min_m": -0.005,
                "tilt_deg": 15,
                "tilt_deg_flat": 45,
            },
        }
        conv_v1 = {
            **{k: conv[k] for k in ("is_static", "z_policy")},
            # C-1: manifest-level footprint override (meta["footprint"]) must
            # win over the inherited-conventions default, same precedence as
            # the old hand-assembled frag_lines block used
            # (`meta.get('footprint') or conv['footprint_shape']`) -- lost
            # when this was first ported to conv_v1, silently dropping the
            # override for every asset that sets one (301_cup/305_bowl/
            # 308_pitcher/313_cup all rely on this).
            "footprint_shape": meta.get("footprint") or conv["footprint_shape"],
            "stable_poses": [
                {
                    "pose_id": "upright",
                    "orientation_wxyz": ledger_mod.X90_WXYZ,
                    "is_default": True,
                }
            ],
            "inherited_from": conv.get("precedent"),
        }
        entry = new_model_entry(
            model=int(model),
            representations=reps,
            mesh_bbox_m=size,
            mesh_up_axis="Y",
            origin_convention="bottom-center",
            size_resolution=size_res,
            conventions=conv_v1,
            source=source_v1,
            verification=[settle_entry],
            # This writer declares profile=cross_backend (it registers the
            # source USD as the isaacsim representation), and cross_backend
            # requires the inertial KEY to exist. engine_derived + nulls is
            # the honest value for a rigid import: no asset-side measurement
            # exists, the engine infers mass distribution from collision
            # geometry. Omitting this made the writer fail its own validator.
            inertial=unknown_inertial("engine_derived"),
        )
        settle_entry["verified_digest"] = reps_digest(entry, "sapien")

        led_path = ledger_path(args.library_dir, asset)
        existing = json.loads(led_path.read_text()) if led_path.exists() else None
        # A re-import replaces the model entry wholesale, which silently
        # DOWNGRADED hand-audited licenses: 301_cup was declared CC-BY-4.0 by
        # the 2026-08-09 audit, got re-imported on 08-10 for a collision fix,
        # and came out status=unknown again. The audit is attached to the
        # SOURCE (library/group/file), not to the import event -- so as long
        # as those coordinates are unchanged and the incoming record knows
        # nothing the existing one doesn't, the audited license survives.
        if existing:
            for em in existing.get("models", []):
                if em.get("model_id") != int(model):
                    continue
                es, ns = em.get("source", {}), source_v1
                if (
                    es.get("license", {}).get("status") == "declared"
                    and ns["license"]["status"] == "unknown"
                    and all(es.get(k) == ns.get(k) for k in ("library", "group", "file"))
                ):
                    ns["license"] = es["license"]
        led = upsert_model(
            existing,
            asset=asset,
            category=meta.get("category", "unknown"),
            kind="rigid",
            # cross_backend is a statement of fact, not aspiration: NVIDIA
            # records register their source USD as an isaacsim representation;
            # a web record's GLB is registered as portable, so it declares
            # sapien_only and owes nothing it cannot show.
            profile="sapien_only" if is_web else "cross_backend",
            identity=identity_final,
            aliases=aliases,
            colors=colors,
            materials=[],
            tags=["rigid", "external", "batch"],
            model_entry=entry,
        )
        # single_led: a throwaway ledger containing ONLY this model's own
        # entry. Built once, reused both as the file-integrity check input
        # right below and (unconditionally when there are violations) as
        # the run-snapshot unpack source further down -- not rebuilt twice.
        single_led = upsert_model(
            None,
            asset=asset,
            category=meta.get("category", "unknown"),
            kind="rigid",
            # cross_backend is a statement of fact, not aspiration: NVIDIA
            # records register their source USD as an isaacsim representation;
            # a web record's GLB is registered as portable, so it declares
            # sapien_only and owes nothing it cannot show.
            profile="sapien_only" if is_web else "cross_backend",
            identity=identity_final,
            aliases=aliases,
            colors=colors,
            materials=[],
            tags=["rigid", "external", "batch"],
            model_entry=entry,
        )
        # Two-layer gate (fix-round-3, Critical -- harness 4): structural
        # checks run over the whole merged ledger (`led`, check_files=False)
        # -- schema/required-field/duplicate-id shape is legitimately a
        # whole-asset concern. File integrity (existence + sha256) runs
        # ONLY over `single_led` (check_files=True), i.e. only this model's
        # own representations. A sibling model's disk state -- missing
        # (fix-round-2's bug) or digest-stale because it re-exported new
        # bytes and then failed its own settle check (fix-round-3's bug) --
        # can no longer leak into THIS model's admission decision. Whole-
        # library file-integrity sweeps are T8's job, not this per-model
        # gate's.
        violations = validate_ledger(led, check_files=False) + validate_ledger(
            single_led, check_files=True
        )
        if violations or not checks["pass"]:
            row["status"] = "rejected"
            row.setdefault("reasons", []).extend(
                f"schema_violation:{v.code}:{v.path}" for v in violations
            )
        else:
            # I-1: whole-ledger write through the fcntl-locked atomic writer
            # (lib/ledger.py) -- a bare write_text() here would race a
            # driver-level SIGKILL (crash-isolation subprocesses are killed
            # on timeout) into a torn ledger.json that breaks every later
            # reader of this asset.
            ledger_mod.write_ledger(led_path, led)
        # run snapshot: always written (pool-layer record), even for a
        # rejected model -- when there are violations, unpack from
        # single_led instead of `led` so this doesn't depend on the rest of
        # the asset's (possibly also-invalid) models.
        bundle_ledger = led if not violations else single_led
        # I-2: pick the bundle by asset_id suffix, not to_ir_bundles(...)[-1]
        # -- [-1] silently depended on "this model_id was just appended to
        # the end of models[]", which M-1's fix (no longer wiping ledger.json
        # on every driver pre-wipe) breaks for a re-run that revisits an
        # existing, non-last model_id.
        bundle = next(
            b
            for b in to_ir_bundles(bundle_ledger)
            if b["asset_id"].endswith(f"_m{model}")
        )
        (bundles_dir / f"{asset}_m{model}.json").write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001
        row.update(status="rejected", reasons=[f"{type(exc).__name__}: {exc}"])
    emit_row(row)

if args.only_index is not None:
    raise SystemExit(0)

# quarantine rejected models (fix-round-2, M-1 Critical): their files must
# NOT stay in the library, or the catalog scanner would pick them up as
# usable without overrides -- AND, if this exact model_id already had an
# entry in the authoritative ledger from an earlier run (verdict=pass,
# pointing at files this loop is about to delete), that entry must be
# pruned too, or gen_fragment would keep projecting a dangling reference
# (files gone, ledger still says pass). models[] is a point-in-time image
# of the pool's current state, not an audit log -- pruning on eviction is
# consistent with "quarantine physically isolates out of the asset pool"
# (OVERVIEW.md); the audit trail lives in import_matrix.json + the run's
# bundle snapshot, not the ledger. Per-model granularity throughout (no
# whole-directory rmtree here) -- same reasoning as the driver pre-wipe fix
# above: a directory-wide operation would also touch sibling models this
# run never rejected.
for row in matrix:
    if row["status"] != "accepted":
        a, m = row["asset"], row["model"]
        for p in (
            lib / a / "visual" / f"base{m}.glb",
            lib / a / "collision" / f"base{m}.glb",
            lib / a / f"model_data{m}.json",
        ):
            if p.exists():
                p.unlink()
        # H3 顺手: orphaned snapshot from a model that settled fine (snapshot
        # rendered) but got rejected on a schema violation before this loop
        # ever ran -- same pool-hygiene reasoning as the 3 files above.
        for p in (lib / a / "snapshots").glob(f"m{m}_*.png"):
            p.unlink()
        lp = ledger_path(args.library_dir, a)
        if lp.exists():
            led_on_disk = json.loads(lp.read_text())
            models = led_on_disk.get("models", [])
            pruned = [mm for mm in models if mm.get("model_id") != int(m)]
            if len(pruned) != len(models):
                if pruned:
                    led_on_disk["models"] = pruned
                    ledger_mod.write_ledger(lp, led_on_disk)
                else:
                    lp.unlink()
                    lock_p = lp.with_suffix(".lock")
                    if lock_p.exists():
                        lock_p.unlink()

# fix-round-3 (configured item 1): an asset that never made it into the
# ledger at all this run -- brand new and every model rejected, or pruned
# down to nothing above -- would otherwise leave an empty shell behind
# (empty visual/, collision/, and any orphaned snapshots/ from a model that
# settled fine but got rejected on a schema violation before quarantine
# ever touched its snapshot file). Left alone, s9_build_shadow_root.py
# symlinks that empty shell straight into the shadow tree. Whole-directory
# removal is safe HERE specifically because "no ledger.json and no
# model_data*.json left" means there is no surviving model for this asset
# to disturb -- not a bulk pre-emptive wipe like the bugs fixed above.
for a in {row["asset"] for row in matrix}:
    adir = lib / a
    if (
        adir.exists()
        and not ledger_path(args.library_dir, a).exists()
        and not any(adir.glob("model_data*.json"))
    ):
        shutil.rmtree(adir)

# overrides fragment (I-4): --overrides-fragment stays scoped to this run's
# assets (acquire_batch's per-candidate concatenation and s9's "wanted"
# assertion depend on that -- a full-library fragment is gen_fragment's own
# CLI's job, not this script's). gen_fragment.generate still does the actual
# projection (authoritative-ledger source of truth, latest-settle-pass
# filter via lib.ledger.latest_verification -- see gen_fragment.py
# docstring); the result is filtered down to run_assets afterward rather
# than re-deriving the projection logic here.
run_assets = {row["asset"] for row in matrix}
frag, _lib_stats = gen_fragment.generate(args.library_dir)
frag = {k: v for k, v in frag.items() if k in run_assets}
Path(args.overrides_fragment).parent.mkdir(parents=True, exist_ok=True)
gen_fragment.write_yaml(frag, Path(args.overrides_fragment))

# unknown-license count scoped to run_assets (gen_fragment.generate's own
# stat is a whole-library aggregate; re-derive the same settle-pass +
# license-status predicate restricted to this run's assets rather than
# changing gen_fragment.py's contract for one caller).
unknown_in_run = 0
for a in run_assets:
    lp = ledger_path(args.library_dir, a)
    if not lp.exists():
        continue
    for m in json.loads(lp.read_text()).get("models", []):
        latest = ledger_mod.latest_verification(m, "sapien", "settle")
        if latest is None or latest.get("verdict") != "pass":
            continue
        if m.get("source", {}).get("license", {}).get("status") != "declared":
            unknown_in_run += 1
print(
    f"WARNING: {unknown_in_run} models with unknown license in view",
    file=sys.stderr,
)

(out / "import_matrix.json").write_text(
    json.dumps(
        matrix, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
acc = sum(1 for r in matrix if r["status"] == "accepted")
# M-4: fragment_assets restored to this-run scope (len(frag) is now
# run_assets-filtered, see I-4 above) rather than the whole-library count
# gen_fragment.generate would otherwise report.
print(f"PHASE2 accepted={acc}/{len(matrix)} fragment_assets={len(frag)}")
