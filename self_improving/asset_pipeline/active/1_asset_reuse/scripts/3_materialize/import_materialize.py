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
import json
import math
import shutil
import sys
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
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import gen_fragment  # noqa: E402
from lib import conventions as conv_lib  # noqa: E402
from lib import ledger as ledger_mod  # noqa: E402
from lib import (
    ledger_writes,  # noqa: E402
    writer_paths,  # noqa: E402
)
from lib.ledger import (  # noqa: E402
    ledger_path,
    new_model_entry,
    reps_digest,
    to_ir_bundles,
    unknown_inertial,
    upsert_model,
    validate_ledger,
)
from runtime_config import ASSET_CATALOG  # noqa: E402

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
    default=str(ASSET_CATALOG),
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
records = json.loads((staging / "staging_manifest.json").read_text())
if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
    raise SystemExit("staging manifest must be a list of records")
try:
    seen_record_models = set()
    for record in records:
        asset = ledger_mod.canonical_asset_key(record.get("asset"))
        model = ledger_mod.canonical_model_id(record.get("model"))
        ledger_mod.claim_asset_model(seen_record_models, asset, model)
        record["asset"] = asset
        record["model"] = model
except (
    ledger_mod.UnsafeAssetKeyError,
    ledger_mod.InvalidModelIdError,
    ledger_mod.DuplicateAssetModelError,
) as exc:
    raise SystemExit(str(exc)) from exc

# The library groups assets by source provider. Provider is the only asset
# property that never changes (category gains aliases, licence goes unknown ->
# declared, usable flips when a measurement campaign unlocks an asset), which
# is why it -- and nothing else -- is allowed into the path.
_WEB_PROVIDER_DIRS = {
    "objaverse": "objaverse",
    "github_tree": "github",
    "github": "github",
}


def _provider_dir(record):
    """Mirrors exactly the branch that writes source.library further down
    (group `web_*` came off the web tiers, everything else off the NVIDIA
    Isaac server), so a new asset's directory and its ledger's declared source
    can never disagree."""
    if not record["group"].startswith("web_"):
        return "nvidia"
    provider = record.get("source_provider") or "github"
    if provider not in _WEB_PROVIDER_DIRS:
        raise SystemExit(
            f"unknown source provider {provider!r}: add it to _WEB_PROVIDER_DIRS. "
            "A new retrieval tier gets its own library subdir -- silently landing "
            "it in github/ would make the ledger's source and its path disagree."
        )
    return _WEB_PROVIDER_DIRS[provider]


_PROVIDER_BY_ASSET = {}
for _record in records:
    _asset = _record["asset"]
    _provider = _provider_dir(_record)
    _previous_provider = _PROVIDER_BY_ASSET.setdefault(_asset, _provider)
    if _previous_provider != _provider:
        raise SystemExit(
            f"staging records disagree on provider for {_asset}: "
            f"{_previous_provider!r} != {_provider!r}"
        )

out.mkdir(parents=True, exist_ok=True)
(out / "shots").mkdir(exist_ok=True)


def adir_for(asset):
    """This asset's directory. An asset that already exists keeps the home it
    has -- a re-import must never fork a second directory under a different
    provider -- and a first-time asset is placed by the decision above."""
    asset = ledger_mod.canonical_asset_key(asset)
    found = ledger_mod.asset_dir(lib, asset)
    candidate = found if found is not None else lib / _PROVIDER_BY_ASSET[asset] / asset
    return writer_paths.contained_path(lib, candidate)


def _rejected_model_files(asset_dir, model_id):
    """Enumerate one rejected model's cleanup set without following symlinks."""

    asset_dir = Path(asset_dir)
    model_id = int(model_id)
    direct = [
        asset_dir / "visual" / f"base{model_id}.glb",
        asset_dir / "collision" / f"base{model_id}.glb",
        asset_dir / f"model_data{model_id}.json",
    ]
    snapshots = writer_paths.contained_path(asset_dir, asset_dir / "snapshots")
    discovered = list(snapshots.glob(f"m{model_id}_*.png")) if snapshots.is_dir() else []
    return [
        writer_paths.contained_path(asset_dir, path)
        for path in (*direct, *discovered)
        if path.exists() or path.is_symlink()
    ]


def _unlink_rejected_files(asset_dir, paths):
    for path in paths:
        safe = writer_paths.contained_path(asset_dir, path)
        safe.unlink(missing_ok=True)


