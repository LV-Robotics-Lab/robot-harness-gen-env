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

Stdlib plus trimesh (for rigid mesh_up_axis/mesh_bbox_m measurement -- see
_measure_rigid_geometry); this script only ever runs under env-gen-yuxin
(which has trimesh installed), unlike lib/ledger.py, which stays pure
stdlib so both conda envs (isaac-smoke py3.11 / env-gen-yuxin py3.10) can
import it.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import trimesh

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

# mesh_up_axis / origin_convention (round 4, review fix-round-1 C1+C2):
# rounds 1-3 all inferred this from stable_orientation_wxyz in one way or
# another (uniform "Y"; kind-conditioned named-constant matching; a general
# quaternion/dot-product rule). All three were wrong for the same root
# reason, caught by an external review's per-asset mesh audit: of the 18
# usable models, only 5 have a genuinely asset-specific stable_orientation_wxyz
# override -- the rest are an unset library default that happens to read as
# a valid quaternion. Treating "nobody filled this in" as geometric evidence
# was the mistake; round 3's more mathematically careful treatment of that
# same bad signal made accuracy WORSE (14/18 -> 9/18 correct against
# ground truth), not better.
#
# Geometry and pose are now fully decoupled:
#   - mesh_up_axis is measured directly off the asset's own files (see
#     _measure_rigid_geometry for rigid; the fixed constant below for
#     articulated) -- a file-format fact, never inferred from a placement
#     quaternion.
#   - stable_orientation_wxyz stays exactly where it always was, feeding
#     ONLY physical.conventions.stable_poses (_stable_poses, unchanged
#     across every round) -- it's catalog-authored task/placement data, not
#     mesh-geometry evidence. 036_cabinet's X90 stable pose is real upstream
#     data and is kept verbatim in stable_poses; it no longer has any
#     bearing on mesh_up_axis, which resolves the round-2/3 apparent
#     contradiction (same asset, two "disagreeing" signals) by recognizing
#     the two signals were never answering the same question.
_AXIS_ORIGIN = {"Y": "bottom-center", "Z": "base-at-floor"}

# "Touches the floor" tolerance for _measure_rigid_geometry, relative to the
# mesh's own largest extent (not an absolute meter value -- raw mesh units
# aren't necessarily meters; catalog `scale` converts them later). Verified
# against the real catalog: every genuine floor-touching axis measures at
# effectively exact 0 (e.g. 004_fluted-block's Y-minimum is -1.8e-8 after
# its node-level transform is applied), while genuinely-ambiguous meshes
# (020_hammer, 034_knife: authored centroid-centered, confirmed by their
# min/(extent/2) ratio being ~1.0 -- i.e. symmetric about the origin -- on
# ALL THREE axes, not a near-miss on any one of them) aren't remotely close
# to this tolerance on any axis. 1e-3 has margin on both sides for every
# real asset measured; it is not a fitted/fragile threshold.
_FLOOR_REL_TOL = 1e-3

# Articulated (URDF/PartNet-Mobility): fixed, not measured. All 3 usable
# urdf assets' mobility.urdf share the exact same root fixed-joint
# transform (rpy="1.570796326794897 0 -1.570796326794897", connecting
# link "base" -> the rest of the kinematic tree) -- independently confirmed
# byte-for-byte identical across 015_laptop/036_cabinet/037_box, not just
# asserted. That's PartNet-Mobility's standard Z-up export convention,
# applied uniformly by construction (not something a per-model geometry
# check would add confidence to); it also matches the one pre-existing
# articulated ledger precedent in the pool (314_cabinet: Z + base-at-floor).
_ARTICULATED_AXIS = "Z"
_ARTICULATED_ORIGIN = _AXIS_ORIGIN[_ARTICULATED_AXIS]


