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
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import ledger

_ORIGIN_NORMALIZED_SUFFIX = " normalized"
_UNKNOWN_CONVERTER = "unknown (pre-v1 import)"

_RUN_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})")
# results/web_runs/ is the OTHER concurrent session's live output area
# (Pipeline Studio); its run dirs use the same YYYYMMDD_ naming convention as
# the historical import/acquire runs backfill is meant to read, so a bare
# date-pattern check can't tell them apart -- excluded by name, explicitly.
_LIVE_OUTPUT_DIRS = frozenset({"web_runs"})


def _mtime_date(path):
    return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()


def _latest_file_mtime_date(dir_path):
    """Date of the most recently modified *file* under dir_path (recursive).
    A directory's own mtime is NOT a proxy for its content's age -- it
    changes on any add/delete inside it, independent of the age of the
    files that remain (real incident: a _source/<group>/ mirror dir's own
    mtime read days after every actual file still inside it, because a file
    had since been deleted from the dir). Returns None if the directory
    contains no files, so the caller can fall back explicitly rather than
    silently taking max() of an empty sequence."""
    files = [p for p in dir_path.rglob("*") if p.is_file()]
    if not files:
        return None
    return datetime.date.fromtimestamp(
        max(p.stat().st_mtime for p in files)
    ).isoformat()


def _atomic_write_text(path, content):
    """tmpfile + os.replace, matching lib.ledger._atomic_write_json's
    atomicity -- SOURCE_MANIFEST.json lives in the shared _source/ pool
    where a concurrent backfill/import run may be reading it; a bare
    path.write_text() could be observed half-written."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def _dated_run_match(name):
    """Regex match iff `name` starts with a genuinely valid YYYYMMDD date --
    not just 8 digits shaped like one (rejects e.g. "99999999_foo")."""
    m = _RUN_DATE_RE.match(name)
    if not m:
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return m


def _is_trusted_bundle_path(path, results_root):
    """A discovered bundle is only trusted if its lineage (results_root ->
    file) includes a genuine dated run directory, and its top-level
    container isn't a known live/concurrent-output area."""
    rel_parts = path.relative_to(results_root).parts
    if rel_parts and rel_parts[0] in _LIVE_OUTPUT_DIRS:
        return False
    return any(_dated_run_match(part) for part in rel_parts)


def _parse_bundle_aliases(raw_list):
    """--bundle-alias ASSET:N=PATH (repeatable) -> {(asset, n): Path}. One-off
    overrides for legacy bundles filed under a results dir/name that predates
    the `*/bundles/<asset>_m<n>.json` convention the glob assumes."""
    aliases = {}
    for raw in raw_list:
        key, sep, path = raw.partition("=")
        if not sep or not path:
            raise ValueError(f"--bundle-alias must be ASSET:N=PATH, got {raw!r}")
        asset, csep, n_str = key.partition(":")
        if not csep or not n_str.isdigit():
            raise ValueError(
                f"--bundle-alias ASSET:N=PATH: N must be an int, got {raw!r}"
            )
        aliases[(asset, int(n_str))] = Path(path)
    return aliases


def _find_latest_bundle(results_root, asset, n, report, note_key, bundle_aliases=None):
    if bundle_aliases and (asset, n) in bundle_aliases:
        return bundle_aliases[(asset, n)]
    # rglob, not a fixed one-level glob: the historical results tree has
    # bundles/ at varying depths across pipeline eras (e.g.
    # <results_root>/_test/<run>/bundles/ vs
    # <results_root>/_test/<run>/<subrun>/bundles/) -- but only matches whose
    # lineage traces to a genuine dated run are trusted (see
    # _is_trusted_bundle_path); anything else needs an explicit
    # --bundle-alias escape hatch. The newest trusted match by mtime wins.
    #
    # H1 顺手: candidates rejected by the trust filter are derived (filtered)
    # behavior, not just an absence -- log each one to the report instead of
    # letting it vanish silently (T8's own incident was exactly this shape:
    # a derived-behavior change with no record of what was excluded and why).
    matches = []
    for p in results_root.rglob(f"bundles/{asset}_m{n}.json"):
        if _is_trusted_bundle_path(p, results_root):
            matches.append(p)
        else:
            report["notes"]["bundle_rejected_untrusted"].append(
                f"{note_key}:{p.relative_to(results_root)}"
            )
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _map_origin(raw):
    if raw is None:
        return None
    if raw.endswith(_ORIGIN_NORMALIZED_SUFFIX):
        return raw[: -len(_ORIGIN_NORMALIZED_SUFFIX)]
    return raw


