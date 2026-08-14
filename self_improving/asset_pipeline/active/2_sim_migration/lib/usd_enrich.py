"""Enrich an openxsim EnvironmentPackage with backend-specific asset representations.

The env-gen importer (`import_env_gen`) fills each asset with only its native
SAPIEN mesh representation (GLB/URDF) — all that `resolved_scene.json` carries.
To actually compile an env-gen scene to Isaac, each asset also needs an
`isaacsim` USD representation. The asset pipeline (1_asset_reuse) converts the
RoboTwin GLB → USD and knows where each USD lives; this module is the junction
that *registers* those USD files onto the IR's asset bundles, so
`IsaacSimCompiler` finds them instead of raising "no existing USD representation".

Conversion (making the USD) is the asset pipeline's job; enrichment (recording
it in the IR) is this junction's job — the two are deliberately separate.

Scale ownership: the asset pipeline bakes the RoboTwin ``model_data`` scale into
the USD geometry (the USD is a finished, real-world-sized asset). env-gen carries
that *same* RoboTwin scale onto ``SceneObject.scale`` (its ``mesh_scale``), which
the SAPIEN-native path correctly applies to the *raw* mesh. For the baked USD,
applying it again as ``xformOp:scale`` would double-scale the object, so when this
junction attaches a baked USD it also neutralizes ``scale`` to ``(1, 1, 1)`` on
every object that resolves to that asset. This assumes the baked scale equals the
object's ``mesh_scale`` — true here because both come from RoboTwin ``model_data``.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping

from agenticsim.openxsim.ir import AssetRepresentation, EnvironmentPackage

# `lib.ledger` (1_asset_reuse/lib) is only needed by the ledger-backed lookup
# path below (enrich_from_ledgers / _latest_isaac_pass); it is imported
# locally in those two functions rather than at module level so that
# enrich_isaac_usd -- and every existing caller of this module -- keeps no
# import-time dependency on 1_asset_reuse being on sys.path.


def _apply_isaac_representations(
    package: EnvironmentPackage,
    enrichment: Mapping[str, AssetRepresentation],
) -> EnvironmentPackage:
    """Return a copy of ``package`` with the isaacsim representation in
    ``enrichment`` (keyed by ``AssetBundle.asset_id``) appended to the
    matching asset, and ``scale`` neutralized to ``(1, 1, 1)`` on every
    object that resolves to an enriched asset (see module docstring: a baked
    USD already carries the RoboTwin scale, re-applying ``mesh_scale`` on top
    of it would double-scale the object). Callers decide which assets belong
    in ``enrichment`` (and pre-check the USD file exists) -- this is purely
    the attach + neutralize mechanism, shared so it stays in exactly one
    place.
    """
    enriched_asset_ids: set[str] = set()
    neutralize_ids: set[str] = set()
    new_assets = []
    for asset in package.assets:
        rep = enrichment.get(asset.asset_id)
        if rep is not None:
            asset = replace(asset, representations=asset.representations + (rep,))
            enriched_asset_ids.add(asset.asset_id)
            # v3: neutralize ONLY when the representation declares its scale
            # baked. The old unconditional neutralize assumed every attached
            # USD carried the RoboTwin scale -- measured false for the whole
            # external pool, whose isaacsim reps point at _source originals:
            # a cracker box compiled 2.8527x (= exactly 1/scale) too large
            # (E2, 2026-08-15). An undeclared rep keeps the object scale.
            gs = (rep.metadata or {}).get("geometry_state") or {}
            if gs.get("scale_baked") is True:
                neutralize_ids.add(asset.asset_id)
        new_assets.append(asset)

    new_objects = tuple(
        replace(obj, scale=(1.0, 1.0, 1.0))
        if obj.asset_id in neutralize_ids
        else obj
        for obj in package.env.objects
    )
    return replace(
        package,
        assets=tuple(new_assets),
        env=replace(package.env, objects=new_objects),
    )


def enrich_isaac_usd(
    package: EnvironmentPackage,
    usd_lookup: Mapping[tuple[str, int], str],
) -> EnvironmentPackage:
    """Return a copy of ``package`` with an ``isaacsim`` USD representation added
    to every asset for which ``usd_lookup`` supplies an existing USD file, and the
    ``scale`` neutralized on every object that resolves to such an asset.

    ``usd_lookup`` maps ``(env_gen_asset_id, model_id)`` -> USD path. The key is
    matched against each AssetBundle's ``source["asset_id"]`` / ``source["model_id"]``
    (the original env-gen identity the importer preserves). Assets with no lookup
    entry, or whose USD file is absent on disk, are left unchanged — the
    Transfer-side compiler will then honestly report the missing representation
    rather than this junction fabricating one. Their objects keep their original
    ``scale`` (the SAPIEN-native path still needs it).
    """
    enrichment: dict[str, AssetRepresentation] = {}
    for asset in package.assets:
        key = (asset.source.get("asset_id"), asset.source.get("model_id"))
        usd = usd_lookup.get(key)
        if usd and Path(usd).is_file():
            enrichment[asset.asset_id] = AssetRepresentation(
                format="usd",
                uri=str(usd),
                backend="isaacsim",
                role="visual_and_collision",
                # this path's contract has always been "caller hands a baked
                # USD"; state it so the neutralize rule stays uniform
                metadata={"geometry_state": {"scale_baked": True}},
            )
    return _apply_isaac_representations(package, enrichment)


# --- ledger-backed lookup (spec §9: upstream + external pool ledgers) -----

# An IR AssetBundle.asset_id built from a ledger asset_id (env-gen references
# a pool asset directly) has the shape ``<prefix>_<dir>_m<model_id>``, e.g.
# "external_302_can_m3" (pool asset unpacked) or "robotwin_071_can_m0"
# (upstream asset, same shape via to_ir_bundles) -- see
# lib/ledger.py:upsert_model's asset_id_prefix and to_ir_bundles' "_m<N>"
# suffix, which this parser inverts.
_LEDGER_ID_PREFIXES = ("asset_", "robotwin_", "external_")
_MODEL_SUFFIX_RE = re.compile(r"^(.*)_m(\d+)$")


def _parse_ledger_asset_id(asset_id: str) -> tuple[str, int]:
    """Recover (ledger directory name, model_id) from an IR asset_id. Strips a
    leading ``robotwin_``/``external_`` prefix if present, then a trailing
    ``_m<N>`` suffix if present (model_id defaults to 0 when there is no
    suffix, so a bare directory-name-shaped id is also accepted)."""
    name = asset_id
    for prefix in _LEDGER_ID_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    match = _MODEL_SUFFIX_RE.match(name)
    if match:
        return match.group(1), int(match.group(2))
    return name, 0


def _latest_isaac_pass(model_entry: dict) -> bool:
    """True iff `model_entry` has a non-stale ``pass`` verification for
    backend=isaacsim under any check (spec §9's "verified" fact is a
    best-effort note, not check-specific)."""
    from lib import ledger

    return any(
        (v := ledger.latest_verification(model_entry, "isaacsim", check)) is not None
        and v.get("verdict") == "pass"
        for check in ledger.CHECKS
    )


def enrich_from_ledgers(
    package: EnvironmentPackage,
    ledger_dirs: Iterable[str | Path],
) -> tuple[EnvironmentPackage, dict]:
    """Enrich ``package`` with isaacsim USD representations looked up from
    per-asset ledgers (spec §9), instead of a caller-supplied ``usd_lookup``.

    For every asset, its ``asset_id`` is parsed back into a ledger directory
    name + model_id (see ``_parse_ledger_asset_id``); ``ledger_dirs`` is
    searched in order for ``<dir>/<name>/ledger.json`` (first match wins --
    e.g. upstream ledgers before the external pool). If found, the matching
    model's non-snapshot ``backend=isaacsim`` representation is registered
    via the same attach + scale-neutralize mechanism as ``enrich_isaac_usd``
    (``_apply_isaac_representations``). If that model's latest isaacsim
    verification (any check) is a non-stale ``pass``, the registered
    representation's metadata gets ``verified: True`` -- absence of
    verification never blocks registration, only the compile step downstream
    cares about verification.

    An asset with no ledger anywhere, no matching model, or no isaacsim
    representation is left untouched (same honest-report-the-gap behavior as
    ``enrich_isaac_usd``) and classified accordingly. A found representation
    whose ``uri`` does not exist on disk is also left unregistered, but
    reported as a ``warnings`` entry rather than silently dropped.

    Returns ``(enriched_package, report)`` where ``report`` is
    ``{"enriched": [...], "skipped_no_ledger": [...],
    "skipped_no_isaac_rep": [...], "warnings": [...]}`` (lists of
    ``asset_id``, except ``warnings`` which is free-text) -- the caller can
    use this to judge coverage before compiling.
    """
    enrichment: dict[str, AssetRepresentation] = {}
    report: dict[str, list[str]] = {
        "enriched": [],
        "skipped_no_ledger": [],
        "skipped_no_isaac_rep": [],
        "warnings": [],
    }

    for asset in package.assets:
        # v3 junction rule: the importer preserves the original env-gen
        # identity in AssetBundle.source; that is the lookup key. The string
        # parse of asset_id is only a fallback -- the sanitizer prefixes
        # numeric-leading ids with "asset_", which silently zeroed ledger
        # coverage until E2 (skipped_no_ledger on the first real external
        # asset, 2026-08-15).
        src_aid = (asset.source or {}).get("asset_id")
        src_mid = (asset.source or {}).get("model_id")
        if src_aid:
            dir_name, model_id = str(src_aid), int(src_mid or 0)
        else:
            dir_name, model_id = _parse_ledger_asset_id(asset.asset_id)

        ledger_data = None
        for ledger_dir in ledger_dirs:
            candidate = Path(ledger_dir) / dir_name / "ledger.json"
            if candidate.is_file():
                ledger_data = json.loads(candidate.read_text())
                break
        if ledger_data is None:
            report["skipped_no_ledger"].append(asset.asset_id)
            continue

        model_entry = next(
            (m for m in ledger_data.get("models", []) if m.get("model_id") == model_id),
            None,
        )
        isaac_rep = None
        if model_entry is not None:
            isaac_rep = next(
                (
                    r
                    for r in model_entry.get("representations", [])
                    if r.get("backend") == "isaacsim" and r.get("role") != "snapshot"
                ),
                None,
            )
        if isaac_rep is None:
            report["skipped_no_isaac_rep"].append(asset.asset_id)
            continue

        uri = isaac_rep.get("uri")
        if not uri or not Path(uri).is_file():
            report["warnings"].append(
                f"{asset.asset_id}: isaacsim representation uri not found: {uri!r}"
            )
            continue

        metadata = dict(isaac_rep.get("metadata") or {})
        if _latest_isaac_pass(model_entry):
            metadata["verified"] = True

        enrichment[asset.asset_id] = AssetRepresentation(
            format=isaac_rep.get("format") or "usd",
            uri=uri,
            backend="isaacsim",
            role=isaac_rep.get("role") or "visual_and_collision",
            sha256=isaac_rep.get("sha256") or "",
            size_bytes=isaac_rep.get("size_bytes") or 0,
            metadata=metadata,
        )
        report["enriched"].append(asset.asset_id)

    enriched_package = _apply_isaac_representations(package, enrichment)
    return enriched_package, report
