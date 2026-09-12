#!/usr/bin/env python3
"""Articulated reverse import, phase 2 (env-gen-yuxin env): SAPIEN validation
of the exported URDF + library registration.

Checks: URDF loads via SAPIEN loader (fix_root_link), dof matches the export
report, limits preserved, 120-step settle finite, and a joint sweep — each
movable joint driven to both limits without the articulation exploding.
Writes model_data<model_id>.json, screenshot, per-asset v3 ledger entry, and a
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
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import conventions, ledger, ledger_writes

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

# Ledger identity: --instance-dir is <asset>/<model_id>/ (see its --help
# above); asset/model_id are derived from that structure, --library-dir is
# the separate data/asset_library root (same convention as s8a/s8b/gen_fragment).
# category has no CLI arg on this script (unlike s13a) so it keeps the value
# this script has always hardcoded.
asset_label = inst.parent.name
try:
    asset = ledger.canonical_asset_key(asset_label)
    model_id = int(inst.name)
    if inst.name != str(model_id):
        raise ledger.InvalidModelIdError("model directory must use the canonical base-10 spelling")
    model_id = ledger.canonical_model_id(model_id)
except (ValueError, ledger.UnsafeAssetKeyError, ledger.InvalidModelIdError):
    # upsert_model replaces model_id wholesale (re-import semantics) -- a
    # silent fallback to 0 here would silently clobber an existing model 0
    # on any non-numeric --instance-dir leaf. Fail loudly instead.
    print(
        f"FAIL s13b: --instance-dir model directory {inst.name!r} is not "
        f"numeric (expected .../{asset_label}/<model_id>/)"
    )
    sys.exit(1)
ledger.claim_asset_model(set(), asset, model_id)
category = "cabinet"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


report = json.loads((inst / "export_report.json").read_text())
expected_movable = int(report["joints_movable"])
bbox = report["bbox_m"]

source_usd_path = Path(args.source_usd)
reported_source_path = Path(report.get("source_usd", ""))
reported_source_candidates = [reported_source_path]
if not reported_source_path.is_absolute() and len(reported_source_path.parts) == 1:
    # s13a may have been invoked with a bare filename while s13b receives the
    # same file as an absolute path.  Preserve that legitimate spelling
    # difference, but never accept a byte-identical copy at another inode.
    reported_source_candidates.append(source_usd_path.parent / reported_source_path)
source_identity_matches = False
for reported_source_candidate in reported_source_candidates:
    try:
        if reported_source_candidate.samefile(source_usd_path):
            source_identity_matches = True
            break
    except OSError:
        continue
reported_source_sha256 = report.get("source_usd_sha256")
if (
    not isinstance(report.get("source_usd"), str)
    or not report["source_usd"].strip()
    or not isinstance(reported_source_sha256, str)
    or len(reported_source_sha256) != 64
    or any(char not in "0123456789abcdef" for char in reported_source_sha256)
    or not source_usd_path.is_file()
    or not source_identity_matches
    or sha256(source_usd_path) != reported_source_sha256
):
    print("FAIL s13b: source USD does not match export report")
    sys.exit(1)

out.mkdir(parents=True, exist_ok=True)

try:
    import sapien
except ModuleNotFoundError:
    print("FAIL s13b: SAPIEN is required for articulated runtime validation")
    sys.exit(1)

lib_dir = Path(args.library_dir)
try:
    asset_path = ledger.ensure_asset_directory(lib_dir, asset)
    lp = asset_path / "ledger.json"
    if lp.is_file():
        loaded = ledger.load_asset_ledger(lib_dir, asset)
        existing_ledger = loaded.document
        lp = loaded.ledger_path
        asset_path = loaded.asset_dir
    else:
        existing_ledger = None
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f"FAIL s13b: unsafe or invalid asset location ({exc})")
    sys.exit(1)

sapien_representation = {
    "format": "urdf",
    "uri": str(inst / "mobility.urdf"),
    "backend": "sapien",
    "role": "visual_and_collision",
    "sha256": sha256(inst / "mobility.urdf"),
    "files": ledger_writes.representation_files(inst / "mobility.urdf"),
    "collision_meta": {
        "mode": "unknown",
        "unknown_reason": "URDF collision authoring was not geometrically probed",
    },
    "metadata": {
        "derived_from": report["source_usd"],
        "links": report["links"],
        "movable_joints": expected_movable,
        "note": "geometry-only OBJs; source materials not ported (lossy)",
    },
}
preflight_model = {"model_id": model_id, "representations": [sapien_representation]}
execution_snapshot = ledger_writes.publish_execution_snapshot(
    asset_path / "verification_inputs",
    source_asset_dir=inst,
    asset_key=asset,
    model_entry=preflight_model,
    extra_files={
        "export_report": inst / "export_report.json",
        "source_usd": args.source_usd,
    },
)
execution_root = Path(ledger.resolve_uri(execution_snapshot["root_uri"]))
execution_urdf = execution_root / "assets" / "objects" / asset / "mobility.urdf"


sc = sapien.Scene()
sc.set_timestep(1 / 100)
sc.add_ground(0)
sc.set_ambient_light([0.5, 0.5, 0.5])
sc.add_directional_light([0.3, 0.3, -1], [1.5, 1.5, 1.5])
loader = sc.create_urdf_loader()
loader.fix_root_link = True
art = loader.load(str(execution_urdf))
if art is None:
    print("FAIL s13b: URDF failed to load")
    sys.exit(1)


def articulation_poses_are_finite(articulation):
    """Return separate root/link finiteness facts for the executed body."""

    def pose_is_finite(pose):
        try:
            return bool(
                np.isfinite(np.asarray(pose.p, dtype=float)).all()
                and np.isfinite(np.asarray(pose.q, dtype=float)).all()
            )
        except (AttributeError, TypeError, ValueError):
            return False

    try:
        root_finite = pose_is_finite(articulation.get_pose())
        links_finite = all(pose_is_finite(link.get_pose()) for link in articulation.get_links())
    except (AttributeError, TypeError):
        return False, False
    return root_finite, links_finite


dof = int(art.dof)
active = art.get_active_joints()
limits = [(float(j.get_limits()[0][0]), float(j.get_limits()[0][1])) for j in active]
expected_limits = [
    (float(joint["lower"]), float(joint["upper"]))
    for joint in report.get("movable", [])
    if isinstance(joint, dict) and "lower" in joint and "upper" in joint
]
limits_match = len(expected_limits) == len(limits) and all(
    abs(actual_lo - expected_lo) <= 1e-5 and abs(actual_up - expected_up) <= 1e-5
    for (actual_lo, actual_up), (expected_lo, expected_up) in zip(limits, expected_limits)
)

q0 = np.asarray(art.get_qpos(), dtype=float)
q_mid = None
for i in range(120):
    sc.step()
    if i == 89:
        q_mid = np.asarray(art.get_qpos(), dtype=float)
qpos = np.asarray(art.get_qpos(), dtype=float)
settle_root_pose_finite, settle_link_poses_finite = articulation_poses_are_finite(art)
settled_root_orientation_wxyz = [float(value) for value in art.get_pose().q]
settle_ok = bool(np.isfinite(qpos).all() and settle_root_pose_finite and settle_link_poses_finite)
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
        root_pose_finite, link_poses_finite = articulation_poses_are_finite(art)
        okq = bool(np.isfinite(cur).all() and root_pose_finite and link_poses_finite)
        sweep_ok = sweep_ok and okq
        sweep_detail.append(
            {
                "joint": i,
                "target": round(target, 3),
                "finite": okq,
                "root_pose_finite": root_pose_finite,
                "link_poses_finite": link_poses_finite,
            }
        )
    q = np.array(art.get_qpos(), dtype=float)
    q[i] = 0.0
    art.set_qpos(q)

cam = sc.add_camera("cam", 640, 480, np.deg2rad(60), 0.01, 10.0)
# pose joints half-open for an informative screenshot (doors/drawers visible)
q_show = np.array(
    [lo + 0.6 * (up - lo) if abs(lo) > abs(up) else lo + 0.6 * (up - lo) for lo, up in limits]
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
if not all(
    ledger.artifact_file_record_is_current(record) for record in sapien_representation["files"]
) or not ledger.execution_snapshot_is_current(execution_snapshot):
    print("FAIL s13b: URDF closure changed during fixed-input validation")
    sys.exit(1)
runtime_capability = ledger_writes.capture_runtime_capability(
    loader_modules=[sapien],
    sapien_module=sapien,
    entrypoint="SAPIEN Scene.create_urdf_loader.load",
    config={
        "loader_root": "immutable_snapshot_explicit_path",
        "fix_root_link": True,
        "timestep_s": 0.01,
        "settle_steps": 120,
        "late_window_start_step": 89,
        "joint_limit_steps": 30,
        "render_resolution": [640, 480],
    },
)
Image.fromarray(img).save(out / "sapien_314_cabinet.png")

(inst / f"model_data{model_id}.json").write_text(
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
    "limits_match": limits_match,
    "limits": [[round(a, 3), round(b, 3)] for a, b in limits],
    "settle_finite": settle_ok,
    "root_pose_finite": settle_root_pose_finite,
    "link_poses_finite": settle_link_poses_finite,
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
    and checks["limits_match"]
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
    sapien_representation,
    {
        "format": "usd",
        "uri": ledger.to_portable_uri(args.source_usd),
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": sha256(args.source_usd),
        "files": ledger_writes.representation_files(args.source_usd),
        "collision_meta": {
            "mode": "unknown",
            "unknown_reason": "source USD collision authoring was not geometrically probed",
        },
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

mass_override = {"value": None, "status": "unknown"}

verification_run_id = out.name

conventions_block = {
    # This validator intentionally executes the articulation with a fixed
    # root. Publish that fact instead of presenting the run as evidence that
    # a free body dynamically settled into the same pose.
    "is_static": True,
    "z_policy": conventions.CONSERVATIVE_DEFAULTS["z_policy"],
    "footprint_shape": conventions.CONSERVATIVE_DEFAULTS["footprint_shape"],
    "stable_poses": [
        {
            "pose_id": "fixed_root_identity",
            "orientation_wxyz": ledger.IDENTITY_WXYZ,  # URDF Z-up -> identity
            "is_default": True,
            **(
                {
                    "measured_against": {
                        "backend": "sapien",
                        "run_id": verification_run_id,
                    }
                }
                if checks["status"] == "pass"
                else {}
            ),
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

source_block = {
    "kind": "retrieved",
    "library": "NVIDIA Isaac Assets 5.1",
    "group": "Sektion_Cabinet",
    "file": source_usd_path.name,
    "license": {
        "spdx": None,
        "status": "unknown",
        "terms_note": "NVIDIA asset EULA; verify before redistribution",
    },
    "retrieved_at": datetime.date.fromtimestamp(source_usd_path.stat().st_mtime).isoformat(),
    "source_manifest_path": str(inst / "export_report.json"),
}

verified_digest = ledger.reps_digest({"representations": representations}, "sapien")

# The human-readable run report is one write, before any receipt.  Trust is
# carried by the per-model immutable artifact below, not by this mutable name.
validation_payload = json.loads(
    json.dumps(checks, default=lambda o: o.item() if hasattr(o, "item") else str(o))
)
ledger._atomic_write_json(out / "cabinet314_validation.json", validation_payload)

model_entry = ledger.new_model_entry(
    model=model_id,
    representations=representations,
    mesh_bbox_m=bbox,
    mesh_up_axis="Z",
    origin_convention="base-at-floor",
    size_resolution=size_resolution,
    conventions=conventions_block,
    source=source_block,
    verification=[],
    articulation=articulation,
    mass_override=mass_override,
    inertial=ledger.unknown_inertial("urdf_inertial"),
)
verification_timestamp = datetime.datetime.now().isoformat(timespec="seconds")
verification_payload = ledger_writes.issue_qualified_verification(
    issuer="asset.s13b_joint_sweep.v1",
    asset_key=asset,
    model_id=model_id,
    run_id=verification_run_id,
    timestamp=verification_timestamp,
    reps_digest=verified_digest,
    inputs={
        "task": {
            "asset_key": asset,
            "model_id": model_id,
            "allow_free_joints": bool(args.allow_free_joints),
            "execution_urdf": ledger.to_portable_uri(execution_urdf),
        },
    },
    thresholds={
        "min_screenshot_std": 1.0,
        "allow_free_joints": bool(args.allow_free_joints),
    },
    result={
        "schema": "asset_joint_sweep_result.v2",
        "loaded": True,
        "dof": dof,
        "expected_dof": expected_movable,
        "limits_match": limits_match,
        "settle_finite": settle_ok,
        "converged": converged,
        "free_joint_count": len(free_joints),
        "sweep_finite": sweep_ok,
        "screenshot_std": float(img.std()),
        "fix_root_link": True,
        "root_pose_id": "fixed_root_identity",
        "root_orientation_wxyz": settled_root_orientation_wxyz,
        "z_policy": conventions_block["z_policy"],
        "details": validation_payload,
    },
    model_entry=model_entry,
    execution_snapshot=execution_snapshot,
    runtime_capability=runtime_capability,
)
verification_artifact = ledger_writes.publish_verification_evidence(
    asset_path / "verification_evidence", verification_payload
)
verification_entry = ledger_writes.receipt_from_evidence(
    verification_payload, verification_artifact
)
model_entry["verification"] = [verification_entry]

led = ledger.upsert_model(
    existing_ledger,
    asset=asset,
    category=category,
    kind="articulated",
    # The source USD is registered as this model's isaacsim representation
    # (see representations above), so the asset is cross-backend capable by
    # construction; inertial rides along as a structured unknown whose basis
    # says the engine reads the mass distribution out of the URDF.
    profile="cross_backend",
    # Single-articulated-asset imports go through no manifest at all, so
    # there is nothing to attribute the category claim to. The 2026-08-10
    # semantics audit found exactly 2 such assets and this is why they read
    # as unknown -- recording that honestly beats inventing a basis.
    identity={"basis": "unknown", "evidence": None, "verified": False},
    aliases=[category],
    colors=[],
    materials=[],
    tags=["articulated", "external", "reverse-import"],
    model_entry=model_entry,
)
violations = ledger.validate_ledger(led, check_files=True)
if violations:
    # H3: gate aligned with import_materialize's own gate (a ledger that
    # fails its own validator must not become authoritative) -- this used
    # to WARN and write anyway. The run snapshot below (bundle/validation
    # JSON, model_data<model_id>.json, screenshot) is unaffected: those are a
    # point-in-time record of this run, not the pool's authoritative state.
    #
    # I-3 (review round 1): blocking the write alone left this silent --
    # the script could still print PASS s13b / exit 0 on a run whose ledger
    # never got persisted, which is *more* likely to go unnoticed than the
    # old WARN-and-write-anyway behavior. The headline goes to stdout in
    # this script's own FAIL style (grep-able, and folded into the final
    # exit code below); the per-violation trace stays on stderr as detail.
    print(f"FAIL s13b: schema violations ({len(violations)})")
    print("NOT writing authoritative ledger:", file=sys.stderr)
    for v in violations:
        print(f"  {v.path} [{v.code}] {v.message}", file=sys.stderr)
else:
    # atomic + fcntl-locked write (lib.ledger.write_ledger) instead of a
    # bare write_text() -- same torn-file risk import_materialize's I-1
    # fix addressed (crash-isolation subprocesses are killed on timeout).
    ledger_writes.write_validated(lp, led, expected=existing_ledger)

# Back-compat snapshot at the original bundle path: same authoritative
# content, re-derived (flattened) from the ledger rather than hand-assembled.
flat_bundle = next(
    b for b in ledger.to_ir_bundles(led) if b["asset_id"] == f"{led['asset_id']}_m{model_id}"
)
(out / "cabinet314_bundle.json").write_text(json.dumps(flat_bundle, indent=2, ensure_ascii=False))
print(json.dumps(checks, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
# I-3: a ledger violation must fail the run even when the physics checks
# themselves passed -- the ledger not landing is exactly the kind of
# failure this exit code exists to signal.
overall_ok = checks["status"] == "pass" and not violations
print("PASS s13b" if overall_ok else "FAIL s13b")
sys.exit(0 if overall_ok else 1)