ROTX90 = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_CLEANUP_CANDIDATE_SCHEMA = "asset_materialize_cleanup_candidate.v1"


def _canonical_sha256(value):
    return hashlib.sha256(ledger_mod.canonical_json_bytes(value)).hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _model_with_id(document, model_id):
    if not isinstance(document, dict):
        return None
    return next(
        (
            model
            for model in document.get("models", [])
            if isinstance(model, dict) and model.get("model_id") == model_id
        ),
        None,
    )


def _cleanup_candidate(asset_dir, model_entry, ledger_before):
    """Capture the exact rejected output identity before attempting its CAS."""

    asset_dir = Path(asset_dir)
    model_id = model_entry["model_id"]
    before_model = _model_with_id(ledger_before, model_id)
    candidate_paths = {
        asset_dir / "visual" / f"base{model_id}.glb",
        asset_dir / "collision" / f"base{model_id}.glb",
        asset_dir / f"model_data{model_id}.json",
    }
    for representation in model_entry.get("representations", []):
        if not isinstance(representation, dict) or representation.get("role") != "snapshot":
            continue
        for record in representation.get("files", []):
            if not isinstance(record, dict) or not isinstance(record.get("uri"), str):
                continue
            path = Path(ledger_mod.resolve_uri(record["uri"]))
            if path.is_relative_to(asset_dir):
                candidate_paths.add(path)
    files = []
    for path in sorted(candidate_paths):
        path = writer_paths.contained_path(asset_dir, path)
        if not path.is_file():
            continue
        relative = path.relative_to(asset_dir).as_posix()
        files.append({"path": relative, "sha256": sha256(path)})
    return {
        "schema": _CLEANUP_CANDIDATE_SCHEMA,
        "model_id": model_id,
        "model_sha256": _canonical_sha256(model_entry),
        "ledger_before_model_sha256": (
            _canonical_sha256(before_model) if before_model is not None else None
        ),
        "ledger_write_committed": False,
        "files": sorted(files, key=lambda record: record["path"]),
    }


def _validated_cleanup_candidate(asset_dir, model_id, value):
    """Return a normalized candidate descriptor, or ``None`` when untrusted."""

    if not isinstance(value, dict) or value.get("schema") != _CLEANUP_CANDIDATE_SCHEMA:
        return None
    if value.get("model_id") != model_id:
        return None
    model_digest = value.get("model_sha256")
    before_digest = value.get("ledger_before_model_sha256")
    write_committed = value.get("ledger_write_committed", False)
    files = value.get("files")
    if (
        not _is_sha256(model_digest)
        or (before_digest is not None and not _is_sha256(before_digest))
        or not isinstance(write_committed, bool)
        or not isinstance(files, list)
    ):
        return None
    normalized_files = []
    seen = set()
    allowed_direct = {
        f"visual/base{model_id}.glb",
        f"collision/base{model_id}.glb",
        f"model_data{model_id}.json",
    }
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            return None
        relative = record["path"]
        digest = record["sha256"]
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or Path(relative).as_posix() != relative
            or relative in seen
            or not _is_sha256(digest)
        ):
            return None
        try:
            path = writer_paths.contained_path(asset_dir, Path(asset_dir) / relative)
        except (OSError, ValueError):
            return None
        if path.relative_to(asset_dir).as_posix() != relative:
            return None
        relative_path = Path(relative)
        if relative not in allowed_direct and not (
            relative_path.parent == Path("snapshots")
            and relative_path.name.startswith(f"m{model_id}_")
            and relative_path.suffix == ".png"
        ):
            return None
        seen.add(relative)
        normalized_files.append({"path": path, "sha256": digest})
    return {
        "model_sha256": model_digest,
        "ledger_before_model_sha256": before_digest,
        "ledger_write_committed": write_committed,
        "files": tuple(normalized_files),
    }


def _candidate_files_still_present(candidate):
    return any(
        record["path"].is_file() and sha256(record["path"]) == record["sha256"]
        for record in candidate["files"]
    )


def _unlink_candidate_files(asset_dir, candidate):
    """Unlink only names whose bytes still match this worker's candidate."""

    for record in candidate["files"]:
        path = writer_paths.contained_path(asset_dir, record["path"])
        if path.is_file() and sha256(path) == record["sha256"]:
            path.unlink()


def _model_representation_files_are_current(model):
    records = [
        record
        for representation in model.get("representations", [])
        if isinstance(representation, dict)
        for record in representation.get("files", [])
        if isinstance(record, dict)
    ]
    return bool(records) and all(
        ledger_mod.artifact_file_record_is_current(record) for record in records
    )


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


