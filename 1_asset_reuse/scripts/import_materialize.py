#!/usr/bin/env python3
"""Batch external import, phase 2 (env-gen-yuxin env).

Reads staging_manifest.json, normalizes every converted GLB to RoboTwin
conventions (upAxis-driven Z->Y rotation, origin at bottom center), materializes
multi-model asset dirs under data/asset_library/, runs a SAPIEN settle check per
model, writes per-model AssetBundles, an import matrix, and the overrides
fragment consumed by the catalog builder.

Gates per model: settled (<2mm late drift), no ground penetration (>-2mm),
tilt < 15 deg (45 deg for 'flat' items). Failures are recorded with reasons and
excluded from the overrides fragment (= excluded from the catalog).
"""

import argparse
import hashlib
import sys
import json
import math
import shutil
from pathlib import Path

import numpy as np
import sapien
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import conventions as conv_lib  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--staging", required=True)
parser.add_argument("--library-dir", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--overrides-fragment", required=True)
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


def settle_check(vis, col, height, flat):
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
    for i in range(300):
        sc.step()
        if i % 50 == 0 or i == 299:
            poses.append(actor.get_pose())
    final = poses[-1]
    drift = float(np.linalg.norm(np.array(final.p) - np.array(poses[-2].p)))
    up = quat_rotate(list(final.q), [0.0, 1.0, 0.0])
    tilt = float(np.degrees(np.arccos(np.clip(up[2], -1, 1))))
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
        "no_penetration": final.p[2] > -0.002,
        "tilt_deg": tilt,
        "tilt_ok": tilt < tilt_lim,
        "tilt_limit": tilt_lim,
    }
    checks["pass"] = (
        checks["settled"] and checks["no_penetration"] and checks["tilt_ok"]
    )
    return checks, img


