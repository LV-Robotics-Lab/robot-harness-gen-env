#!/usr/bin/env python3
"""Backfill: map upstream RoboTwin asset_catalog.json entries into per-asset
v1 ledgers under --out (data/upstream_ledgers/<asset>/ledger.json).

Architecture (spec §9, docs/2026-08-08-asset-ingest-metadata-contract-design.md):
  derived core   -- identity/semantics/geometry/placement mapped straight off
                     the upstream catalog entry. Owned by the catalog; every
                     rerun overwrites these fields (that's the point: pull
                     upstream, rerun, stay in sync).
  incremental layer -- non-sapien representations (e.g. an isaacsim USD
                     registered via --isaac-usd), verification[], and a
                     license once it has been hand-audited to status
                     "declared". Owned by this project; a rerun reads the
                     existing on-disk ledger (if any) and carries these
                     forward untouched.

Only catalog models with usable == True are ingested; usable == False models
are skipped and recorded in the report (they were already excluded upstream
via the catalog's own derive_usable-style bookkeeping -- see `missing`).

Pure stdlib (matches lib/ledger.py's two-conda-env constraint), except this
script itself only ever runs under env-gen-yuxin.
"""

import argparse
import datetime
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger

ASSET_ID_PREFIX = "robotwin"
SOURCE_LIBRARY = "RoboTwin (upstream)"
DEFAULT_LICENSE = {
    "spdx": None,
    "status": "unknown",
    "terms_note": "RoboTwin asset library; mixed origins, unaudited (upstream)",
}
# Honest placeholder: this tool registers an isaacsim USD a human produced via
# the a_forward (line A, sapien->isaacsim) conversion pipeline, but does not
# run that conversion itself and so cannot attest to the exact tool/version
# used -- naming a specific converter here would be fabricating provenance
# this script never observed (same "don't invent it" rule as ledger.py's
# other converter fields).
ISAAC_USD_CONVERTER = (
    "a_forward line converter (tool/version not tracked by backfill_upstream)"
)

# mesh_up_axis / origin_convention: general geometric rule (round 3),
# replacing an earlier per-kind hardcode/branch (round 1: uniform "Y" for
# everything; round 2: rigid stayed "Y", articulated special-cased against
# the two named quaternion constants IDENTITY_WXYZ/X90_WXYZ). Neither
# survived contact with the real catalog: a sweep of ALL 18 usable models
# showed stable_orientation_wxyz is not uniform within EITHER kind (rigid:
# 11 IDENTITY / 3 X90 / 1 other; articulated: 2 IDENTITY / 1 X90 --
# 036_cabinet itself, the round-2 "precedent" asset, is the X90 outlier).
#
# The rule here doesn't special-case IDENTITY/X90 at all -- it rotates the
# mesh's own Y=(0,1,0) and Z=(0,0,1) axes by stable_orientation_wxyz and
# checks which image ends up closer to world +Z=(0,0,1) (the axis that
# "becomes up" once the object is placed in its stable resting pose is, by
# construction, the mesh's own native up-axis). IDENTITY -> Z's image IS
# +Z -> "Z"; X90 -> Y's image IS +Z -> "Y" fall out automatically as the
# two exact special cases. A model where neither image clears the cos(45
# deg) threshold is genuinely undetermined by this method and is EXCLUDED
# from ingestion (not defaulted) -- see notes.up_axis_ambiguous.
_UP_AXIS_DOT_THRESHOLD = math.cos(math.radians(45))  # ~0.70710678
_AXIS_ORIGIN = {"Y": "bottom-center", "Z": "base-at-floor"}


def _quat_rotate_y_and_z(q):
    """(R(q) @ (0,1,0), R(q) @ (0,0,1)) for unit quaternion q=(w,x,y,z),
    via the standard Hamilton unit-quaternion rotation matrix -- columns 1
    and 2 read off directly (pure stdlib arithmetic, no matrix/vector lib;
    the two lookups needed don't justify building the full 3x3 matrix)."""
    w, x, y, z = q
    img_y = (2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x))
    img_z = (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))
    return img_y, img_z