def actor_min_world_z(actor):
    """Read the full collision actor's world AABB without mesh sampling."""

    bounds = actor.get_global_aabb()
    if hasattr(bounds, "minimum"):
        minimum = bounds.minimum
    elif isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        minimum = bounds[0]
    else:
        array = np.asarray(bounds, dtype=float)
        if array.shape != (2, 3):
            raise ValueError("runtime actor exposes no canonical world AABB")
        minimum = array[0]
    value = float(minimum[2])
    if not math.isfinite(value):
        raise ValueError("runtime actor AABB is not finite")
    return value


def settle_check(vis, col, height, flat):
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
    min_corner_z = actor_min_world_z(actor)
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
        "rest_orientation_wxyz": [round(float(value), 6) for value in final.q],
    }
    checks["pass"] = checks["settled"] and checks["no_penetration"] and checks["tilt_ok"]
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
    adir = adir_for(asset_name)
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
            json.dumps(row_dict, default=lambda o: o.item() if hasattr(o, "item") else str(o))
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
            scene.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
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
            scene.apply_transform(trimesh.transformations.scale_matrix(size_res["scale"]))
            lo, hi = scene.bounds
            size = [float(b - a) for a, b in zip(lo, hi)]
        conv = conv_lib.inherit_conventions(meta.get("category", "unknown"), args.reference_catalog)
        if not (0.01 < size[1] < 1.0):
            raise ValueError(f"implausible height {size[1]:.3f}m")

        vis = adir_for(asset) / "visual" / f"base{model}.glb"
        col = adir_for(asset) / "collision" / f"base{model}.glb"

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

                merged = scene.dump(concatenate=True) if isinstance(scene, trimesh.Scene) else scene
                cmesh = coacd.Mesh(np.asarray(merged.vertices), np.asarray(merged.faces))
                parts = coacd.run_coacd(cmesh, threshold=0.05)
                cs = trimesh.Scene()
                for vs, fs in parts:
                    cs.add_geometry(trimesh.Trimesh(np.asarray(vs), np.asarray(fs)))
                cs.export(str(col))
                row["collision_mode"] = f"coacd:{len(parts)}parts"
            else:
                shutil.copy(vis, col)
                row["collision_mode"] = "copy"
            (adir_for(asset) / f"model_data{model}.json").write_text(
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
        settle_physics_pass = checks["pass"]
        row["status"] = "accepted" if checks["pass"] else "rejected"
        if not checks["pass"]:
            row["reasons"] = [k for k in ("settled", "no_penetration", "tilt_ok") if not checks[k]]

        # ---- post-render identity check -------------------------------
        # A web-sourced candidate had no picture before download, so the
        # pre-download gate could only wave it through as "unverifiable".
        # But the settle shot above IS a picture of the thing that would
        # enter the pool -- so this is where "nothing to look at" stops
        # being a free pass. A match upgrades the identity claim to
        # basis=vlm/verified=true (same standard the NVIDIA path meets);
        # a mismatch rejects the model outright; model trouble (unreadable)
        # keeps the honest degraded claim rather than blocking on infra.
        #
        # Pre-verified (basis=vlm) models are re-checked too, not skipped:
        # the pre-download gate saw a 256px server thumbnail, and at the
        # 50k corpus that picture can be an ambiguous crop -- dsready's
        # TrafficCamera05 thumbnail is indistinguishable from a sledgehammer
        # (2026-08-12). The settle shot is a second, independent viewpoint
        # of the REAL converted geometry; agreement keeps verified=true with
        # the extra evidence on file, disagreement evicts the model even
        # though the thumbnail once matched.
        identity_final = dict(IDENTITY)
        post_verdict = None
        if checks["pass"] and args.identity_basis in ("requested_by_acquire", "vlm"):
            from lib import a6_verify as a6

            shot = out / "shots" / f"{asset}_m{model}.png"
            verdict = a6.verify_image(shot, meta.get("category"), aliases=meta.get("aliases"))
            vpath = out / f"identity_post_render_{asset}_m{model}.json"
            vpath.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n")
            row["identity_post_render"] = verdict.get("verdict")
            if verdict["verdict"] == a6.MATCH:
                identity_final = {
                    "basis": "vlm",
                    "evidence": str(vpath),
                    "verified": True,
                }
                post_verdict = verdict
            elif verdict["verdict"] == a6.MISMATCH:
                checks["pass"] = False
                row["status"] = "rejected"
                row["reasons"] = [f"identity_mismatch_post_render:{verdict.get('seen_as')}"]
            elif args.identity_basis == "requested_by_acquire":
                # fail CLOSED, not open: an unverifiable-source candidate has
                # never shown any visual evidence -- this settle shot was its
                # first and only chance, and "unreadable" here means the
                # asset would enter the pool with ZERO identity evidence.
                # Measured cost of the old fail-open: the VLM cache was
                # silently absent after the machine migration and a Khronos
                # sofa entered the pool as category "tvon_the_table"
                # (2026-08-15). Infra trouble on a pre-verified (basis=vlm)
                # candidate still keeps the honest degraded claim above.
                checks["pass"] = False
                row["status"] = "rejected"
                row["reasons"] = [f"identity_unverifiable_post_render:{verdict.get('verdict')}"]

        aliases = meta.get("aliases", [])
        colors = meta.get("colors", [])
        materials = meta.get("materials", [])

        # Materialize source provenance before representations are assembled:
        # files[] must describe the paths the ledger will actually publish,
        # not a run-scoped web cache that happens to contain identical bytes.
        usd_local = r.get("usd_local")
        manifest_path = (
            Path(usd_local).parent / "SOURCE_MANIFEST.json"
            if usd_local
            else lib / "_source" / r["group"] / "SOURCE_MANIFEST.json"
        )
        is_web = r["group"].startswith("web_")
        if is_web:
            web_src_dir = lib / "_source" / r["group"]
            web_src_dir.mkdir(parents=True, exist_ok=True)
            original = Path(r["usd_local"]).resolve()
            source_members = ledger_writes.representation_files(original)
            manifest_files = {}
            for member in source_members:
                source_member = ledger_mod.resolve_uri(member["uri"])
                relative = source_member.relative_to(original.parent)
                mirrored_member = web_src_dir / relative
                mirrored_member.parent.mkdir(parents=True, exist_ok=True)
                if not mirrored_member.exists():
                    shutil.copy(source_member, mirrored_member)
                manifest_files[relative.as_posix()] = member["sha256"]
            mirrored = web_src_dir / original.name
            manifest_path = web_src_dir / "SOURCE_MANIFEST.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "files": dict(sorted(manifest_files.items())),
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

        # v3 frame/geometry_state: facts this writer ALREADY knows and, until
        # 2026-08-24, dropped on the floor. migrate_v3 backfilled them for the
        # 08-15 cohort, so every asset acquired AFTER that batch landed without
        # them (7 of them by 08-24) -- the gap grew by one per import. up_axis
        # is Y because this converter normalizes to it (that is exactly what
        # `rotated` records); the writer bakes scale into the GLB and places
        # the origin at the footprint centre.
        glb_frame = {"up_axis": "Y"}
        glb_geometry_state = {"scale_baked": True, "origin": "bottom-center"}

        reps = [
            {
                "format": "glb",
                "uri": ledger_mod.to_portable_uri(vis),
                "backend": "sapien",
                "role": "visual",
                "frame": dict(glb_frame),
                "geometry_state": dict(glb_geometry_state),
                "sha256": sha256(vis),
                "files": ledger_writes.representation_files(vis),
                "metadata": {
                    "derived_from": r["usd"],
                    "converter": "omni.kit.asset_converter@isaac-5.1",
                    "conversion_params": {"rotated_z2y": rotated},
                },
            },
            {
                "format": "glb",
                "uri": ledger_mod.to_portable_uri(col),
                "backend": "sapien",
                "role": "collision",
                "frame": dict(glb_frame),
                "geometry_state": dict(glb_geometry_state),
                "sha256": sha256(col),
                "files": ledger_writes.representation_files(col),
                "collision_meta": {
                    "mode": "explicit_mesh",
                    **(
                        {
                            "decomposer": "coacd",
                            "params": {"threshold": 0.05},
                            "hull_count": int(row["collision_mode"].split(":", 1)[1][:-5]),
                        }
                        if row["collision_mode"].startswith("coacd:")
                        else {}
                    ),
                },
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
                # web sources: register the PERSISTENT _source mirror (created
                # a few lines below), never the run-scoped cache -- four
                # ledgers pointed into /tmp webcache dirs that a cleanup then
                # deleted, and the audit flagged them file_missing
                # (2026-08-13). NVIDIA usd_local already IS the mirror.
                "uri": ledger_mod.to_portable_uri(
                    r["usd_local"]
                    if not r["group"].startswith("web_")
                    else lib / "_source" / r["group"] / Path(r["usd_local"]).name
                ),
                "backend": ("isaacsim" if not r["group"].startswith("web_") else "portable"),
                "role": "visual_and_collision",
                "sha256": r["usd_sha256"],
                "files": ledger_writes.representation_files(r["usd_local"]),
                "collision_meta": {
                    "mode": "unknown",
                    "unknown_reason": "source collision authoring was not probed",
                },
                # only the web branch gets the frame claim: that entry is the
                # normalized GLB mirror this writer produced. The NVIDIA branch
                # registers the UNTOUCHED source USD -- asserting our own
                # normalization over someone else's artifact would be a lie.
                **(
                    {
                        "frame": dict(glb_frame),
                        "geometry_state": dict(glb_geometry_state),
                    }
                    if r["group"].startswith("web_")
                    else {}
                ),
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
                snap_dir = adir_for(asset) / "snapshots"
                snap_dir.mkdir(parents=True, exist_ok=True)
                snap_path = snap_dir / f"m{model}_default.png"
                Image.fromarray(img).save(snap_path)
                reps.append(
                    {
                        "format": "png",
                        "uri": ledger_mod.to_portable_uri(snap_path),
                        "backend": "portable",
                        "role": "snapshot",
                        "sha256": sha256(snap_path),
                        "files": ledger_writes.representation_files(snap_path),
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

        # thresholds mirror settle_check()'s real gate constants above
        # (0.002 late-drift, -0.005 no-penetration floor -- note this is
        # -5mm, not the -2mm the module docstring states; tilt 15/45deg).
        verification_run_id = out.name
        verification_timestamp = dt.datetime.now().isoformat(timespec="seconds")
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
                    **(
                        {
                            "measured_against": {
                                "backend": "sapien",
                                "run_id": verification_run_id,
                            }
                        }
                        if settle_physics_pass
                        else {}
                    ),
                }
            ],
            "inherited_from": conv.get("precedent"),
        }
        loader_model = {"model_id": model, "representations": reps}
        authoritative_asset_dir = adir_for(asset)
        execution_snapshot = ledger_writes.publish_execution_snapshot(
            authoritative_asset_dir / "verification_inputs",
            source_asset_dir=authoritative_asset_dir,
            asset_key=asset,
            model_entry=loader_model,
            extra_files={
                "reference_catalog": args.reference_catalog,
                "source": r["usd_local"],
            },
        )
        execution_root = Path(ledger_mod.resolve_uri(execution_snapshot["root_uri"]))
        snapshot_asset = execution_root / "assets" / "objects" / asset
        snapshot_vis = snapshot_asset / Path(vis).resolve().relative_to(
            authoritative_asset_dir.resolve()
        )
        snapshot_col = snapshot_asset / Path(col).resolve().relative_to(
            authoritative_asset_dir.resolve()
        )
        trusted_checks, _trusted_image = settle_check(
            snapshot_vis,
            snapshot_col,
            size[1],
            bool(meta.get("flat")),
        )
        if not ledger_mod.execution_snapshot_is_current(execution_snapshot) or not all(
            ledger_mod.artifact_file_record_is_current(record)
            for representation in reps
            if representation.get("backend") == "sapien"
            and representation.get("role") != "snapshot"
            for record in representation["files"]
        ):
            raise ledger_writes.VerificationEvidenceError(
                "authoritative SAPIEN closure changed during snapshot validation"
            )
        runtime_capability = ledger_writes.capture_runtime_capability(
            loader_modules=[sapien],
            sapien_module=sapien,
            entrypoint="SAPIEN ActorBuilder GLB visual/collision settle",
            config={
                "loader_root": "immutable_snapshot_explicit_paths",
                "timestep_s": 0.01,
                "settle_steps": 900,
                "sample_interval_steps": 50,
                "spawn_pose_wxyz": ROTX90,
                "spawn_z_m": 0.005,
                "collision_loader": "add_multiple_convex_collisions_from_file",
            },
        )
        verification_thresholds = {
            "max_late_drift_m": 0.002,
            "min_support_z_m": -0.005,
            "max_tilt_deg": float(trusted_checks["tilt_limit"]),
        }
        finite_result = all(
            math.isfinite(float(trusted_checks[field]))
            for field in ("late_drift_m", "min_corner_z_m", "tilt_deg")
        )
        origin_z_m = float(trusted_checks["final_z_m"]) if finite_result else None
        strict_result = {
            "schema": "asset_settle_result.v2",
            "finite": finite_result,
            "late_drift_m": float(trusted_checks["late_drift_m"]),
            "support_z_m": float(trusted_checks["min_corner_z_m"]),
            "tilt_deg": float(trusted_checks["tilt_deg"]),
            "rest_orientation_wxyz": list(trusted_checks["rest_orientation_wxyz"]),
            "origin_z_m": origin_z_m,
            "derived_z_policy": (
                ledger_mod.z_policy_from_origin(origin_z_m) if finite_result else None
            ),
            "details": {"checks": trusted_checks, "post_verdict": post_verdict},
        }
        physical_verdict = ledger_mod.qualified_verification_verdict(
            "sapien", "settle", verification_thresholds, strict_result
        )
        settle_physics_pass = physical_verdict == "pass"
        default_pose = conv_v1["stable_poses"][0]
        if settle_physics_pass:
            default_pose["orientation_wxyz"] = strict_result["rest_orientation_wxyz"]
            conv_v1["z_policy"] = strict_result["derived_z_policy"]
            default_pose["measured_against"] = {
                "backend": "sapien",
                "run_id": verification_run_id,
            }
        else:
            default_pose.pop("measured_against", None)
            checks["pass"] = False
            row["status"] = "rejected"
            row["reasons"] = ["fixed_snapshot_settle_failed"]
        row["trusted_snapshot_check"] = trusted_checks
        verified_digest = reps_digest(loader_model, "sapien")
        physical_result = json.loads(
            json.dumps(
                strict_result,
                default=lambda value: value.item() if hasattr(value, "item") else str(value),
            )
        )
        entry = new_model_entry(
            model=model,
            representations=reps,
            mesh_bbox_m=size,
            mesh_up_axis="Y",
            origin_convention="bottom-center",
            size_resolution=size_res,
            conventions=conv_v1,
            source=source_v1,
            verification=[],
            # This writer declares profile=cross_backend (it registers the
            # source USD as the isaacsim representation), and cross_backend
            # requires the inertial KEY to exist. engine_derived + nulls is
            # the honest value for a rigid import: no asset-side measurement
            # exists, the engine infers mass distribution from collision
            # geometry. Omitting this made the writer fail its own validator.
            inertial=unknown_inertial("engine_derived"),
        )
        evidence_payload = ledger_writes.issue_qualified_verification(
            issuer="asset.materialize_settle.v1",
            asset_key=asset,
            model_id=model,
            run_id=verification_run_id,
            timestamp=verification_timestamp,
            reps_digest=verified_digest,
            inputs={
                "task": {
                    "asset_key": asset,
                    "model_id": model,
                    "identity_basis": args.identity_basis,
                    "staging_record": r,
                    "execution_root": execution_snapshot["root_uri"],
                },
            },
            thresholds=verification_thresholds,
            result=physical_result,
            model_entry=entry,
            execution_snapshot=execution_snapshot,
            runtime_capability=runtime_capability,
        )
        evidence_record = ledger_writes.publish_verification_evidence(
            adir_for(asset) / "verification_evidence", evidence_payload
        )
        settle_entry = ledger_writes.receipt_from_evidence(evidence_payload, evidence_record)
        entry["verification"] = [settle_entry]

        try:
            loaded = ledger_mod.load_asset_ledger(args.library_dir, asset)
        except FileNotFoundError:
            loaded = None
            led_path = ledger_path(args.library_dir, asset)
            existing = None
        else:
            led_path = loaded.ledger_path
            existing = loaded.document
        # A2 语义回填：把 VLM 在 settle shot 上观察到的颜色/材质并进语义栏
        # （声明优先、观察只填空），并记录 attribute_basis。只对**新建账本**
        # 生效——已有账本的资产级语义受 upsert 防漂移契约保护，这里改任何
        # 值都会（正确地）触发 ValueError，所以沿用账本现值。
        attribute_basis = None
        if existing is None and post_verdict is not None:
            from lib import a6_verify as a6_merge_mod

            colors, _cb = a6_merge_mod.merge_observed_attributes(colors, post_verdict.get("colors"))
            materials, _mb = a6_merge_mod.merge_observed_attributes(
                materials, post_verdict.get("materials")
            )
            basis = {k: v for k, v in (("colors", _cb), ("materials", _mb)) if v}
            attribute_basis = basis or None
        elif existing is not None:
            # 已有账本：声明若只是账本语义的子集（含空——A2 回填造成的常态），
            # 沿用账本现值以通过防漂移等值检查；声明里出现账本没有的新值则
            # 保持原样送检，让 upsert 的 ValueError 把真实冲突暴露出来。
            ex_sem = existing.get("semantics", {})
            if all(v in ex_sem.get("colors", []) for v in colors):
                colors = ex_sem.get("colors", colors)
            if all(v in ex_sem.get("materials", []) for v in materials):
                materials = ex_sem.get("materials", materials)
            if all(v in ex_sem.get("aliases", []) for v in aliases):
                aliases = ex_sem.get("aliases", aliases)
        # A re-import replaces the model entry wholesale, which silently
        # DOWNGRADED hand-audited licenses: 301_cup was declared CC-BY-4.0 by
        # the 2026-08-09 audit, got re-imported on 08-10 for a collision fix,
        # and came out status=unknown again. The audit is attached to the
        # SOURCE (library/group/file), not to the import event -- so as long
        # as those coordinates are unchanged and the incoming record knows
        # nothing the existing one doesn't, the audited license survives.
        if existing:
            for em in existing.get("models", []):
                if em.get("model_id") != model:
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
            materials=materials,
            tags=["rigid", "external", "batch"],
            model_entry=entry,
            attribute_basis=attribute_basis,
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
            materials=materials,
            tags=["rigid", "external", "batch"],
            model_entry=entry,
            attribute_basis=attribute_basis,
        )
        # A rejected worker is cleaned later by the driver, after all isolated
        # subprocesses have returned.  Capture both sides of this worker's CAS
        # now: the exact model it intends to publish, the same-model entry it
        # observed before the CAS, and hashes for every model-scoped path it
        # created.  The driver must not infer ownership from model_id alone --
        # another worker may legally win that name before this process returns.
        row["cleanup_candidate"] = _cleanup_candidate(
            adir_for(asset),
            entry,
            existing,
        )
        # Authoritative writes are whole-ledger writes.  Every surviving model
        # and every loader dependency therefore has to match disk at the write
        # boundary; a stale sibling is typed debt to repair, not something a
        # fresh model may silently republish.
        violations = validate_ledger(led, check_files=True)
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
            ledger_writes.write_validated(led_path, led, expected=existing)
            row["cleanup_candidate"]["ledger_write_committed"] = True
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
            b for b in to_ir_bundles(bundle_ledger) if b["asset_id"].endswith(f"_m{model}")
        )
        (bundles_dir / f"{asset}_m{model}.json").write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False)
        )
        if row["status"] == "accepted":
            row.pop("cleanup_candidate", None)
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
        asset_dir = adir_for(a)
        try:
            loaded = ledger_mod.load_asset_ledger(args.library_dir, a)
        except FileNotFoundError:
            loaded = None
            lp = ledger_path(args.library_dir, a)
            led_on_disk = None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            row.setdefault("reasons", []).append(f"unsafe ledger location: {exc}")
            continue
        else:
            lp = loaded.ledger_path
            led_on_disk = loaded.document
        current_model = _model_with_id(led_on_disk, m)
        raw_cleanup_candidate = row.get("cleanup_candidate")
        cleanup_candidate = _validated_cleanup_candidate(asset_dir, m, raw_cleanup_candidate)
        if raw_cleanup_candidate is not None and cleanup_candidate is None:
            row.setdefault("reasons", []).append("cleanup skipped: invalid candidate identity")
            continue

        # Model ids are reusable names, not ownership tokens.  A current,
        # file-consistent model that is neither the entry this worker observed
        # nor the candidate it published belongs to a concurrent winner and is
        # completely out of scope for this rejection.  An unchanged prior
        # model is likewise preserved when its declared closure is still
        # current: this worker did not damage it.  The full-entry digests make
        # the decision include receipts and source/convention facts, while the
        # CAS below proves the ledger did not change between this read and the
        # cleanup transaction.
        if cleanup_candidate is not None and current_model is not None:
            current_model_digest = _canonical_sha256(current_model)
            before_digest = cleanup_candidate["ledger_before_model_sha256"]
            candidate_digest = cleanup_candidate["model_sha256"]
            if current_model_digest == before_digest:
                if _model_representation_files_are_current(current_model):
                    continue
                if not _candidate_files_still_present(cleanup_candidate):
                    continue
            elif current_model_digest == candidate_digest:
                if not cleanup_candidate[
                    "ledger_write_committed"
                ] and _model_representation_files_are_current(current_model):
                    # A byte-identical concurrent publish is still not owned by
                    # a worker whose CAS never committed.
                    continue
            else:
                continue
        elif (
            cleanup_candidate is None
            and current_model is not None
            and _model_representation_files_are_current(current_model)
        ):
            # Crash rows produced before a candidate token exists retain the
            # same fail-closed rule: never evict a coherent authoritative model.
            continue

        pruned = None
        pruned_ledger = None
        if led_on_disk is not None:
            models = led_on_disk.get("models", [])
            pruned = [mm for mm in models if mm.get("model_id") != m]
            if len(pruned) != len(models) and pruned:
                pruned_ledger = json.loads(json.dumps(led_on_disk))
                pruned_ledger["models"] = pruned
                ledger_writes.validate_for_write(pruned_ledger)
        cleanup_paths = [] if cleanup_candidate is not None else _rejected_model_files(asset_dir, m)

        # A worker that died before publishing any model bytes leaves no
        # authoritative state to CAS and no files to clean.  Entering the
        # ledger transaction in that case would create ``ledger.lock`` as the
        # only child of an otherwise empty asset directory, preventing the
        # empty-shell cleanup below from removing it.
        if (
            led_on_disk is None
            and not cleanup_paths
            and (cleanup_candidate is None or not _candidate_files_still_present(cleanup_candidate))
        ):
            continue

        # The authoritative CAS happens first and cleanup runs while the same
        # ledger lock is still held.  A concurrent receipt/model append can
        # therefore never be overwritten and can never acquire the lock in
        # the gap before its referenced files are removed.
        if led_on_disk is None:
            replacement = None
        elif len(pruned) == len(led_on_disk.get("models", [])):
            replacement = led_on_disk
        elif pruned:
            replacement = pruned_ledger
        else:
            replacement = None
        if cleanup_candidate is None:
            cleanup_paths = tuple(cleanup_paths)

            def cleanup(asset_dir=asset_dir, paths=cleanup_paths):
                _unlink_rejected_files(asset_dir, paths)

        else:

            def cleanup(asset_dir=asset_dir, candidate=cleanup_candidate):
                _unlink_candidate_files(asset_dir, candidate)

        ledger_writes.commit_then_cleanup(
            lp,
            replacement,
            expected=led_on_disk,
            cleanup=cleanup,
        )