def _measure_rigid_geometry(visual_path, report, note_key):
    """Load the visual mesh via trimesh (same call as the RoboTwin smoke
    precedent, scripts/a_forward/robotwin_asset.py's glb_bbox helper:
    trimesh.load(path).bounds) -- for a GLB/OBJ with a node hierarchy this
    is already computed with every node/scene transform applied, so e.g.
    004_fluted-block's node-level X+90 transform is baked into the
    measurement, not something this function has to special-case.
    RoboTwin's rigid assets rest with their bottom flush against their own
    local origin plane; the axis whose measured minimum sits at
    (approximately, see _FLOOR_REL_TOL) zero is that mesh's up axis.
    Returns (axis, origin_convention, extents_m) where extents_m is the
    SAME measurement's per-axis extent (max-min, mesh's own native/measured
    axis order -- not reordered to any canonical frame), unscaled; the
    caller multiplies by scale_applied. Using this measurement for both the
    axis call AND mesh_bbox_m (round 3's bug: mesh_bbox_m was catalog
    dimensions_m, a distinct annotation -- robotwin_asset.py's own
    docstring warns model_data "extents" can disagree with the actual mesh
    bbox) means the two can never silently disagree with each other.
    Returns None (report['notes']['up_axis_ambiguous'] populated) if the
    file fails to load, if zero or more-than-one axis is near-zero, or if
    the (unique) near-zero axis is X (index 0 -- not a representable Y|Z
    up_axis value): e.g. 020_hammer/034_knife, whose meshes are authored
    centroid-centered on every axis (see module-level comment)."""
    try:
        scene = trimesh.load(str(visual_path))
        lo, hi = scene.bounds
    except Exception:
        report["notes"]["up_axis_ambiguous"].append(note_key)
        return None
    extents = [float(h - l) for l, h in zip(lo, hi)]
    max_extent = max(extents) or 1.0
    near_zero = [i for i in range(3) if abs(lo[i]) <= _FLOOR_REL_TOL * max_extent]
    axis = {1: "Y", 2: "Z"}.get(near_zero[0]) if len(near_zero) == 1 else None
    if axis is None:
        report["notes"]["up_axis_ambiguous"].append(note_key)
        return None
    return axis, _AXIS_ORIGIN[axis], extents


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _format_from_uri(path):
    """representations[].format from the file's own suffix (review fix C3)
    -- rounds 1-3 hardcoded "glb" for every rigid representation, which was
    silently wrong for the four 900_* series assets (900_gen_block_2057baba
    etc.), whose visual/collision files are .obj, not .glb."""
    return Path(path).suffix.lstrip(".").lower()


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


def _has_isaac_rep(led, model_entry):
    """True iff every model this ledger will hold owns a non-snapshot
    isaacsim representation -- the same rule migrate_ledger_v2 applied, kept
    identical so a rerun of this backfill cannot silently reclassify."""
    models = list((led or {}).get("models") or [])
    models = [m for m in models if m.get("model_id") != model_entry.get("model_id")]
    models.append(model_entry)
    return bool(models) and all(
        any(
            r.get("backend") == "isaacsim" and r.get("role") != "snapshot"
            for r in m.get("representations") or []
        )
        for m in models
    )


def _size_resolution(mesh_bbox_m, scale_applied):
    # Takes the already-resolved mesh_bbox_m (round 4: trimesh-measured x
    # scale for rigid, catalog dimensions_m for articulated -- see
    # _resolve_models) rather than re-deriving it from the catalog model dict.
    #
    # v2: actual_max_dim_m is the PRE-scale reading, matching what
    # conventions.resolve_size has always produced and what the validator's
    # size identity (max(mesh_bbox_m) == actual_max_dim_m * scale) now
    # enforces. This used to write max(mesh_bbox_m) -- the POST-scale number
    # under a pre-scale field name -- which made the same field mean two
    # different things depending on which writer produced the ledger.
    scale = scale_applied if scale_applied else 1.0
    return {
        "mode": "upstream_catalog",
        "actual_max_dim_m": max(mesh_bbox_m) / scale,
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
            "format": _format_from_uri(visual),
            "uri": str(visual),
            "backend": "sapien",
            "role": "visual",
            "sha256": _sha256_file(visual),
            "size_bytes": visual.stat().st_size,
            "metadata": {},
        },
        {
            "format": _format_from_uri(collision),
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
            "format": _format_from_uri(urdf),
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
        "format": _format_from_uri(usd_path),
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
    mesh_bbox_m,
    scale_applied,
):
    # up_axis/origin_convention/mesh_bbox_m/scale_applied are all resolved
    # once by _resolve_models (before any of this asset's models reach here)
    # -- not recomputed per call, so _derive_scale_applied's report side
    # effect (notes.non_uniform_scale) fires exactly once per model.
    representations = (
        _articulated_representations(model)
        if kind == "articulated"
        else _rigid_representations(model)
    )
    # v3: the measured up-axis/origin describe the FILES this backfill just
    # measured, so they live on those representations (frame/geometry_state),
    # not as per-model fields -- the same model's other-backend files can and
    # do disagree (a Y-up baked GLB next to a Z-up unbaked USD).
    for rp in representations:
        rp.setdefault("frame", {})["up_axis"] = up_axis
        gs = rp.setdefault("geometry_state", {})
        gs.setdefault("origin", origin_convention)
        # upstream RoboTwin files are loaded WITH model_data scale at
        # create_actor time -- the file itself does not carry it
        gs.setdefault("scale_baked", False)

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
        # v2: which of the two ways this model came to exist. Everything
        # this backfill sees already existed in the RoboTwin library, so it
        # is retrieved by definition -- nothing here was generated.
        "kind": "retrieved",
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
        mesh_bbox_m=mesh_bbox_m,
        mesh_up_axis=up_axis,
        origin_convention=origin_convention,
        size_resolution=_size_resolution(mesh_bbox_m, scale_applied),
        conventions=_conventions(model),
        source=source,
        verification=verification,
        articulation=_articulation(model) if kind == "articulated" else None,
        mass_override=_mass_override(kind),
        # Always recorded, whatever the profile turns out to be: under
        # cross_backend the validator requires the key, and under sapien_only
        # a structured unknown costs nothing and still says something true --
        # that no asset-side measurement exists and where the engine gets it.
        inertial=ledger.unknown_inertial(
            "urdf_inertial" if kind == "articulated" else "engine_derived"
        ),
    )
    return model_entry