def _rebase_uri(uri, lib, results_root, report, note_key):
    """Legacy bundles recorded absolute URIs under an old repo root (the
    checkout has since been renamed); if the recorded path doesn't exist,
    re-root it under the current --library-dir/--results-root by anchoring
    on the first `data/asset_library/...` or `results/...` segment still
    present in the string. Never invents a path -- if no anchor is found or
    the rebased candidate doesn't exist either, the original is returned
    unchanged and file_missing surfaces honestly."""
    if not uri or Path(uri).exists():
        return uri
    for anchor, root in (("data/asset_library/", lib), ("results/", results_root)):
        idx = uri.find(anchor)
        if idx == -1:
            continue
        candidate = root / uri[idx + len(anchor) :]
        if candidate.exists():
            report["notes"]["uri_rebased"].append(note_key)
            return str(candidate)
    return uri


def _transform_representations(old_reps, lib, results_root, report, note_key):
    """rotated_z2y -> conversion_params; converter defaulted when absent
    (never fabricate a version); derived_from kept as-is; uri rebased onto
    the live repo root (see _rebase_uri)."""
    out = []
    for rep in old_reps:
        new_rep = dict(rep)
        new_rep["uri"] = _rebase_uri(
            rep.get("uri"), lib, results_root, report, note_key
        )
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
        if rep.get("role") == "visual" and rep.get("backend") == "sapien":
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


def _derive_group_from_reps(old_reps):
    """Some legacy bundles (e.g. pre-batch-pipeline smoke-test imports) never
    recorded source.group. Recover it from a representation URI/derived_from
    that already lives under <lib>/_source/<group>/ -- read off data the
    bundle already carries, not invented."""
    for rep in old_reps:
        for candidate in (
            rep.get("uri"),
            (rep.get("metadata") or {}).get("derived_from"),
        ):
            if candidate and "/_source/" in candidate:
                rest = candidate.split("/_source/", 1)[1]
                return rest.split("/", 1)[0]
    return None


def _derive_file_from_reps(old_reps, group):
    """Some legacy bundles recorded source.group but not source.file. Recover
    it as the path under <lib>/_source/<group>/ that a representation's
    uri/derived_from already points at -- same "read off what's already
    there" derivation as _derive_group_from_reps, not a guess."""
    if not group:
        return None
    marker = f"/_source/{group}/"
    for rep in old_reps:
        for candidate in (
            rep.get("uri"),
            (rep.get("metadata") or {}).get("derived_from"),
        ):
            if candidate and marker in candidate:
                return candidate.split(marker, 1)[1]
    return None


def _derive_prefix_from_reps(old_reps, library_name):
    """Best-effort `prefix` for a from-scratch-generated SOURCE_MANIFEST.json:
    strip the recorded source.library name off a representation's
    metadata.origin, e.g. "Isaac Assets 5.1 /Isaac/Props/X" + library
    "NVIDIA Isaac Assets 5.1" -> "/Isaac/Props/X". None (a precedented value
    in this schema) if nothing usable is on record -- never guessed."""
    if not library_name:
        return None
    for rep in old_reps:
        origin = (rep.get("metadata") or {}).get("origin")
        if origin and library_name.endswith(origin.split("/")[0].strip()):
            rest = origin[len(origin.split("/")[0]) :].strip()
            return rest or None
    return None


def _generate_source_manifest(group_dir, prefix_hint):
    """Derive a SOURCE_MANIFEST.json from files actually on disk under
    group_dir (sha256 per file) -- for a group whose mirror directory exists
    but whose manifest was never written. Same {prefix, files} shape as the
    existing manifests (see e.g. _source/mugs/SOURCE_MANIFEST.json); no keys
    invented."""
    files = {}
    for p in sorted(group_dir.rglob("*")):
        if p.is_file() and p.name != "SOURCE_MANIFEST.json":
            rel = p.relative_to(group_dir).as_posix()
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"prefix": prefix_hint, "files": files}


