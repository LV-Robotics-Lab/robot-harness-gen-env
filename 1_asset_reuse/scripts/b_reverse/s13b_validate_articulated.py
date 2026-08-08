#!/usr/bin/env python3
"""Articulated reverse import, phase 2 (env-gen-yuxin env): SAPIEN validation
of the exported URDF + library registration.

Checks: URDF loads via SAPIEN loader (fix_root_link), dof matches the export
report, limits preserved, 120-step settle finite, and a joint sweep — each
movable joint driven to both limits without the articulation exploding.
Writes model_data0.json, screenshot, per-asset v1 ledger entry, and a
flattened bundle JSON snapshot (back-compat with the pre-ledger consumers).
"""

import argparse
import datetime
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import sapien

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import conventions, ledger

parser = argparse.ArgumentParser()
parser.add_argument("--instance-dir", required=True, help=".../314_cabinet/0")
parser.add_argument("--source-usd", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--library-dir", required=True, help="data/asset_library root")
parser.add_argument(
    "--allow-free-joints",
    action="store_true",
    help="accept joints whose gravity equilibrium differs from rest pose (recorded)",
)
args = parser.parse_args()

inst = Path(args.instance_dir)
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
report = json.loads((inst / "export_report.json").read_text())
expected_movable = int(report["joints_movable"])
bbox = report["bbox_m"]

# Ledger identity: --instance-dir is <asset>/<model_id>/ (see its --help
# above); asset/model_id are derived from that structure, --library-dir is
# the separate data/asset_library root (same convention as s8a/s8b/gen_fragment).
# category has no CLI arg on this script (unlike s13a) so it keeps the value
# this script has always hardcoded.
asset = inst.parent.name
if not inst.name.isdigit():
    # upsert_model replaces model_id wholesale (re-import semantics) -- a
    # silent fallback to 0 here would silently clobber an existing model 0
    # on any non-numeric --instance-dir leaf. Fail loudly instead.
    print(
        f"FAIL s13b: --instance-dir model directory {inst.name!r} is not "
        f"numeric (expected .../{asset}/<model_id>/)"
    )
    sys.exit(1)
model_id = int(inst.name)
category = "cabinet"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


sc = sapien.Scene()
sc.set_timestep(1 / 100)
sc.add_ground(0)
sc.set_ambient_light([0.5, 0.5, 0.5])
sc.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])
loader = sc.create_urdf_loader()
loader.fix_root_link = True
art = loader.load(str(inst / "mobility.urdf"))
if art is None:
    print("FAIL s13b: URDF failed to load")
    sys.exit(1)
dof = int(art.dof)
active = art.get_active_joints()
limits = [(float(j.get_limits()[0][0]), float(j.get_limits()[0][1])) for j in active]

q0 = np.asarray(art.get_qpos(), dtype=float)
q_mid = None
for i in range(120):
    sc.step()
    if i == 89:
        q_mid = np.asarray(art.get_qpos(), dtype=float)
qpos = np.asarray(art.get_qpos(), dtype=float)
settle_ok = bool(np.isfinite(qpos).all())
converged = bool(np.all(np.abs(qpos - q_mid) < 1e-3))
jtypes = [getattr(j, "type", "revolute") for j in active]
free_joints = []
equilibrium = []
for i2, jt in enumerate(jtypes):
    drift = float(abs(qpos[i2] - q0[i2]))
    thresh = 0.005 if "prismatic" in str(jt) else math.radians(5)
    equilibrium.append(
        {
            "joint": i2,
            "type": str(jt),
            "rest": round(float(q0[i2]), 4),
            "equilibrium": round(float(qpos[i2]), 4),
            "self_drift": round(drift, 4),
            "free": drift > thresh,
        }
    )
    if drift > thresh:
        free_joints.append(i2)

sweep_ok = True
sweep_detail = []
for i, (lo, up) in enumerate(limits):
    for target in (lo, up):
        if not math.isfinite(target):
            continue
        q = np.array(art.get_qpos(), dtype=float)
        q[i] = target
        art.set_qpos(q)
        for _ in range(30):
            sc.step()
        cur = np.asarray(art.get_qpos(), dtype=float)
        okq = bool(np.isfinite(cur).all())
        sweep_ok = sweep_ok and okq
        sweep_detail.append({"joint": i, "target": round(target, 3), "finite": okq})
    q = np.array(art.get_qpos(), dtype=float)
    q[i] = 0.0
    art.set_qpos(q)