def _derive_up_axis_and_origin(model, report, note_key):
    """Returns (axis, origin_convention), or None if the orientation is
    malformed or genuinely ambiguous (caller must then exclude the model
    from ingestion and has already had the exclusion recorded here)."""
    q = model.get("stable_orientation_wxyz")
    if not q or len(q) != 4:
        report["notes"]["up_axis_ambiguous"].append(note_key)
        return None
    img_y, img_z = _quat_rotate_y_and_z(q)
    # dot with world +Z=(0,0,1) is just the z-component of the image.
    dot_y, dot_z = img_y[2], img_z[2]
    if dot_y < _UP_AXIS_DOT_THRESHOLD and dot_z < _UP_AXIS_DOT_THRESHOLD:
        report["notes"]["up_axis_ambiguous"].append(note_key)
        return None
    axis = "Y" if dot_y > dot_z else "Z"
    return axis, _AXIS_ORIGIN[axis]


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_file_mtime_date(dir_path):
    """Date of the most recently modified *file* under dir_path (recursive).
    None if the directory contains no files (caller falls back explicitly).
    Mirrors backfill_ledger_v1.py's _latest_file_mtime_date -- a directory's
    own mtime is not a proxy for the age of the files remaining inside it."""
    files = [p for p in dir_path.rglob("*") if p.is_file()]
    if not files:
        return None
    return datetime.date.fromtimestamp(
        max(p.stat().st_mtime for p in files)
    ).isoformat()


def _build_source_manifest(asset_dir):
    """{"prefix": <asset_dir relative to nothing in particular -- just the
    dir name for a human reading the file>, "files": {relpath: sha256}} for
    every file under asset_dir, sorted for determinism across reruns. Same
    shape as the existing pool manifests (see e.g.
    data/asset_library/_source/*/SOURCE_MANIFEST.json)."""
    files = {}
    for p in sorted(asset_dir.rglob("*")):
        if p.is_file() and p.name != "SOURCE_MANIFEST.json":
            files[p.relative_to(asset_dir).as_posix()] = _sha256_file(p)
    return {"prefix": asset_dir.name, "files": files}


def _relative_to_root(path_str, root):
    try:
        return Path(path_str).relative_to(root).as_posix()
    except ValueError:
        return path_str  # honest fallback: never fabricate a rebased path


def _derive_scale_applied(scale, report, note_key):
    """physical.scale_applied is a scalar (NOT_NULLABLE_MODEL forbids null),
    but the catalog's `scale` is a 3-tuple. When uniform that IS the scalar
    (same derivation as backfill_ledger_v1.py's _derive_scale_applied); when
    non-uniform there is no null-safe fallback (validator requires a
    non-null value here), so the first axis is used and the discrepancy is
    recorded in the report rather than silently dropped."""
    if not scale:
        return None
    if len(set(scale)) != 1:
        report["notes"]["non_uniform_scale"].append(note_key)
    return scale[0]


def _stable_poses(model):
    return [
        {
            "pose_id": model["stable_pose_id"],
            "orientation_wxyz": model["stable_orientation_wxyz"],
            "is_default": True,
        }
    ]


def _size_resolution(model, scale_applied):
    dims = model["dimensions_m"]
    return {
        "mode": "upstream_catalog",
        "actual_max_dim_m": max(dims),
        "scale": scale_applied,
        "reference_max_dim_m": None,
        "reference_assets": [],
        "verdict": "upstream_authored",
    }


def _conventions(model):
    return {
        "is_static": model["is_static"],
        "z_policy": model["z_policy"],
        "footprint_shape": model["footprint_shape"],
        "stable_poses": _stable_poses(model),
        "support_margin_m": model.get("support_margin_m"),
        "support_spawn_clearance_m": model.get("support_spawn_clearance_m"),
        "inherited_from": None,
    }


def _mass_override(kind):
    return {
        "value": None,
        "status": "unknown",
        "runtime_default_kg": 0.1,
        "runtime_default_basis": "urdf_inertial"
        if kind == "articulated"
        else "global_constant",
    }


def _rigid_representations(model):
    visual = Path(model["visual_path"])
    collision = Path(model["collision_path"])
    return [
        {
            "format": "glb",
            "uri": str(visual),
            "backend": "sapien",
            "role": "visual",
            "sha256": _sha256_file(visual),
            "size_bytes": visual.stat().st_size,
            "metadata": {},
        },
        {
            "format": "glb",
            "uri": str(collision),
            "backend": "sapien",
            "role": "collision",
            "sha256": _sha256_file(collision),
            "size_bytes": collision.stat().st_size,
            "metadata": {},
        },
    ]