def _build_source(lib, bundle, bundle_path, report, note_key):
    """Returns (source_dict, pending_manifest). pending_manifest is None
    unless a _source/<group>/ mirror dir exists without a SOURCE_MANIFEST.json
    -- in which case it's (manifest_path, manifest_content, note_key), NOT
    yet written to disk. Writing it is main()'s job, gated on this asset's
    ledger actually clearing validate_ledger (T8 review I-1: writing it here
    unconditionally left an orphaned synthetic manifest in the pool for
    314_cabinet/sektion_cabinet even though 314_cabinet never promoted to
    v1 -- wrong prefix shape, and a retrieved_at that would resolve to the
    manifest's own (backfill-run-day) mtime on any later rerun instead of
    the real acquisition date)."""
    old_source = bundle.get("source", {})
    old_reps = bundle.get("representations", [])
    group = old_source.get("group")
    if group is None:
        group = _derive_group_from_reps(old_reps)
        if group is not None:
            report["notes"]["group_derived_from_representations"].append(
                f"{note_key}:{group}"
            )
    group_dir = lib / "_source" / group if group else None
    manifest_path = group_dir / "SOURCE_MANIFEST.json" if group_dir else None

    pending_manifest = None
    if manifest_path is not None and manifest_path.exists():
        retrieved_at = _mtime_date(manifest_path)
        source_manifest_path = str(manifest_path.resolve())
        basis = "source_manifest"
    elif group_dir is not None and group_dir.is_dir():
        # Mirror dir exists, the manifest file itself doesn't -- derive its
        # content now (pure computation, no write yet) so validate_ledger
        # can see a would-be source_manifest_path (check_files never checks
        # source_manifest_path existence, only representations[].uri, so
        # previewing the path here is safe).
        prefix_hint = _derive_prefix_from_reps(old_reps, old_source.get("library"))
        manifest_content = _generate_source_manifest(group_dir, prefix_hint)
        pending_manifest = (manifest_path, manifest_content, note_key)
        report["notes"]["source_manifest_generated"].append(note_key)
        # H1 hardening 4: the mirror dir's OWN mtime is polluted by any
        # later add/delete inside it and doesn't reflect the age of the
        # files that remain -- use the newest FILE inside instead. An empty
        # dir has no file mtime to fall back to, so it keeps the prior
        # (dir-mtime) semantics, explicitly flagged in the report rather
        # than silently falling through.
        latest_file_date = _latest_file_mtime_date(group_dir)
        if latest_file_date is not None:
            retrieved_at = latest_file_date
        else:
            retrieved_at = _mtime_date(group_dir)
            report["notes"]["retrieved_at_mirror_dir_empty"].append(note_key)
        source_manifest_path = str(manifest_path.resolve())
        basis = "generated_from_mirror_dir"
    else:
        retrieved_at = _mtime_date(bundle_path)
        source_manifest_path = None
        basis = "bundle_mtime"
    report["notes"]["retrieved_at_basis"][note_key] = basis

    file = old_source.get("file")
    if file is None:
        file = _derive_file_from_reps(old_reps, group)
        if file is not None:
            report["notes"]["file_derived_from_representations"].append(note_key)

    license_raw = old_source.get("license")
    license_block = {"spdx": None, "status": "unknown", "terms_note": license_raw}

    source = {
        "library": old_source.get("library"),
        "group": group,
        "file": file,
        "license": license_block,
        "retrieved_at": retrieved_at,
        "source_manifest_path": source_manifest_path,
    }
    return source, pending_manifest


def _run_timestamp(run_dir, matrix_path):
    """ISO timestamp for a verification entry: prefer a genuinely valid
    YYYYMMDD date off run_dir's own name or its immediate parent's -- later
    pipeline eras nest bundles/ (and the import_matrix.json next to it)
    under a named sub-batch (e.g. .../20260803_smoke_usd2envgen/batch_v3/
    bundles/), so the dated run directory is one level up from run_dir, not
    run_dir itself. Falls back to import_matrix.json's own mtime date rather
    than parsing a non-numeric directory name (e.g. "batch_v3") into a
    bogus date."""
    for d in (run_dir, run_dir.parent):
        m = _dated_run_match(d.name)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00"
    return f"{_mtime_date(matrix_path)}T00:00:00"


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
    timestamp = _run_timestamp(run_dir, matrix_path)
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


def _derive_scale_applied(old_physical, report, note_key):
    """Legacy bundles that predate the scale_applied field still recorded a
    per-axis physical.scale vector; when it's uniform that IS scale_applied
    (a field rename, not new information) -- non-uniform or absent is left
    None rather than guessed."""
    scale = old_physical.get("scale")
    if isinstance(scale, list) and len(scale) == 3 and len(set(scale)) == 1:
        report["notes"]["scale_applied_derived_from_scale_vector"].append(note_key)
        return scale[0]
    return None


def _normalize_articulation(old_articulation):
    """Legacy articulated bundles carry articulation.joints[].name but not
    the joint_names list the v1 schema requires (validator: kind=articulated
    needs articulation.joint_names); derive it in joints[] order rather than
    dropping the rest of the block. Matches s13b's own joint_names shape
    (dof-length list of movable joint name strings)."""
    art = dict(old_articulation) if isinstance(old_articulation, dict) else {}
    if "joint_names" not in art:
        names = [
            j.get("name")
            for j in art.get("joints") or []
            if isinstance(j, dict) and j.get("name")
        ]
        if names:
            art["joint_names"] = names
    return art