def _resolve_models(entry, kind, report):
    """usable-filter (unchanged since round 1: usable:false models are
    skipped, recorded in report['skipped_unusable']) + per-model geometry
    resolution (round 4: rigid measured via trimesh, articulated fixed to
    the verified PartNet-Mobility convention -- see module-level comments
    above _measure_rigid_geometry). Runs once per entry, before any output
    file is touched for that entry, so:
      - notes.non_uniform_scale / notes.up_axis_ambiguous are each
        populated exactly once per model (not recomputed later);
      - main() can validate --isaac-usd targets (I1) against the same
        resolved set used for writing, before any file gets written.
    Returns [(model, up_axis, origin_convention, mesh_bbox_m, scale_applied),
    ...] -- only for models that are BOTH usable:true AND resolved to a
    concrete up_axis (an ambiguous or unmeasurable rigid model is excluded
    here, not defaulted)."""
    asset = entry["asset_id"]
    usable_models = [m for m in entry["models"] if m.get("usable")]
    for m in entry["models"]:
        if not m.get("usable"):
            report["skipped_unusable"].append(f"{asset}:m{m['model_id']}")

    resolved = []
    for m in usable_models:
        note_key = f"{asset}:m{m['model_id']}"
        scale_applied = _derive_scale_applied(m.get("scale"), report, note_key)
        if kind == "articulated":
            resolved.append(
                (
                    m,
                    _ARTICULATED_AXIS,
                    _ARTICULATED_ORIGIN,
                    m["dimensions_m"],
                    scale_applied,
                )
            )
            continue
        measured = _measure_rigid_geometry(Path(m["visual_path"]), report, note_key)
        if measured is None:
            continue
        axis, origin_convention, extents = measured
        mesh_bbox_m = [
            e * (scale_applied if scale_applied is not None else 1.0) for e in extents
        ]
        resolved.append((m, axis, origin_convention, mesh_bbox_m, scale_applied))
    return resolved


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

    # Phase 1: usable-filter + geometry/up_axis resolution for every entry,
    # before any output file is touched. This lets --isaac-usd targets be
    # validated (I1, below) against the exact same resolved set the write
    # phase will use -- an asset present in the catalog but with zero
    # ingestible models after resolution (e.g. its only usable model's
    # up_axis is ambiguous) can only be known after resolution runs, not
    # from catalog presence alone.
    resolved_by_asset = {}
    for asset, entry in entries_by_asset.items():
        kind = "articulated" if entry.get("load_type") == "urdf" else "rigid"
        resolved_by_asset[asset] = (kind, _resolve_models(entry, kind, report))

    unresolvable_isaac_usd = {
        asset for asset in isaac_usd_map if not resolved_by_asset[asset][1]
    }
    if unresolvable_isaac_usd:
        print(
            "ERROR: --isaac-usd asset(s) have no ingestible model (all "
            f"unusable or up_axis-ambiguous): {sorted(unresolvable_isaac_usd)}",
            file=sys.stderr,
        )
        sys.exit(2)

    # Phase 2: build + (if --apply) write one ledger per asset that has at
    # least one resolved model, reusing phase 1's resolution unchanged.
    for asset, entry in entries_by_asset.items():
        kind, resolved_models = resolved_by_asset[asset]
        if not resolved_models:
            continue

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

        first_usable_model_id = resolved_models[0][0]["model_id"]

        # upsert_model deep-copies existing_ledger internally and validates
        # asset-level fields match; start from None explicitly here (the
        # derived-core rebuild always regenerates asset-level fields fresh
        # from the catalog) and let each upsert_model call below re-attach
        # models[] one at a time.
        led = None
        for (
            m,
            up_axis,
            origin_convention,
            mesh_bbox_m,
            scale_applied,
        ) in resolved_models:
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
                mesh_bbox_m,
                scale_applied,
            )
            led = ledger.upsert_model(
                led,
                asset=asset,
                category=category,
                kind=kind,
                # Upstream RoboTwin assets carry no isaacsim representation
                # unless one was registered by hand via --isaac-usd; the
                # profile follows that evidence rather than an aspiration.
                profile=(
                    "cross_backend" if _has_isaac_rep(led, model_entry) else "sapien_only"
                ),
                identity={
                    "basis": "upstream_catalog",
                    "evidence": str(args.catalog),
                    "verified": False,
                },
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
