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

# RoboTwin's own scene-generation layout is Y-up with a bottom-center mesh
# origin for every load_type it emits (rigid GLB pairs and PartNet-Mobility
# URDF alike) -- evidenced by z_policy being uniformly "origin_on_table"
# across both load_types in the catalog, and by the rigid-asset precedent in
# scripts/a_forward/robotwin_asset.py (mesh_up_axis="Y"). No per-asset
# variation is recorded anywhere upstream, so this is applied uniformly
# rather than conditioned on kind.
MESH_UP_AXIS = "Y"
ORIGIN_CONVENTION = "bottom-center"


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
    robotwin_root,
    existing_model,
    isaac_usd_path,
    report,
    note_key,
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
        "file": _relative_to_root(model["model_path"], robotwin_root),
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
        mesh_up_axis=MESH_UP_AXIS,
        origin_convention=ORIGIN_CONVENTION,
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


def _empty_report():
    return {
        "written": [],
        "skipped_unusable": [],
        "aliases_defaulted": [],
        "violations": {},
        "notes": {
            "non_uniform_scale": [],
            "isaac_usd_registered": [],
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
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    catalog = json.loads(Path(args.catalog).read_text())
    robotwin_root = Path(catalog["robotwin_root"])
    source_commit = catalog["source_commit"]
    out_dir = Path(args.out)

    try:
        isaac_usd_map = _parse_isaac_usd(args.isaac_usd)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

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

        first_usable_model_id = usable_models[0]["model_id"]

        # upsert_model deep-copies existing_ledger internally and validates
        # asset-level fields match; start from None explicitly here (the
        # derived-core rebuild always regenerates asset-level fields fresh
        # from the catalog) and let each upsert_model call below re-attach
        # models[] one at a time.
        led = None
        for m in usable_models:
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
                robotwin_root,
                existing_model,
                isaac_usd_path,
                report,
                note_key,
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

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill_upstream_report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
