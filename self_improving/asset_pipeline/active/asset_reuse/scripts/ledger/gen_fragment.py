#!/usr/bin/env python3
"""Generate the overrides fragment (external_overrides_fragment_merged.yml
shape) from the authoritative per-asset ledgers under --library-dir.

Filtering: a model is included only if its latest (backend=sapien, check=X)
verification has a valid immutable evidence artifact, is digest-fresh, and
has verdict == pass, where X is
kind-aware: asset-level kind=="articulated" ledgers (s13b pipeline) check
"joint_sweep" -- its 120-step settle-then-sweep already subsumes a bare
settle check -- while kind=="rigid" ledgers check "settle" as before.
lib.ledger.latest_trusted_verification encodes the complete trust decision;
legacy receipts and mutable report paths are deliberately not sufficient.
--license-gate additionally requires source.license.status == "declared".

PyYAML is not used for the write path -- the output is hand-formatted to
match the existing merged fragment's exact style (2-space indent, flow-style
lists, quoted model-id keys) so the generator doesn't introduce style drift.
lib/ stays pure stdlib; this script is the (only) place PyYAML would be used,
and even here only the test round-trips through yaml.safe_load to check the
result, not this module.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import ledger, ledger_writes


def _default_pose(model):
    poses = model["physical"]["conventions"]["stable_poses"]
    for pose in poses:
        if pose.get("is_default"):
            return pose
    raise ValueError(f"model {model.get('model_id')!r} has no is_default stable pose")


def _project_model(model):
    conv = model["physical"]["conventions"]
    pose = _default_pose(model)
    out = {
        "stable_pose_id": pose["pose_id"],
        "stable_orientation_wxyz": pose["orientation_wxyz"],
        "z_policy": conv["z_policy"],
        "footprint_shape": conv["footprint_shape"],
    }
    if conv.get("is_static") is True:
        out["is_static"] = True
    # v3 appearance -> per-MODEL colours in the view (CatalogModel.colors):
    # the model-level truth the asset-level union cannot carry (a yellow, a
    # red and a white basket under one asset_id)
    measured = (model.get("appearance") or {}).get("colors_measured")
    if measured:
        out["colors"] = list(measured)
    return out


def _project_asset(led, models_out, measured_colors):
    out = {
        "category": led["category"],
        "aliases": list(led["semantics"]["aliases"]),
        "models": models_out,
    }
    colors = led["semantics"].get("colors") or []
    if colors:
        out["colors"] = list(colors)
    elif measured_colors is not None:
        # v3: measured appearance is the authority for colour when no hand
        # declaration exists. Same publication rule the shadow build used
        # when this fact lived in a sidecar file: publish only when every
        # projected model agrees -- entry.colors is asset-level upstream and
        # a non-empty value REJECTS mismatching queries, so a multi-colour
        # asset claiming one colour would hide its other models forever.
        out["colors"] = list(measured_colors)
    # materials: projected since v3. Through v2 this line was missing -- the
    # upstream grounder consults entry.materials for material queries, so a
    # "wooden bowl" could never match ANY external asset (measured
    # 2026-08-15, dialectic round).
    materials = led["semantics"].get("materials") or []
    if materials:
        out["materials"] = list(materials)
    return out


def _agreed_measured_colors(led, model_ids):
    """The colour every projected model agrees on, else None."""
    seen = []
    for model in led.get("models", []):
        if str(model.get("model_id")) not in model_ids:
            continue
        colors = (model.get("appearance") or {}).get("colors_measured")
        seen.append(tuple(colors) if colors else None)
    if not seen or any(c is None for c in seen):
        return None
    return list(seen[0]) if len(set(seen)) == 1 else None


def _qualified_assets(library_dir):
    """Yield identity-checked ledgers and their exact physically qualified models."""

    library_dir = Path(library_dir)
    for asset_path in ledger.iter_assets(library_dir):
        asset_key = asset_path.name
        try:
            loaded = ledger.load_asset_ledger(library_dir, asset_key)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        led = loaded.document
        # A trusted receipt binds the ledger hashes, while check_files proves
        # those hashes still describe the loader-visible bytes on disk.
        if ledger.validate_ledger(led, check_files=True):
            continue
        check = "joint_sweep" if led.get("kind") == "articulated" else "settle"
        models = []
        for model in led.get("models", []):
            latest = ledger.latest_trusted_verification(model, "sapien", check, loaded.asset_key)
            if latest is not None and latest.get("verdict") == "pass":
                models.append(model)
        if models:
            yield loaded, led, tuple(models)


def _project_representation_roles(model):
    roles = {}
    for representation in model.get("representations", []):
        if representation.get("backend") != "sapien" or representation.get("role") == "snapshot":
            continue
        files = [dict(member) for member in representation["files"]]
        primary = next(
            member
            for member in files
            if (member["uri"], member["sha256"])
            == (representation["uri"], representation["sha256"])
        )
        roles.setdefault(representation["role"], []).append({"primary": primary, "closure": files})
    return roles


def qualified_execution_projection(library_dir):
    """Return the complete authority needed to publish a library execution view."""

    projection = {}
    for loaded, _led, models in _qualified_assets(library_dir):
        projected_models = {}
        for model in models:
            model_id = model["model_id"]
            model_dirs = [str(loaded.asset_dir.resolve(strict=True))]
            numbered_model_dir = loaded.asset_dir / str(model_id)
            if numbered_model_dir.is_dir():
                model_dirs.append(str(numbered_model_dir.resolve(strict=True)))
            metadata_path = loaded.asset_dir / f"model_data{model_id}.json"
            metadata = (
                ledger_writes.provenance_file_record(metadata_path)
                if metadata_path.is_file()
                else None
            )
            projected_models[model_id] = {
                "reps_digest": ledger.reps_digest(model, "sapien"),
                "physical_facts_digest": ledger.settle_physical_facts_digest(model),
                "stable_pose_id": _default_pose(model)["pose_id"],
                "stable_orientation_wxyz": list(_default_pose(model)["orientation_wxyz"]),
                "z_policy": model["physical"]["conventions"]["z_policy"],
                "model_dirs": model_dirs,
                "metadata": metadata,
                "roles": _project_representation_roles(model),
            }
        projection[loaded.asset_key] = {
            "asset_dir": str(loaded.asset_dir.resolve(strict=True)),
            "models": projected_models,
        }
    return projection


def qualified_model_ids(library_dir):
    """Return the shared execution-view projection used by s9 and this generator."""

    return {
        asset_key: frozenset(model["model_id"] for model in models)
        for loaded, _led, models in _qualified_assets(library_dir)
        for asset_key in (loaded.asset_key,)
    }


def _samefile(left, right):
    try:
        return Path(left).samefile(Path(right))
    except (OSError, TypeError, ValueError):
        return False


def _path_matches_record(path, record):
    return ledger.artifact_file_record_is_current(record) and _samefile(
        path, ledger.resolve_uri(record["uri"])
    )


def _role_primaries(projected_model, roles):
    return [
        binding["primary"] for role in roles for binding in projected_model["roles"].get(role, ())
    ]


def _catalog_model_matches_projection(catalog_model, projected_model):
    if not any(
        _samefile(catalog_model.get("model_path"), model_dir)
        for model_dir in projected_model["model_dirs"]
    ):
        return False
    for bindings in projected_model["roles"].values():
        for binding in bindings:
            if not all(
                ledger.artifact_file_record_is_current(member) for member in binding["closure"]
            ):
                return False
    metadata_path = catalog_model.get("metadata_path")
    if metadata_path is not None and (
        projected_model["metadata"] is None
        or not _path_matches_record(metadata_path, projected_model["metadata"])
    ):
        return False
    path_roles = {
        "urdf_path": ("visual_and_collision",),
        "visual_path": ("visual", "visual_and_collision"),
        "collision_path": ("collision", "visual_and_collision"),
    }
    for field, roles in path_roles.items():
        path = catalog_model.get(field)
        if path is None:
            continue
        if not any(
            _path_matches_record(path, primary)
            for primary in _role_primaries(projected_model, roles)
        ):
            return False
    if catalog_model.get("usable") is True:
        if catalog_model.get("urdf_path") is None and (
            catalog_model.get("visual_path") is None or catalog_model.get("collision_path") is None
        ):
            return False
    return (
        catalog_model.get("stable_pose_id") == projected_model["stable_pose_id"]
        and catalog_model.get("stable_orientation_wxyz")
        == projected_model["stable_orientation_wxyz"]
        and catalog_model.get("z_policy") == projected_model["z_policy"]
    )


def filter_catalog_execution_view(document, library_dir, qualified, *, shadow_objects=None):
    """Restrict scanner output only where its asset resolves into the library.

    Upstream-native entries are deliberately untouched.  This preserves the
    fallback when an unqualified library directory has the same name as a
    valid native asset.  The caller must parse the returned document through
    ``scene_gen.catalog.AssetCatalog`` before publishing it.
    """

    filtered = json.loads(json.dumps(document, allow_nan=False))
    root = Path(library_dir).resolve()
    entries = []
    for entry in filtered.get("entries", []):
        try:
            asset_path = Path(entry["asset_path"]).resolve(strict=True)
            from_library = asset_path.is_relative_to(root)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            # A malformed scanner entry is left for AssetCatalog's strict
            # parser to reject; this helper must not accidentally classify it
            # as a trusted library entry.
            entries.append(entry)
            continue
        authority = qualified.get(entry.get("asset_id"))
        if authority is None:
            if from_library:
                continue
            if shadow_objects is not None and not _samefile(
                asset_path, Path(shadow_objects) / str(entry.get("asset_id"))
            ):
                continue
            entries.append(entry)
            continue
        if not from_library or not _samefile(asset_path, authority["asset_dir"]):
            continue
        seen_model_ids = set()
        models = []
        for model in entry.get("models", []):
            model_id = model.get("model_id")
            if model_id in seen_model_ids:
                models = []
                break
            seen_model_ids.add(model_id)
            projected_model = authority["models"].get(model_id)
            if projected_model is not None and _catalog_model_matches_projection(
                model, projected_model
            ):
                models.append(model)
        if not models:
            continue
        entry["models"] = models
        entry["available"] = any(model.get("usable") is True for model in models)
        entry["availability_reasons"] = sorted(
            {
                reason
                for model in models
                for reason in model.get("missing", [])
                if isinstance(reason, str)
            }
        )
        entries.append(entry)
    filtered["entries"] = entries
    return filtered


def generate(library_dir, *, license_gate=False):
    """Project the per-asset ledgers under library_dir into an overrides
    fragment dict, filtering on latest-verification-pass (+ optional
    license gate). The verification check is kind-aware: "joint_sweep" for
    asset-level kind=="articulated" ledgers, "settle" for kind=="rigid"
    ledgers (see module docstring). Returns (fragment, stats).

    stats["unknown_license_models"] counts verification-passing models
    whose source.license.status != "declared" -- computed the same way
    regardless of license_gate (it answers "how many need a license
    decision", not "how many the gate happened to eat this run")."""
    frag = {}
    stats = {"unknown_license_models": 0}

    for loaded, led, qualified_models in _qualified_assets(library_dir):
        asset_key = loaded.asset_key
        models_out = {}
        for model in qualified_models:
            license_status = model.get("source", {}).get("license", {}).get("status")
            if license_status != "declared":
                stats["unknown_license_models"] += 1
            if license_gate and license_status != "declared":
                continue
            models_out[str(model["model_id"])] = _project_model(model)
        if models_out:
            measured = _agreed_measured_colors(led, set(models_out))
            frag[asset_key] = _project_asset(led, models_out, measured)

    return frag, stats


def _fmt_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _fmt_flow_list(items):
    return "[" + ", ".join(_fmt_scalar(v) for v in items) + "]"


def write_yaml(frag, path):
    """Hand-formatted writer (deliberately not yaml.dump, to avoid style
    drift from the existing merged fragment): 2-space indent per level,
    flow-style `[a, b]` lists, quoted `"<model_id>"` keys, `colors` omitted
    when empty. Semantics are what matter -- yaml.safe_load(output) should
    equal the equivalent hand-built dict."""
    lines = []
    for asset_key in sorted(frag):
        entry = frag[asset_key]
        lines.append(f"  {asset_key}:")
        lines.append(f"    category: {entry['category']}")
        lines.append(f"    aliases: {_fmt_flow_list(entry['aliases'])}")
        if entry.get("colors"):
            lines.append(f"    colors: {_fmt_flow_list(entry['colors'])}")
        if entry.get("materials"):
            lines.append(f"    materials: {_fmt_flow_list(entry['materials'])}")
        lines.append("    models:")
        for model_id in sorted(entry["models"], key=int):
            m = entry["models"][model_id]
            lines.append(f'      "{model_id}":')
            lines.append(f"        stable_pose_id: {m['stable_pose_id']}")
            lines.append(
                f"        stable_orientation_wxyz: {_fmt_flow_list(m['stable_orientation_wxyz'])}"
            )
            lines.append(f"        z_policy: {m['z_policy']}")
            lines.append(f"        footprint_shape: {m['footprint_shape']}")
            if m.get("is_static"):
                lines.append("        is_static: true")
            if m.get("colors"):
                lines.append(f"        colors: {_fmt_flow_list(m['colors'])}")

    text = "\n".join(lines) + ("\n" if lines else "")
    Path(path).write_text(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--license-gate", action="store_true")
    args = parser.parse_args(argv)

    frag, stats = generate(args.library_dir, license_gate=args.license_gate)
    write_yaml(frag, args.out)

    n = stats["unknown_license_models"]
    if args.license_gate:
        print(
            f"WARNING: {n} models with unknown license excluded by license gate",
            file=sys.stderr,
        )
    else:
        print(f"WARNING: {n} models with unknown license in view", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