def _articulated_representations(model):
    # visual_path/collision_path/urdf_path all point at the same
    # mobility.urdf for a urdf-load_type catalog entry -- one combined
    # sapien representation, matching the RoboTwin cabinet precedent in
    # scripts/a_forward/robotwin_asset.py (role="visual_and_collision").
    urdf = Path(model.get("urdf_path") or model["visual_path"])
    return [
        {
            "format": "urdf",
            "uri": str(urdf),
            "backend": "sapien",
            "role": "visual_and_collision",
            "sha256": _sha256_file(urdf),
            "size_bytes": urdf.stat().st_size,
            "metadata": {},
        }
    ]


def _articulation(model):
    joints = model.get("articulation_joints") or []
    return {
        "joint_names": [j["name"] for j in joints],
        "joint_types": [j.get("joint_type") for j in joints],
        "limits": [[j.get("lower"), j.get("upper")] for j in joints],
        "closed_qpos": model.get("articulation_closed_qpos") or [],
        "open_qpos": model.get("articulation_open_qpos") or [],
    }


def _isaac_representation(usd_path, derived_from):
    return {
        "format": "usd",
        "uri": str(usd_path),
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": _sha256_file(usd_path),
        "size_bytes": usd_path.stat().st_size,
        "metadata": {
            "derived_from": derived_from,
            "converter": ISAAC_USD_CONVERTER,
            "conversion_params": {},
        },
    }


def _existing_model(existing_ledger, model_id):
    if existing_ledger is None:
        return None
    for m in existing_ledger.get("models", []):
        if m.get("model_id") == model_id:
            return m
    return None


def _build_model_entry(
    entry,
    model,
    kind,
    retrieved_at,
    source_manifest_path,
    group,
    relbase,
    existing_model,
    isaac_usd_path,
    report,
    note_key,
    up_axis,
    origin_convention,
):
    scale_applied = _derive_scale_applied(model.get("scale"), report, note_key)

    representations = (
        _articulated_representations(model)
        if kind == "articulated"
        else _rigid_representations(model)
    )

    # Incremental layer: carry forward any previously-registered non-sapien
    # representation (e.g. an earlier --isaac-usd registration) untouched,
    # unless this run supplies a fresh --isaac-usd for this exact model, in
    # which case that one entry is upserted (replaced, not duplicated).
    preserved = [
        rp
        for rp in (existing_model or {}).get("representations", [])
        if rp.get("backend") != "sapien"
    ]
    if isaac_usd_path is not None:
        preserved = [rp for rp in preserved if rp.get("backend") != "isaacsim"]
        preserved.append(
            _isaac_representation(isaac_usd_path, representations[0]["uri"])
        )
        report["notes"]["isaac_usd_registered"].append(note_key)
    representations = representations + preserved

    source = {
        "library": SOURCE_LIBRARY,
        "group": group,
        "file": _relative_to_root(model["model_path"], relbase),
        "license": DEFAULT_LICENSE,
        "retrieved_at": retrieved_at,
        "source_manifest_path": str(source_manifest_path),
    }
    existing_license = (existing_model or {}).get("source", {}).get("license")
    if existing_license and existing_license.get("status") == "declared":
        source["license"] = existing_license

    verification = (existing_model or {}).get("verification", [])

    model_entry = ledger.new_model_entry(
        model=model["model_id"],
        representations=representations,
        mesh_bbox_m=model["dimensions_m"],
        mesh_up_axis=up_axis,
        origin_convention=origin_convention,
        scale_applied=scale_applied,
        size_resolution=_size_resolution(model, scale_applied),
        conventions=_conventions(model),
        source=source,
        verification=verification,
        articulation=_articulation(model) if kind == "articulated" else None,
        mass_override=_mass_override(kind),
    )
    return model_entry


def _parse_isaac_usd(raw_list):
    """--isaac-usd ASSET=PATH (repeatable) -> {asset: Path}. Every path is
    checked to exist right here, before any catalog processing starts, so a
    typo'd path fails fast rather than mid-run with a partially-applied
    backfill."""
    out = {}
    for raw in raw_list:
        asset, sep, path_str = raw.partition("=")
        if not sep or not path_str:
            raise ValueError(f"--isaac-usd must be ASSET=PATH, got {raw!r}")
        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(f"--isaac-usd {asset}: file not found: {path}")
        out[asset] = path
    return out