# fix-round-3 (configured item 1): an asset that never made it into the
# ledger at all this run -- brand new and every model rejected, or pruned
# down to nothing above -- would otherwise leave an empty shell behind
# (empty visual/, collision/, and any orphaned snapshots/ from a model that
# settled fine but got rejected on a schema violation before quarantine
# ever touched its snapshot file). Left alone, s9_build_shadow_root.py
# symlinks that empty shell straight into the shadow tree. Whole-directory
# Recursive removal is intentionally forbidden here: another writer could
# create a ledger after the check and before rmtree.  Non-recursive rmdir is
# race-safe because the kernel succeeds only if the directory is still empty.
for a in {row["asset"] for row in matrix}:
    adir = adir_for(a)
    if (
        adir.exists()
        and not ledger_path(args.library_dir, a).exists()
        and not any(adir.glob("model_data*.json"))
    ):
        ledger_writes.quarantine_empty_asset_directory(ledger_path(args.library_dir, a))

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
    try:
        loaded = ledger_mod.load_asset_ledger(args.library_dir, a)
    except FileNotFoundError:
        continue
    except (OSError, ValueError, json.JSONDecodeError):
        continue
    for m in loaded.document.get("models", []):
        latest = ledger_mod.latest_trusted_verification(m, "sapien", "settle", loaded.asset_key)
        if latest is None or latest.get("verdict") != "pass":
            continue
        if m.get("source", {}).get("license", {}).get("status") != "declared":
            unknown_in_run += 1
print(
    f"WARNING: {unknown_in_run} models with unknown license in view",
    file=sys.stderr,
)

(out / "import_matrix.json").write_text(
    json.dumps(matrix, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
)
acc = sum(1 for r in matrix if r["status"] == "accepted")
# M-4: fragment_assets restored to this-run scope (len(frag) is now
# run_assets-filtered, see I-4 above) rather than the whole-library count
# gen_fragment.generate would otherwise report.
print(f"PHASE2 accepted={acc}/{len(matrix)} fragment_assets={len(frag)}")