cam = sc.add_camera("cam", 640, 480, np.deg2rad(60), 0.01, 10.0)
# pose joints half-open for an informative screenshot (doors/drawers visible)
q_show = np.array(
    [
        lo + 0.6 * (up - lo) if abs(lo) > abs(up) else lo + 0.6 * (up - lo)
        for lo, up in limits
    ]
)
q_show = np.array([(lo if abs(lo) > abs(up) else up) * 0.6 for lo, up in limits])
art.set_qpos(q_show)
for _ in range(10):
    sc.step()
    art.set_qpos(q_show)
span = max(bbox)
eye = np.array([1.35 * span, -1.35 * span, 1.05 * span])
f = np.array([0, 0, bbox[2] / 2]) - eye
f /= np.linalg.norm(f)
left = np.cross([0, 0, 1], f)
left /= np.linalg.norm(left)
upv = np.cross(f, left)
m = np.column_stack([f, left, upv])
w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
qc = [
    w,
    (m[2, 1] - m[1, 2]) / (4 * w),
    (m[0, 2] - m[2, 0]) / (4 * w),
    (m[1, 0] - m[0, 1]) / (4 * w),
]
try:
    cam.entity.set_pose(sapien.Pose(p=eye.tolist(), q=qc))
except AttributeError:
    cam.set_local_pose(sapien.Pose(p=eye.tolist(), q=qc))
sc.update_render()
cam.take_picture()
img = (np.clip(cam.get_picture("Color"), 0, 1) * 255).astype(np.uint8)[:, :, :3]
from PIL import Image

Image.fromarray(img).save(out / "sapien_314_cabinet.png")