def _build_model_entry(lib, results_root, asset, n, frag_data, report, bundle_aliases):
    note_key = f"{asset}:m{n}"
    bundle_path = _find_latest_bundle(
        results_root, asset, n, report, note_key, bundle_aliases
    )
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
    representations = _transform_representations(
        old_reps, lib, results_root, report, note_key
    )
    origin_convention = _origin_convention(old_reps)

    mass = dict(old_physical.get("mass_kg", {}))
    mass["runtime_default_basis"] = (
        "urdf_inertial" if kind == "articulated" else "global_constant"
    )

    source, pending_manifest = _build_source(lib, bundle, bundle_path, report, note_key)

    scale_applied = old_physical.get("scale_applied")
    if scale_applied is None:
        scale_applied = _derive_scale_applied(old_physical, report, note_key)

    model_entry = ledger.new_model_entry(
        model=n,
        representations=representations,
        mesh_bbox_m=old_physical.get("mesh_bbox_m"),
        mesh_up_axis=old_physical.get("mesh_up_axis"),
        origin_convention=origin_convention,
        scale_applied=scale_applied,
        size_resolution=old_physical.get("size_resolution"),
        conventions=conventions,
        source=source,
        verification=[],
        articulation=_normalize_articulation(bundle.get("articulation")),
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
        "pending_manifest": pending_manifest,
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
    if ids:
        return sorted(ids)
    # Articulated layout (s13b convention): <asset>/<n>/model_data<n>.json,
    # one numeric subdir per model, instead of the rigid-asset flat layout
    # above.
    for sub in sorted(asset_dir.iterdir()):
        if sub.is_dir() and sub.name.isdigit():
            marker = sub / f"model_data{sub.name}.json"
            if marker.exists():
                ids.append(int(sub.name))
    return sorted(ids)


def _empty_report():
    return {
        "written": 0,
        "skipped": [],
        "excluded": [],
        "violations": {},
        "notes": {
            "aliases_defaulted": [],
            "no_bundle_found": [],
            "no_settle_record": [],
            "retrieved_at_basis": {},
            "group_derived_from_representations": [],
            "source_manifest_generated": [],
            "source_manifest_written": [],
            "scale_applied_derived_from_scale_vector": [],
            "uri_rebased": [],
            "file_derived_from_representations": [],
            "retrieved_at_mirror_dir_empty": [],
            "bundle_rejected_untrusted": [],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--fragment", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--bundle-alias",
        action="append",
        default=[],
        metavar="ASSET:N=PATH",
        help="one-off override: use PATH as the legacy bundle for model N of "
        "ASSET instead of the */bundles/<asset>_m<n>.json glob (for bundles "
        "filed under a results dir/name that predates that convention, e.g. "
        "a pre-batch-pipeline articulated smoke-test bundle). Repeatable.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    lib = Path(args.library_dir)
    results_root = Path(args.results_root)
    frag_data = yaml.safe_load(Path(args.fragment).read_text()) or {}
    out_dir = Path(args.out)
    bundle_aliases = _parse_bundle_aliases(args.bundle_alias)

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

        model_ids = set(_model_ids(asset_dir))
        model_ids |= {n for (a, n) in bundle_aliases if a == asset}
        model_ids = sorted(model_ids)
        if not model_ids:
            continue

        led = None
        pending_manifests = []
        for n in model_ids:
            built = _build_model_entry(
                lib, results_root, asset, n, frag_data, report, bundle_aliases
            )
            if built is None:
                continue
            if built["pending_manifest"] is not None:
                pending_manifests.append(built["pending_manifest"])
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
            # Real data gap the mapping logic can't close without fabricating
            # a value -- pool layer is only-append: don't write a ledger
            # that would fail its own validator, and don't write any
            # SOURCE_MANIFEST.json side effect for it either (T8 review I-1:
            # a manifest written for an asset that never promotes to v1
            # would sit in the pool as an orphaned, undated synthetic file).
            report["excluded"].append(asset)
        elif args.apply:
            written_paths = set()
            for manifest_path, manifest_content, mnote_key in pending_manifests:
                if manifest_path not in written_paths:
                    _atomic_write_text(
                        manifest_path, json.dumps(manifest_content, indent=2) + "\n"
                    )
                    written_paths.add(manifest_path)
                report["notes"]["source_manifest_written"].append(mnote_key)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps(led, indent=2) + "\n")
            report["written"] += 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backfill_report.json").write_text(json.dumps(report, indent=2) + "\n")

    sys.exit(1 if report["violations"] else 0)


if __name__ == "__main__":
    main()
