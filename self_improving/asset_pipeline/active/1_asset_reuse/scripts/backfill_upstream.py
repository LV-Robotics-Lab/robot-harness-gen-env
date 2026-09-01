#!/usr/bin/env python3
"""Backfill: map upstream RoboTwin asset_catalog.json entries into per-asset
v3 ledgers under --out (data/upstream_ledgers/<asset>/ledger.json).

Architecture (spec §9, docs/2026-08-08-asset-ingest-metadata-contract-design.md):
  derived core   -- identity/semantics/geometry/placement mapped straight off
                     the upstream catalog entry. Owned by the catalog; every
                     rerun refreshes these fields, except measurement receipts:
                     a catalog declaration is not a simulator measurement and
                     a newer existing stable-pose receipt is preserved.
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
import base64
import datetime
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger, ledger_writes, writer_paths

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
ISAAC_USD_CONVERTER = "a_forward line converter (tool/version not tracked by backfill_upstream)"

_PAIR_TRANSACTION_JOURNAL_NAME = ".SOURCE_MANIFEST.ledger.transaction.json"
_PAIR_TRANSACTION_SCHEMA = "asset_manifest_ledger_transaction.v1"
_PAIR_TRANSACTION_TOMBSTONE_SCHEMA = "asset_manifest_ledger_transaction_pending.v1"


class PairTransactionRecoveryError(RuntimeError):
    """A durable manifest/ledger transaction cannot be recovered safely."""


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
#     ONLY physical.conventions.stable_poses -- it's catalog-authored
#     task/placement data, not mesh-geometry evidence. Its measured_against
#     block is copied only from an explicit measurement handle or a matching
#     existing real receipt; source_commit is never disguised as a run id.
#     036_cabinet's X90 stable pose is kept verbatim and no longer has any
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
    extents = [float(high - low) for low, high in zip(lo, hi)]
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
    return datetime.date.fromtimestamp(max(p.stat().st_mtime for p in files)).isoformat()


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
        # 不在 root 下（如 ext 模型住 asset_library）→ 走可移植契约兜底：
        # active 树内给 active-relative，树外才保留绝对路径。此前直接回落
        # 绝对路径，把 ~400 条个人 home 路径写进了 public 仓的 tracked
        # 账本（2026-08-20 路径卫生排查）。
        return ledger.to_portable_uri(path_str)


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


def _existing_pose_measurement(model, existing_model, representations, asset_key):
    """Return only a pose handle backed by a current settle/pass receipt.

    Catalog ``run_id`` fields are declarations, not replay evidence.  A
    backfill rerun may preserve a prior handle only when the same existing
    model also carries the exact current v2 representation-set digest; the
    receipt is copied byte-for-byte and never re-signed here.
    """
    if not isinstance(existing_model, dict):
        return None
    expected_digest = ledger.reps_digest({"representations": representations}, "sapien")
    latest = ledger.latest_trusted_verification(existing_model, "sapien", "settle", asset_key)
    if (
        latest is None
        or latest.get("verdict") != "pass"
        or latest.get("verified_digest") != expected_digest
    ):
        return None
    conventions = (existing_model.get("physical") or {}).get("conventions") or {}
    for pose in conventions.get("stable_poses") or []:
        if not isinstance(pose, dict):
            continue
        if pose.get("pose_id") != model.get("stable_pose_id"):
            continue
        if pose.get("orientation_wxyz") != model.get("stable_orientation_wxyz"):
            continue
        provenance = pose.get("measured_against")
        if not isinstance(provenance, dict):
            continue
        backend = provenance.get("backend")
        run_id = provenance.get("run_id")
        if (
            backend == "sapien"
            and latest.get("run_id") == run_id
            and isinstance(run_id, str)
            and bool(run_id.strip())
        ):
            return json.loads(json.dumps(provenance))
    return None


def _stable_poses(model, existing_model, representations, asset_key):
    pose = {
        "pose_id": model["stable_pose_id"],
        "orientation_wxyz": model["stable_orientation_wxyz"],
        "is_default": True,
    }
    provenance = _existing_pose_measurement(model, existing_model, representations, asset_key)
    if provenance is not None:
        pose["measured_against"] = provenance
    return [pose]


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


def _conventions(model, existing_model, representations, asset_key):
    return {
        "is_static": model["is_static"],
        "z_policy": model["z_policy"],
        "footprint_shape": model["footprint_shape"],
        "stable_poses": _stable_poses(model, existing_model, representations, asset_key),
        "support_margin_m": model.get("support_margin_m"),
        "support_spawn_clearance_m": model.get("support_spawn_clearance_m"),
        "inherited_from": None,
    }


def _mass_override(kind):
    del kind
    return {"value": None, "status": "unknown"}


def _file_record(path, uri=None):
    path = Path(path)
    return {
        "uri": uri if uri is not None else ledger.to_portable_uri(path),
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _representation_files(primary):
    """Record exactly the recursive files reachable from one loader primary."""

    return ledger_writes.representation_files(primary)


def _rigid_representations(model):
    visual = Path(model["visual_path"])
    collision = Path(model["collision_path"])
    return [
        {
            "format": _format_from_uri(visual),
            "uri": ledger.to_portable_uri(visual),
            "backend": "sapien",
            "role": "visual",
            "sha256": _sha256_file(visual),
            "files": _representation_files(visual),
            "metadata": {},
        },
        {
            "format": _format_from_uri(collision),
            "uri": ledger.to_portable_uri(collision),
            "backend": "sapien",
            "role": "collision",
            "sha256": _sha256_file(collision),
            "files": _representation_files(collision),
            # The file is explicitly selected as the collision mesh.  Whether
            # its faces form a convex body is a geometric measurement this
            # catalog backfill did not perform, so do not invent that fact.
            "collision_meta": {"mode": "explicit_mesh"},
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
            "uri": ledger.to_portable_uri(urdf),
            "backend": "sapien",
            "role": "visual_and_collision",
            "sha256": _sha256_file(urdf),
            "files": _representation_files(urdf),
            "collision_meta": {
                "mode": "unknown",
                "unknown_reason": "upstream URDF collision authoring was not probed",
            },
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
        "uri": ledger.to_portable_uri(usd_path),
        "backend": "isaacsim",
        "role": "visual_and_collision",
        "sha256": _sha256_file(usd_path),
        "files": _representation_files(usd_path),
        "collision_meta": {
            "mode": "unknown",
            "unknown_reason": "registered USD collision authoring was not probed",
        },
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


def _normalize_preserved_representation(representation):
    """Upgrade an incremental-layer representation without inventing facts."""
    representation = json.loads(json.dumps(representation))
    representation.pop("size_bytes", None)
    if not representation.get("files"):
        uri = representation.get("uri")
        sha = representation.get("sha256")
        path = ledger.resolve_uri(uri) if isinstance(uri, str) and uri else None
        if path is not None and path.is_file() and _sha256_file(path) == sha:
            representation["files"] = [_file_record(path, uri=uri)]
    files = representation.get("files")
    if isinstance(files, list) and all(
        isinstance(member, dict) and isinstance(member.get("uri"), str) for member in files
    ):
        representation["files"] = sorted(files, key=lambda member: member["uri"])
    role = representation.get("role")
    if role in ("collision", "visual_and_collision") and not representation.get("collision_meta"):
        representation["collision_meta"] = {
            "mode": "unknown",
            "unknown_reason": "legacy representation carried no collision provenance",
        }
    return representation


def _build_model_entry(
    entry,
    model,
    kind,
    retrieved_at,
    source_manifest_path,
    source_manifest_sha256,
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
        _normalize_preserved_representation(rp)
        for rp in (existing_model or {}).get("representations", [])
        if rp.get("backend") != "sapien"
    ]
    if isaac_usd_path is not None:
        preserved = [rp for rp in preserved if rp.get("backend") != "isaacsim"]
        preserved.append(_isaac_representation(isaac_usd_path, representations[0]["uri"]))
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
        "source_manifest_path": ledger.to_portable_uri(source_manifest_path),
        "source_manifest_sha256": source_manifest_sha256,
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
        conventions=_conventions(model, existing_model, representations, entry["asset_id"]),
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
        mesh_bbox_m = [e * (scale_applied if scale_applied is not None else 1.0) for e in extents]
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
        if not sep or not asset or not path_str:
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


def _require_safe_asset_id(value):
    try:
        return ledger.canonical_asset_key(value)
    except ledger.UnsafeAssetKeyError as exc:
        raise ValueError(f"unsafe asset_id: {value!r}") from exc


def _entries_by_asset(catalog, *, objects_root=None):
    entries = catalog.get("entries") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        raise ValueError("catalog entries must be a list")
    indexed = {}
    seen_models = set()
    objects_root = Path(objects_root or str(catalog.get("objects_root"))).absolute()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("catalog entry must be an object")
        asset = _require_safe_asset_id(entry.get("asset_id"))
        if asset in indexed:
            raise ValueError(f"catalog duplicates asset_id: {asset}")
        models = entry.get("models")
        if not isinstance(models, list):
            raise ValueError(f"catalog asset {asset} models must be a list")
        try:
            for model in models:
                if not isinstance(model, dict):
                    raise ledger.InvalidModelIdError("model entry must be an object")
                ledger.claim_asset_model(seen_models, asset, model.get("model_id"))
        except (ledger.InvalidModelIdError, ledger.DuplicateAssetModelError) as exc:
            raise ValueError(f"catalog asset {asset} has duplicate or malformed model_id") from exc
        asset_root = Path(str(entry.get("asset_path"))).absolute()
        expected_asset_root = (objects_root / asset).absolute()
        if not asset_root.is_relative_to(expected_asset_root):
            raise ValueError(f"catalog asset_path escapes objects_root asset key: {asset}")
        writer_paths.contained_path(objects_root, asset_root)
        for model in models:
            for field in _REMAP_FIELDS_MODEL:
                value = model.get(field)
                if value is None:
                    continue
                path = Path(str(value)).absolute()
                try:
                    writer_paths.contained_path(asset_root, path)
                except writer_paths.UnsafeWriterPathError:
                    raise ValueError(f"catalog {asset} {field} escapes asset_path")
        indexed[asset] = entry
    return indexed


def _read_bytes_at(directory_fd, name):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            return stream.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _atomic_write_bytes_at(directory_fd, name, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    temporary = None
    fd = -1
    try:
        for _attempt in range(100):
            candidate = f"{name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(candidate, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                continue
            temporary = candidate
            break
        else:
            raise FileExistsError("could not allocate a unique manifest temporary file")
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _atomic_write_bytes(path, payload, *, locked=None):
    path = Path(path)
    if locked is not None:
        _atomic_write_bytes_at(locked.directory_fd, locked.ledger_name, payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with ledger._open_parent_directory(path) as (directory_fd, name):
        _atomic_write_bytes_at(directory_fd, name, payload)


def _restore_bytes_at(directory_fd, name, previous):
    if previous is None:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            return
        os.fsync(directory_fd)
    else:
        _atomic_write_bytes_at(directory_fd, name, previous)


def _require_pinned_asset_directory(path, directory_fd):
    try:
        current = os.stat(Path(path).parent, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise OSError("asset directory changed during manifest/ledger commit") from exc
    pinned = os.fstat(directory_fd)
    if current.st_dev != pinned.st_dev or current.st_ino != pinned.st_ino:
        raise OSError("asset directory changed during manifest/ledger commit")


def _unlink_at(directory_fd, name):
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    os.fsync(directory_fd)


def _json_bytes(document):
    return (json.dumps(document, indent=2) + "\n").encode()


def _encode_optional_bytes(payload):
    if payload is None:
        return None
    return base64.b64encode(payload).decode("ascii")


def _decode_optional_bytes(payload, field):
    if payload is None:
        return None
    if not isinstance(payload, str):
        raise PairTransactionRecoveryError(f"transaction journal {field} is malformed")
    try:
        return base64.b64decode(payload.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise PairTransactionRecoveryError(f"transaction journal {field} is malformed") from exc


def _transaction_journal(prior_manifest, prior_ledger, manifest_bytes, document):
    body = {
        "schema": _PAIR_TRANSACTION_SCHEMA,
        "prior_manifest_b64": _encode_optional_bytes(prior_manifest),
        "prior_ledger_b64": _encode_optional_bytes(prior_ledger),
        "next_manifest_b64": _encode_optional_bytes(manifest_bytes),
        "next_ledger": document,
    }
    transaction_id = hashlib.sha256(ledger.canonical_json_bytes(body)).hexdigest()
    return {**body, "transaction_id": transaction_id}


def _parse_transaction_journal(payload):
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairTransactionRecoveryError("transaction journal is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("schema") != _PAIR_TRANSACTION_SCHEMA:
        raise PairTransactionRecoveryError("transaction journal has an unsupported schema")
    transaction_id = document.get("transaction_id")
    if not isinstance(transaction_id, str):
        raise PairTransactionRecoveryError("transaction journal has no transaction id")
    body = {key: value for key, value in document.items() if key != "transaction_id"}
    if transaction_id != hashlib.sha256(ledger.canonical_json_bytes(body)).hexdigest():
        raise PairTransactionRecoveryError("transaction journal checksum does not match")
    if not isinstance(document.get("next_ledger"), dict):
        raise PairTransactionRecoveryError("transaction journal next ledger is malformed")
    prior_manifest = _decode_optional_bytes(
        document.get("prior_manifest_b64"), "prior_manifest_b64"
    )
    prior_ledger = _decode_optional_bytes(document.get("prior_ledger_b64"), "prior_ledger_b64")
    next_manifest = _decode_optional_bytes(document.get("next_manifest_b64"), "next_manifest_b64")
    if next_manifest is None:
        raise PairTransactionRecoveryError("transaction journal next manifest is missing")
    return {
        "transaction_id": transaction_id,
        "prior_manifest": prior_manifest,
        "prior_ledger": prior_ledger,
        "next_manifest": next_manifest,
        "next_ledger": document["next_ledger"],
    }


def _read_json_if_possible(payload):
    if payload is None:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return object()
    return value


def _transaction_tombstone(transaction_id):
    return {
        "schema": _PAIR_TRANSACTION_TOMBSTONE_SCHEMA,
        "transaction_id": transaction_id,
    }


def _transaction_state(manifest_locked, ledger_locked, transaction):
    manifest_bytes = _read_bytes_at(manifest_locked.directory_fd, manifest_locked.ledger_name)
    ledger_bytes = _read_bytes_at(ledger_locked.directory_fd, ledger_locked.ledger_name)
    ledger_document = _read_json_if_possible(ledger_bytes)
    return manifest_bytes, ledger_bytes, ledger_document


def _restore_prior_pair(manifest_locked, ledger_locked, transaction):
    _restore_bytes_at(
        manifest_locked.directory_fd,
        manifest_locked.ledger_name,
        transaction["prior_manifest"],
    )
    _restore_bytes_at(
        ledger_locked.directory_fd,
        ledger_locked.ledger_name,
        transaction["prior_ledger"],
    )
    _unlink_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)


def _rollback_pair_transaction(manifest_locked, ledger_locked, transaction):
    manifest_bytes, ledger_bytes, ledger_document = _transaction_state(
        manifest_locked, ledger_locked, transaction
    )
    recognized_manifest = manifest_bytes in {
        transaction["prior_manifest"],
        transaction["next_manifest"],
    }
    prior_ledger_matches = ledger_bytes == transaction["prior_ledger"]
    recognized_ledger = (
        prior_ledger_matches
        or ledger_document == transaction["next_ledger"]
        or ledger_document == _transaction_tombstone(transaction["transaction_id"])
    )
    if not recognized_manifest or not recognized_ledger:
        raise PairTransactionRecoveryError(
            "manifest/ledger changed outside the locked transaction; refusing rollback"
        )
    _restore_prior_pair(manifest_locked, ledger_locked, transaction)


def _recover_pair_transaction(manifest_locked, ledger_locked):
    journal_bytes = _read_bytes_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)
    if journal_bytes is None:
        return
    transaction = _parse_transaction_journal(journal_bytes)
    manifest_bytes, ledger_bytes, ledger_document = _transaction_state(
        manifest_locked, ledger_locked, transaction
    )
    old_manifest = manifest_bytes == transaction["prior_manifest"]
    new_manifest = manifest_bytes == transaction["next_manifest"]
    old_ledger = ledger_bytes == transaction["prior_ledger"]
    new_ledger = ledger_document == transaction["next_ledger"]
    tombstone = ledger_document == _transaction_tombstone(transaction["transaction_id"])

    if new_manifest and new_ledger:
        _unlink_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)
        return
    if old_manifest and old_ledger:
        _unlink_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)
        return
    if tombstone and (old_manifest or new_manifest):
        _restore_prior_pair(manifest_locked, ledger_locked, transaction)
        return
    if new_ledger and old_manifest:
        _atomic_write_bytes_at(
            manifest_locked.directory_fd,
            manifest_locked.ledger_name,
            transaction["next_manifest"],
        )
        _unlink_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)
        return
    if old_ledger and new_manifest:
        _restore_prior_pair(manifest_locked, ledger_locked, transaction)
        return
    raise PairTransactionRecoveryError(
        "transaction journal does not match the current manifest/ledger pair"
    )


def _commit_manifest_and_ledger(manifest_path, manifest_bytes, ledger_path, document, *, expected):
    """Durably CAS one source-manifest/ledger pair in a pinned asset directory.

    A checksummed journal preserves both prior files.  The ledger becomes an
    identity-invalid tombstone before the manifest changes, so a process death
    cannot expose an old ledger beside a new manifest as an accepted asset.
    The next pair writer rolls an incomplete transaction back, or recognizes a
    fully published pair and only removes its leftover journal.  Pair and
    ledger locks stay held throughout normal rollback, preventing restoration
    from overwriting a cooperative receipt writer.
    """

    manifest_path = Path(manifest_path)
    ledger_path = Path(ledger_path)
    with ledger._locked_ledger(manifest_path) as manifest_locked:
        _require_pinned_asset_directory(manifest_path, manifest_locked.directory_fd)
        with ledger._locked_ledger_at(manifest_locked.directory_fd) as ledger_locked:
            _require_pinned_asset_directory(manifest_path, manifest_locked.directory_fd)
            _recover_pair_transaction(manifest_locked, ledger_locked)
            prior_manifest = _read_bytes_at(
                manifest_locked.directory_fd, manifest_locked.ledger_name
            )
            prior_ledger = _read_bytes_at(ledger_locked.directory_fd, ledger_locked.ledger_name)
            ledger_writes.validate_for_write(document)
            if prior_ledger is None:
                if expected is not None:
                    raise ledger_writes.ConcurrentLedgerUpdateError(
                        "expected ledger disappeared before validated write"
                    )
            else:
                if expected is None:
                    raise ledger_writes.ConcurrentLedgerUpdateError(
                        "replacing an existing ledger requires the expected prior document"
                    )
                try:
                    current = json.loads(prior_ledger)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ledger_writes.ConcurrentLedgerUpdateError(
                        "ledger changed before validated write"
                    ) from exc
                if current != expected:
                    raise ledger_writes.ConcurrentLedgerUpdateError(
                        "ledger changed before validated write"
                    )
            transaction_document = _transaction_journal(
                prior_manifest,
                prior_ledger,
                manifest_bytes,
                document,
            )
            transaction = _parse_transaction_journal(_json_bytes(transaction_document))
            _atomic_write_bytes_at(
                manifest_locked.directory_fd,
                _PAIR_TRANSACTION_JOURNAL_NAME,
                _json_bytes(transaction_document),
            )
            try:
                ledger._atomic_write_json(
                    ledger_path,
                    _transaction_tombstone(transaction["transaction_id"]),
                    locked=ledger_locked,
                )
                _atomic_write_bytes(
                    manifest_path,
                    manifest_bytes,
                    locked=manifest_locked,
                )
                ledger._atomic_write_json(
                    ledger_path,
                    document,
                    locked=ledger_locked,
                    pre_replace=lambda: ledger_writes.validate_for_write(document),
                )
                _require_pinned_asset_directory(manifest_path, manifest_locked.directory_fd)
            except Exception:
                _rollback_pair_transaction(manifest_locked, ledger_locked, transaction)
                raise
            _unlink_at(manifest_locked.directory_fd, _PAIR_TRANSACTION_JOURNAL_NAME)
            _require_pinned_asset_directory(manifest_path, manifest_locked.directory_fd)


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

    try:
        entries_by_asset = _entries_by_asset(
            catalog,
            objects_root=(root_remap[1] if root_remap else None),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
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

    unresolvable_isaac_usd = {asset for asset in isaac_usd_map if not resolved_by_asset[asset][1]}
    if unresolvable_isaac_usd:
        print(
            "ERROR: --isaac-usd asset(s) have no ingestible model (all "
            f"unusable or up_axis-ambiguous): {sorted(unresolvable_isaac_usd)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.apply:
        out_dir.mkdir(parents=True, exist_ok=True)

    # Phase 2: build + (if --apply) write one ledger per asset that has at
    # least one resolved model, reusing phase 1's resolution unchanged.
    for asset, entry in entries_by_asset.items():
        kind, resolved_models = resolved_by_asset[asset]
        if not resolved_models:
            continue

        category = entry["category"]
        aliases = list(entry.get("aliases") or [])
        if not aliases:
            aliases = [category]
            report["aliases_defaulted"].append(asset)
        colors = list(entry.get("colors") or [])
        materials = list(entry.get("materials") or [])

        asset_dir = Path(entry["asset_path"])
        retrieved_at = _latest_file_mtime_date(asset_dir)
        if retrieved_at is None:
            retrieved_at = datetime.date.fromtimestamp(asset_dir.stat().st_mtime).isoformat()
            report["notes"].setdefault("retrieved_at_empty_asset_dir", []).append(asset)

        try:
            loaded = ledger.load_asset_ledger(out_dir, asset)
        except FileNotFoundError:
            loaded = None
            lp = ledger.ledger_path(out_dir, asset)
            existing_ledger = None
            output_asset_dir = lp.parent
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report["violations"][asset] = [
                {
                    "path": "external_ids.env_gen",
                    "code": "unsafe_asset_location",
                    "message": str(exc),
                }
            ]
            continue
        else:
            lp = loaded.ledger_path
            existing_ledger = loaded.document
            output_asset_dir = loaded.asset_dir

        source_manifest_path = (output_asset_dir / "SOURCE_MANIFEST.json").resolve()
        source_manifest = _build_source_manifest(asset_dir)
        source_manifest_bytes = (json.dumps(source_manifest, indent=2) + "\n").encode()
        source_manifest_sha256 = hashlib.sha256(source_manifest_bytes).hexdigest()

        first_usable_model_id = resolved_models[0][0]["model_id"]

        # upsert_model deep-copies existing_ledger internally and validates
        # asset-level fields match; start from None explicitly here (the
        # derived-core rebuild always regenerates asset-level fields fresh
        # from the catalog) and let each upsert_model call below re-attach
        # models[] one at a time.
        model_entries = []
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
                source_manifest_sha256,
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
            model_entries.append(model_entry)

        # Profile is one asset-level promise.  Decide it once from the final
        # complete model set; computing it incrementally makes a two-model
        # asset oscillate between cross_backend and sapien_only and causes the
        # upsert contract to crash instead of returning typed evidence.
        profile = (
            "cross_backend"
            if model_entries
            and all(
                any(
                    representation.get("backend") == "isaacsim"
                    and representation.get("role") != "snapshot"
                    for representation in model_entry.get("representations", [])
                )
                for model_entry in model_entries
            )
            else "sapien_only"
        )
        led = None
        for model_entry in model_entries:
            led = ledger.upsert_model(
                led,
                asset=asset,
                category=category,
                kind=kind,
                # Upstream RoboTwin assets carry no isaacsim representation
                # unless one was registered by hand via --isaac-usd; the
                # profile follows that evidence rather than an aspiration.
                profile=profile,
                identity={
                    "basis": "upstream_catalog",
                    "evidence": ledger.to_portable_uri(args.catalog),
                    "verified": False,
                },
                aliases=aliases,
                colors=colors,
                materials=materials,
                tags=(),
                model_entry=model_entry,
                asset_id_prefix=ASSET_ID_PREFIX,
            )

        violations = ledger.validate_ledger(led, check_files=True)
        if violations:
            report["violations"][asset] = [
                {"path": v.path, "code": v.code, "message": v.message} for v in violations
            ]
            # Validation is the admission gate, not report-only telemetry.
            # Never replace a previously valid ledger with a known-invalid
            # candidate merely because --apply was supplied.
            continue

        report["written"].append(asset)

        if args.apply:
            output_asset_dir = ledger.ensure_asset_directory(out_dir, asset)
            lp = output_asset_dir / "ledger.json"
            source_manifest_path = output_asset_dir / "SOURCE_MANIFEST.json"
            _commit_manifest_and_ledger(
                source_manifest_path,
                source_manifest_bytes,
                lp,
                led,
                expected=existing_ledger,
            )

    if root_remap:
        report["notes"]["root_remap"] = {
            "old_prefix": root_remap[0],
            "new_prefix": root_remap[1],
            "hits": root_remap_hits,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill_upstream_report.json").write_text(json.dumps(report, indent=2) + "\n")

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