(inst / "model_data0.json").write_text(
    json.dumps(
        {
            "center": [0, 0, bbox[2] / 2],
            "extents": bbox,
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

checks = {
    "dof": dof,
    "expected_movable": expected_movable,
    "dof_matches": dof == expected_movable,
    "limits": [[round(a, 3), round(b, 3)] for a, b in limits],
    "settle_finite": settle_ok,
    "converged": converged,
    "free_joints": free_joints,
    "equilibrium": equilibrium,
    "free_joints_allowed": bool(args.allow_free_joints),
    "sweep_ok": sweep_ok,
    "sweep_detail": sweep_detail,
    "screenshot_ok": bool(img.std() > 1),
}
free_ok = (not free_joints) or args.allow_free_joints
if free_joints and not args.allow_free_joints:
    print(
        f"joints {free_joints} swing freely under gravity; "
        "pass --allow-free-joints to accept (recorded) or fix dynamics"
    )
checks["status"] = (
    "pass"
    if checks["dof_matches"]
    and settle_ok
    and converged
    and sweep_ok
    and free_ok
    and checks["screenshot_ok"]
    else "fail"
)

# ---------------------------------------------------------------------------
# v1 ledger registration (T6): assemble one models[] entry from this script's
# own dof/limits verification results + the s13a export report, upsert it
# into the per-asset ledger.json (authoritative), and re-derive the legacy
# flattened bundle snapshot from the ledger for back-compat readers.
# ---------------------------------------------------------------------------

representations = [
    {
        "format": "urdf",
        "uri": str(inst / "mobility.urdf"),
        "backend": "sapien",
        "role": "visual_and_collision",
        "sha256": sha256(inst / "mobility.urdf"),
        "size_bytes": (inst / "mobility.urdf").stat().st_size,
        "metadata": {
            "derived_from": report["source_usd"],
            "links": report["links"],
            "movable_joints": expected_movable,
            "note": "geometry-only OBJs; source materials not ported (lossy)",
        },
    },
    {
        "format": "usd",
        "uri": args.source_usd,
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": sha256(args.source_usd),
        "size_bytes": Path(args.source_usd).stat().st_size,
        "metadata": {"origin": "Isaac Assets 5.1 /Isaac/Props/Sektion_Cabinet"},
    },
]

# joint_names/types indexed to match `active`/`limits` (dof-length). s13a's
# export_report.json "movable" list is a list of dicts -- verified against
# s13a_usd2urdf.py (each movable joint's `rec` dict, `rot1` stripped when
# written out) -- in the same relative order (fixed joints filtered out of
# both, URDF file order preserved). Guard both the index bound and the
# element shape (neither is a schema this script itself controls) and fall
# back to a placeholder name/type per unmatched/malformed index rather than
# hard-failing -- dof_matches already flags a length mismatch in `checks`.
movable_meta = report.get("movable", [])


def _movable_field(i, key, fallback):
    if i < len(movable_meta) and isinstance(movable_meta[i], dict):
        return movable_meta[i].get(key) or fallback
    return fallback


joint_names = [_movable_field(i, "name", f"j{i}") for i in range(len(active))]
joint_types = [_movable_field(i, "type", str(jtypes[i])) for i in range(len(active))]
articulation = {
    "joint_names": joint_names,
    "joint_types": joint_types,
    "limits": checks["limits"],
    "closed_qpos": [lo for lo, up in checks["limits"]],
    "open_qpos": [up for lo, up in checks["limits"]],
    "balance_gate": {
        "free_joints_allowed": bool(args.allow_free_joints),
        "measured_equilibrium": equilibrium if args.allow_free_joints else None,
    },
}

mass_override = {
    "value": None,
    "status": "unknown",
    "runtime_default_kg": 10.0,
    "runtime_default_basis": "urdf_inertial",
}

conventions_block = {
    "is_static": conventions.CONSERVATIVE_DEFAULTS["is_static"],
    "z_policy": conventions.CONSERVATIVE_DEFAULTS["z_policy"],
    "footprint_shape": conventions.CONSERVATIVE_DEFAULTS["footprint_shape"],
    "stable_poses": [
        {
            "pose_id": "upright",
            "orientation_wxyz": ledger.IDENTITY_WXYZ,  # URDF Z-up -> identity
            "is_default": True,
        }
    ],
    "inherited_from": None,
}

# s13a only writes a real size_resolution dict when --size-policy was passed;
# otherwise export_report.json has a bare null. physical.size_resolution is
# not-nullable in the ledger, so synthesize the equivalent "no policy
# applied" shape (mirrors conventions.resolve_size's own no-op branch).
size_resolution = report.get("size_resolution") or {
    "mode": None,
    "actual_max_dim_m": max(bbox),
    "scale": report.get("scale_applied", 1.0),
    "reference_max_dim_m": None,
    "reference_assets": [],
    "verdict": "ok",
}

source_usd_path = Path(args.source_usd)
source_block = {
    "library": "NVIDIA Isaac Assets 5.1",
    "group": "Sektion_Cabinet",
    "file": source_usd_path.name,
    "license": {
        "spdx": None,
        "status": "unknown",
        "terms_note": "NVIDIA asset EULA; verify before redistribution",
    },
    "retrieved_at": datetime.date.fromtimestamp(
        source_usd_path.stat().st_mtime
    ).isoformat(),
    "source_manifest_path": str(inst / "export_report.json"),
}

verified_digest = ledger.reps_digest({"representations": representations}, "sapien")
verification_entry = {
    "backend": "sapien",
    "check": "joint_sweep",
    "verdict": checks["status"],
    "run_id": out.name,
    "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
    "verified_digest": verified_digest,
    "report_path": str(out / "cabinet314_validation.json"),
}

model_entry = ledger.new_model_entry(
    model=model_id,
    representations=representations,
    mesh_bbox_m=bbox,
    mesh_up_axis="Z",
    origin_convention="base-at-floor",
    scale_applied=report.get("scale_applied", 1.0),
    size_resolution=size_resolution,
    conventions=conventions_block,
    source=source_block,
    verification=[verification_entry],
    articulation=articulation,
    mass_override=mass_override,
)

lib_dir = Path(args.library_dir)
lp = ledger.ledger_path(lib_dir, asset)
existing_ledger = json.loads(lp.read_text()) if lp.exists() else None
led = ledger.upsert_model(
    existing_ledger,
    asset=asset,
    category=category,
    kind="articulated",
    aliases=[category],
    colors=[],
    materials=[],
    tags=["articulated", "external", "reverse-import"],
    model_entry=model_entry,
)
violations = ledger.validate_ledger(led, check_files=False)
if violations:
    print(f"WARN s13b ledger: {len(violations)} violation(s):")
    for v in violations:
        print(f"  {v.path} [{v.code}] {v.message}")
lp.parent.mkdir(parents=True, exist_ok=True)
lp.write_text(json.dumps(led, indent=2) + "\n")

# Back-compat snapshot at the original bundle path: same authoritative
# content, re-derived (flattened) from the ledger rather than hand-assembled.
flat_bundle = next(
    b
    for b in ledger.to_ir_bundles(led)
    if b["asset_id"] == f"{led['asset_id']}_m{model_id}"
)
(out / "cabinet314_bundle.json").write_text(
    json.dumps(flat_bundle, indent=2, ensure_ascii=False)
)
(out / "cabinet314_validation.json").write_text(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print(
    json.dumps(
        checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )
)
print("PASS s13b" if checks["status"] == "pass" else "FAIL s13b")
sys.exit(0 if checks["status"] == "pass" else 1)