# fill per-item defaults from the first item of the same asset
meta_by_asset = {}
for r in records:
    a = r["asset"]
    if a not in meta_by_asset and "category" in r:
        meta_by_asset[a] = {
            k: r[k]
            for k in ("category", "aliases", "colors", "footprint", "flat")
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

    for asset_name in {r["asset"] for r in records}:
        if (lib / asset_name).exists():
            shutil.rmtree(lib / asset_name)
        (lib / asset_name / "visual").mkdir(parents=True)
        (lib / asset_name / "collision").mkdir(parents=True)
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
            "--only-index",
            str(i),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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

for idx, r in worker_records:
    asset, model = r["asset"], r["model"]
    meta = {
        **meta_by_asset.get(asset, {}),
        **{
            k: r[k]
            for k in ("category", "aliases", "colors", "footprint", "flat")
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
            meta.get("category", "unknown"), size, args.reference_catalog,
            meta.get("size_policy", "match_category"))
        if size_res["scale"] != 1.0:
            scene.apply_transform(
                trimesh.transformations.scale_matrix(size_res["scale"]))
            lo, hi = scene.bounds
            size = [float(b - a) for a, b in zip(lo, hi)]
        conv = conv_lib.inherit_conventions(
            meta.get("category", "unknown"), args.reference_catalog)
        if not (0.01 < size[1] < 1.0):
            raise ValueError(f"implausible height {size[1]:.3f}m")

        vis = lib / asset / "visual" / f"base{model}.glb"
        col = lib / asset / "collision" / f"base{model}.glb"
        scene.export(str(vis))
        shutil.copy(vis, col)
        (lib / asset / f"model_data{model}.json").write_text(
            json.dumps(
                {
                    "center": [float((a + b) / 2) for a, b in zip(lo, hi)],
                    "extents": size,
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

        checks, img = settle_check(vis, col, size[1], bool(meta.get("flat")))
        from PIL import Image

        Image.fromarray(img).save(out / "shots" / f"{asset}_m{model}.png")
        row.update(bbox_m=size, rotated_z2y=rotated,
                   scale_applied=size_res["scale"], size_verdict=size_res["verdict"],
                   conventions_inherited_from=conv["precedent"], **checks)
        row["status"] = "accepted" if checks["pass"] else "rejected"
        if not checks["pass"]:
            row["reasons"] = [
                k for k in ("settled", "no_penetration", "tilt_ok") if not checks[k]
            ]

        bundle = {
            "asset_id": f"external_{asset}_m{model}",
            "category": meta.get("category", "unknown"),
            "representations": [
                {
                    "format": "glb",
                    "uri": str(vis),
                    "backend": "sapien",
                    "role": "visual",
                    "sha256": sha256(vis),
                    "size_bytes": vis.stat().st_size,
                    "metadata": {
                        "derived_from": r["usd"],
                        "rotated_z2y": rotated,
                        "origin": "bottom-center normalized",
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
                        "note": "copy of visual; convex decomposition at load"
                    },
                },
                {
                    "format": "usd",
                    "uri": r["usd_local"],
                    "backend": "isaacsim",
                    "role": "visual_and_collision",
                    "sha256": r["usd_sha256"],
                    "size_bytes": 0,
                    "metadata": {"origin_prefix": r["group"]},
                },
            ],
            "source": {
                "library": "NVIDIA Isaac Assets 5.1",
                "group": r["group"],
                "file": r["usd"],
                "license": "unknown (NVIDIA asset EULA; YCB dataset terms for ycb group)",
            },
            "physical": {
                "mass_kg": {
                    "value": None,
                    "status": "unknown",
                    "runtime_default_kg": 0.1,
                },
                "mesh_bbox_m": size,
                "scale_applied": size_res["scale"],
                "size_resolution": size_res,
                "conventions": conv,
                "scale": [1.0, 1.0, 1.0],
                "mesh_up_axis": "Y",
            },
            "articulation": {},
            "tags": ["rigid", "external", "batch"],
        }
        (bundles_dir / f"{asset}_m{model}.json").write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001
        row.update(status="rejected", reasons=[f"{type(exc).__name__}: {exc}"])
    emit_row(row)

if args.only_index is not None:
    raise SystemExit(0)

# quarantine rejected models: their files must NOT stay in the library, or the
# catalog scanner would pick them up as usable without overrides
for row in matrix:
    if row["status"] != "accepted":
        a, m = row["asset"], row["model"]
        for p in (lib / a / "visual" / f"base{m}.glb",
                  lib / a / "collision" / f"base{m}.glb",
                  lib / a / f"model_data{m}.json"):
            if p.exists():
                p.unlink()
for a in {row["asset"] for row in matrix}:
    if not any(x["status"] == "accepted" for x in matrix if x["asset"] == a):
        if (lib / a).exists():
            shutil.rmtree(lib / a)

# overrides fragment: only assets with >=1 accepted model
frag_lines = []
by_asset = {}
for row in matrix:
    by_asset.setdefault(row["asset"], []).append(row)
for asset, rows in sorted(by_asset.items()):
    accepted = [x for x in rows if x["status"] == "accepted"]
    if not accepted:
        continue
    meta = meta_by_asset.get(asset, {})
    conv = conv_lib.inherit_conventions(
        meta.get("category", "unknown"), args.reference_catalog)
    orient = conv_lib.orientation_for_kind("rigid_yup")
    frag_lines.append(f"  {asset}:")
    frag_lines.append(f"    category: {meta.get('category', 'unknown')}")
    frag_lines.append(f"    aliases: [{', '.join(meta.get('aliases', []))}]")
    if meta.get("colors"):
        frag_lines.append(f"    colors: [{', '.join(meta['colors'])}]")
    frag_lines.append("    models:")
    for x in accepted:
        frag_lines.append(f'      "{x["model"]}":')
        frag_lines.append("        stable_pose_id: upright")
        frag_lines.append(
            f"        stable_orientation_wxyz: [{orient[0]}, {orient[1]}, {orient[2]}, {orient[3]}]")
        frag_lines.append(f"        z_policy: {conv['z_policy']}")
        frag_lines.append(
            f"        footprint_shape: {meta.get('footprint') or conv['footprint_shape']}")
        if meta.get("is_static", conv["is_static"]):
            frag_lines.append("        is_static: true")
Path(args.overrides_fragment).parent.mkdir(parents=True, exist_ok=True)
Path(args.overrides_fragment).write_text("\n".join(frag_lines) + "\n")

(out / "import_matrix.json").write_text(
    json.dumps(
        matrix, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
acc = sum(1 for r in matrix if r["status"] == "accepted")
print(
    f"PHASE2 accepted={acc}/{len(matrix)} fragment_assets={len([a for a, rs in by_asset.items() if any(x['status'] == 'accepted' for x in rs)])}"
)
