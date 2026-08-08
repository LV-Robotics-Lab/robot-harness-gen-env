#!/usr/bin/env python3
"""Backfill: aggregate existing per-model legacy bundles into one
per-asset v1 ledger (data/asset_library/<asset>/ledger.json).

Reads, per asset, the legacy `model_data<N>.json` markers already
materialized under --library-dir, locates the newest matching legacy
per-model bundle under --results-root/*/bundles/<asset>_m<N>.json, and the
overrides fragment (YAML, asset-level aliases/colors + per-model stable
pose conventions), and upgrades each into a v1 models[] entry via
lib.ledger.upsert_model / new_model_entry. Idempotent: an asset whose
ledger.json already has schema_version == v1 is skipped entirely.

PyYAML is used here (and only here / in the test) -- lib/ stays pure
stdlib so both conda envs can import it.
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger

_ORIGIN_NORMALIZED_SUFFIX = " normalized"
_UNKNOWN_CONVERTER = "unknown (pre-v1 import)"


def _mtime_date(path):
    return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()


def _find_latest_bundle(results_root, asset, n):
    matches = list(results_root.glob(f"*/bundles/{asset}_m{n}.json"))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _map_origin(raw):
    if raw is None:
        return None
    if raw.endswith(_ORIGIN_NORMALIZED_SUFFIX):
        return raw[: -len(_ORIGIN_NORMALIZED_SUFFIX)]
    return raw


def _transform_representations(old_reps):
    """rotated_z2y -> conversion_params; converter defaulted when absent
    (never fabricate a version); derived_from kept as-is."""
    out = []
    for rep in old_reps:
        new_rep = dict(rep)
        meta = rep.get("metadata") or {}
        new_meta = {}
        if meta.get("derived_from") is not None:
            new_meta["derived_from"] = meta["derived_from"]
        new_meta["converter"] = meta.get("converter") or _UNKNOWN_CONVERTER
        conv_params = {}
        if "rotated_z2y" in meta:
            conv_params["rotated_z2y"] = meta["rotated_z2y"]
        new_meta["conversion_params"] = conv_params
        new_rep["metadata"] = new_meta
        out.append(new_rep)
    return out


def _origin_convention(old_reps):
    for rep in old_reps:
        if rep.get("role") == "visual":
            origin = (rep.get("metadata") or {}).get("origin")
            if origin:
                return _map_origin(origin)
    return None


def _build_conventions(old_conv, frag_model):
    pose_id = frag_model.get("stable_pose_id")
    orientation = frag_model.get("stable_orientation_wxyz")
    stable_poses = (
        [{"pose_id": pose_id, "orientation_wxyz": orientation, "is_default": True}]
        if pose_id and orientation
        else []
    )
    conventions = {
        "is_static": frag_model.get("is_static", old_conv.get("is_static", False)),
        "z_policy": frag_model.get("z_policy", old_conv.get("z_policy")),
        "footprint_shape": frag_model.get(
            "footprint_shape", old_conv.get("footprint_shape")
        ),
        "stable_poses": stable_poses,
        "inherited_from": old_conv.get("precedent"),
    }
    if "note" in old_conv:
        conventions["note"] = old_conv["note"]
    return conventions


def _build_source(lib, bundle, bundle_path, report, note_key):
    old_source = bundle.get("source", {})
    group = old_source.get("group")
    manifest_path = lib / "_source" / group / "SOURCE_MANIFEST.json" if group else None
    if manifest_path is not None and manifest_path.exists():
        retrieved_at = _mtime_date(manifest_path)
        source_manifest_path = str(manifest_path.resolve())
        basis = "source_manifest"
    else:
        retrieved_at = _mtime_date(bundle_path)
        source_manifest_path = None
        basis = "bundle_mtime"
    report["notes"]["retrieved_at_basis"][note_key] = basis

    license_raw = old_source.get("license")
    license_block = {"spdx": None, "status": "unknown", "terms_note": license_raw}

    return {
        "library": old_source.get("library"),
        "group": group,
        "file": old_source.get("file"),
        "license": license_block,
        "retrieved_at": retrieved_at,
        "source_manifest_path": source_manifest_path,
    }


def _build_verification(run_dir, asset, n, model_entry, report, note_key):
    matrix_path = run_dir / "import_matrix.json"
    row = None
    if matrix_path.exists():
        matrix = json.loads(matrix_path.read_text())
        row = next(
            (r for r in matrix if r.get("asset") == asset and r.get("model") == n),
            None,
        )
    if row is None:
        report["notes"]["no_settle_record"].append(note_key)
        return []
    run_id = run_dir.name
    d = run_id[:8]
    timestamp = f"{d[0:4]}-{d[4:6]}-{d[6:8]}T00:00:00"
    verdict = "pass" if row.get("status") == "accepted" else "fail"
    return [
        {
            "backend": "sapien",
            "check": "settle",
            "verdict": verdict,
            "run_id": run_id,
            "timestamp": timestamp,
            "verified_digest": ledger.reps_digest(model_entry, "sapien"),
            "report_path": str(matrix_path.resolve()),
        }
    ]


def _build_model_entry(lib, results_root, asset, n, frag_data, report):
    bundle_path = _find_latest_bundle(results_root, asset, n)
    note_key = f"{asset}:m{n}"
    if bundle_path is None:
        report["notes"]["no_bundle_found"].append(note_key)
        return None

    bundle = json.loads(bundle_path.read_text())
    run_dir = bundle_path.parents[1]  # results_root/<run>/bundles/<file> -> <run>
    kind = "articulated" if "articulated" in bundle.get("tags", []) else "rigid"

    frag_asset = frag_data.get(asset) or {}
    frag_model = (frag_asset.get("models") or {}).get(str(n), {})

    old_physical = bundle.get("physical", {})
    old_conv = old_physical.get("conventions", {})
    conventions = _build_conventions(old_conv, frag_model)

    old_reps = bundle.get("representations", [])
    representations = _transform_representations(old_reps)
    origin_convention = _origin_convention(old_reps)

    mass = dict(old_physical.get("mass_kg", {}))
    mass["runtime_default_basis"] = (
        "urdf_inertial" if kind == "articulated" else "global_constant"
    )

    source = _build_source(lib, bundle, bundle_path, report, note_key)

    model_entry = ledger.new_model_entry(
        model=n,
        representations=representations,
        mesh_bbox_m=old_physical.get("mesh_bbox_m"),
        mesh_up_axis=old_physical.get("mesh_up_axis"),
        origin_convention=origin_convention,
        scale_applied=old_physical.get("scale_applied"),
        size_resolution=old_physical.get("size_resolution"),
        conventions=conventions,
        source=source,
        verification=[],
        articulation=bundle.get("articulation") or {},
        mass_override=mass,
    )
    model_entry["verification"] = _build_verification(
        run_dir, asset, n, model_entry, report, note_key
    )
    return {
        "model_entry": model_entry,
        "kind": kind,
        "category": bundle.get("category"),
        "tags": bundle.get("tags", []),
    }


def _semantics_for_asset(asset, category, frag_data, report, seen_defaults):
    frag_asset = frag_data.get(asset)
    if frag_asset is None:
        if asset not in seen_defaults:
            report["notes"]["aliases_defaulted"].append(asset)
            seen_defaults.add(asset)
        return [category], []
    return list(frag_asset.get("aliases", [])), list(frag_asset.get("colors", []))


def _model_ids(asset_dir):
    ids = []
    for p in asset_dir.glob("model_data*.json"):
        suffix = p.stem[len("model_data") :]
        if suffix.isdigit():
            ids.append(int(suffix))
    return sorted(ids)


def _empty_report():
    return {
        "written": 0,
        "skipped": [],
        "violations": {},
        "notes": {
            "aliases_defaulted": [],
            "no_bundle_found": [],
            "no_settle_record": [],
            "retrieved_at_basis": {},
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--fragment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    lib = Path(args.library_dir)
    results_root = Path(args.results_root)
    frag_data = yaml.safe_load(Path(args.fragment).read_text()) or {}
    out_dir = Path(args.out)

    report = _empty_report()
    seen_defaults = set()

    for asset_dir in sorted(
        p for p in lib.iterdir() if p.is_dir() and not p.name.startswith("_")
    ):
        asset = asset_dir.name
        lp = ledger.ledger_path(lib, asset)
        if lp.exists():
            existing = json.loads(lp.read_text())
            if existing.get("schema_version") == ledger.SCHEMA_VERSION:
                report["skipped"].append(asset)
                continue

        model_ids = _model_ids(asset_dir)
        if not model_ids:
            continue

        led = None
        for n in model_ids:
            built = _build_model_entry(lib, results_root, asset, n, frag_data, report)
            if built is None:
                continue
            aliases, colors = _semantics_for_asset(
                asset, built["category"], frag_data, report, seen_defaults
            )
            led = ledger.upsert_model(
                led,
                asset=asset,
                category=built["category"],
                kind=built["kind"],
                aliases=aliases,
                colors=colors,
                materials=[],
                tags=built["tags"],
                model_entry=built["model_entry"],
            )

        if led is None:
            continue

        violations = ledger.validate_ledger(led, check_files=True)
        if violations:
            report["violations"][asset] = [
                {"path": v.path, "code": v.code, "message": v.message}
                for v in violations
            ]

        if args.apply:
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps(led, indent=2) + "\n")
            report["written"] += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill_report.json").write_text(json.dumps(report, indent=2) + "\n")

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