def _parse_root_remap(raw):
    """--root-remap OLD=NEW -> (old, new), or None if not given. Single
    rule only (the user's rsync target is one destination tree)."""
    if raw is None:
        return None
    old, sep, new = raw.partition("=")
    if not sep or not old or not new:
        raise ValueError(f"--root-remap must be OLD=NEW, got {raw!r}")
    return old, new


# Absolute path fields that may need remapping, before any existence check,
# sha256 computation, or uri write touches them. urdf_path is included even
# though the coordinator's field list didn't name it explicitly: it aliases
# visual_path/collision_path for a urdf-load_type entry (see
# _articulated_representations, which prefers it when present) -- remapping
# the other two but not this one would silently re-point the urdf
# representation at the stale, un-copied location.
_REMAP_FIELDS_ENTRY = ("asset_path",)
_REMAP_FIELDS_MODEL = (
    "model_path",
    "visual_path",
    "collision_path",
    "metadata_path",
    "urdf_path",
)


def _apply_root_remap(catalog, root_remap):
    """Rewrite every absolute path field under old_prefix to new_prefix,
    across every entry/model in catalog. Returns (catalog, hits) -- catalog
    is a deep copy (the loaded dict is never mutated in place), hits is the
    count of individual field replacements performed (for the report).
    catalog["robotwin_root"] is deliberately left untouched: the physical
    move here is only the assets/objects/ subtree (per the coordinator's
    rsync target data/robotwin_assets/objects/), not the whole external
    RoboTwin checkout, so robotwin_root genuinely still refers to the old
    location. _relative_to_root's caller in main() compensates by using
    new_prefix itself as the relative-path base when a remap is active,
    rather than trying to keep robotwin_root in sync with a subtree move it
    wasn't part of."""
    if root_remap is None:
        return catalog, 0
    old, new = root_remap
    catalog = json.loads(json.dumps(catalog))
    hits = 0
    for entry in catalog["entries"]:
        for field in _REMAP_FIELDS_ENTRY:
            value = entry.get(field)
            if isinstance(value, str) and value.startswith(old):
                entry[field] = new + value[len(old) :]
                hits += 1
        for model in entry.get("models", []):
            for field in _REMAP_FIELDS_MODEL:
                value = model.get(field)
                if isinstance(value, str) and value.startswith(old):
                    model[field] = new + value[len(old) :]
                    hits += 1
    return catalog, hits


def _empty_report():
    return {
        "written": [],
        "skipped_unusable": [],
        "aliases_defaulted": [],
        "violations": {},
        "notes": {
            "non_uniform_scale": [],
            "isaac_usd_registered": [],
            "up_axis_ambiguous": [],
            "root_remap": None,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--isaac-usd",
        action="append",
        default=[],
        metavar="ASSET=PATH",
        help="register an isaacsim USD representation for ASSET's first "
        "usable model (incremental layer). Repeatable.",
    )
    parser.add_argument(
        "--root-remap",
        default=None,
        metavar="OLD_PREFIX=NEW_PREFIX",
        help="rewrite absolute paths under OLD_PREFIX to NEW_PREFIX before "
        "any file-existence check / sha256 / uri write (e.g. the upstream "
        "checkout was rsync'd into this repo). Single rule.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    catalog = json.loads(Path(args.catalog).read_text())
    robotwin_root = Path(catalog["robotwin_root"])
    source_commit = catalog["source_commit"]
    out_dir = Path(args.out)

    try:
        isaac_usd_map = _parse_isaac_usd(args.isaac_usd)
        root_remap = _parse_root_remap(args.root_remap)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    catalog, root_remap_hits = _apply_root_remap(catalog, root_remap)
    # After a remap, model/asset paths point into the new (in-repo) tree,
    # which is not necessarily still under robotwin_root (only the
    # assets/objects/ subtree moved, not the whole external checkout) --
    # use the remap's own new_prefix as the relative-path base so
    # source.file stays a clean relative path instead of falling back to a
    # raw, host-specific absolute one (see _apply_root_remap's docstring).
    relbase = Path(root_remap[1]) if root_remap else robotwin_root

    entries_by_asset = {e["asset_id"]: e for e in catalog["entries"]}
    unknown_isaac_usd = set(isaac_usd_map) - set(entries_by_asset)
    if unknown_isaac_usd:
        print(
            f"ERROR: --isaac-usd asset(s) not found in catalog: {sorted(unknown_isaac_usd)}",
            file=sys.stderr,
        )
        sys.exit(2)

    report = _empty_report()

    for asset, entry in entries_by_asset.items():
        kind = "articulated" if entry.get("load_type") == "urdf" else "rigid"
        category = entry["category"]
        semantic_name = entry.get("semantic_name") or category
        aliases = list(entry.get("aliases") or [])
        if not aliases:
            aliases = [category]
            report["aliases_defaulted"].append(asset)
        colors = list(entry.get("colors") or [])
        materials = list(entry.get("materials") or [])
        tags = ["upstream", "robotwin", kind]

        asset_dir = Path(entry["asset_path"])
        retrieved_at = _latest_file_mtime_date(asset_dir)
        if retrieved_at is None:
            retrieved_at = datetime.date.fromtimestamp(
                asset_dir.stat().st_mtime
            ).isoformat()
            report["notes"].setdefault("retrieved_at_empty_asset_dir", []).append(asset)

        lp = ledger.ledger_path(out_dir, asset)
        existing_ledger = json.loads(lp.read_text()) if lp.exists() else None

        source_manifest_path = (out_dir / asset / "SOURCE_MANIFEST.json").resolve()

        usable_models = [m for m in entry["models"] if m.get("usable")]
        for m in entry["models"]:
            if not m.get("usable"):
                report["skipped_unusable"].append(f"{asset}:m{m['model_id']}")

        if not usable_models:
            continue

        # Geometric up_axis resolution happens before any file I/O (sha256,
        # existence checks) for a model: an ambiguous orientation excludes
        # the model from ingestion entirely (report-and-skip, not defaulted
        # -- see _derive_up_axis_and_origin), so there's no point hashing
        # files for a model that won't be written.
        resolved_models = []
        for m in usable_models:
            note_key = f"{asset}:m{m['model_id']}"
            axis_origin = _derive_up_axis_and_origin(m, report, note_key)
            if axis_origin is None:
                continue
            resolved_models.append((m, axis_origin[0], axis_origin[1]))

        if not resolved_models:
            continue

        first_usable_model_id = resolved_models[0][0]["model_id"]

        # upsert_model deep-copies existing_ledger internally and validates
        # asset-level fields match; start from None explicitly here (the
        # derived-core rebuild always regenerates asset-level fields fresh
        # from the catalog) and let each upsert_model call below re-attach
        # models[] one at a time.
        led = None
        for m, up_axis, origin_convention in resolved_models:
            note_key = f"{asset}:m{m['model_id']}"
            existing_model = _existing_model(existing_ledger, m["model_id"])
            isaac_usd_path = (
                isaac_usd_map[asset]
                if (asset in isaac_usd_map and m["model_id"] == first_usable_model_id)
                else None
            )
            model_entry = _build_model_entry(
                entry,
                m,
                kind,
                retrieved_at,
                source_manifest_path,
                source_commit,
                relbase,
                existing_model,
                isaac_usd_path,
                report,
                note_key,
                up_axis,
                origin_convention,
            )
            led = ledger.upsert_model(
                led,
                asset=asset,
                category=category,
                kind=kind,
                aliases=aliases,
                colors=colors,
                materials=materials,
                tags=tags,
                model_entry=model_entry,
                semantic_name=semantic_name,
                asset_id_prefix=ASSET_ID_PREFIX,
            )

        violations = ledger.validate_ledger(led, check_files=True)
        if violations:
            report["violations"][asset] = [
                {"path": v.path, "code": v.code, "message": v.message}
                for v in violations
            ]

        report["written"].append(asset)

        if args.apply:
            lp.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_ledger(lp, led)
            manifest = _build_source_manifest(asset_dir)
            source_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    if root_remap:
        report["notes"]["root_remap"] = {
            "old_prefix": root_remap[0],
            "new_prefix": root_remap[1],
            "hits": root_remap_hits,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill_upstream_report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
