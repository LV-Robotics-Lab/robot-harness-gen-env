"""Placement-convention inheritance and sizing policy for external assets.

Pure stdlib so both conda envs (isaac-smoke py3.11 / env-gen-yuxin py3.10) can
import it.

Two deliberate design rules:
- is_static / z_policy / footprint_shape are CATEGORY semantics -> inheritable
  from an existing same-category exemplar in the reference catalog.
- stable_orientation is ASSET-GEOMETRY semantics (depends on the mesh's own
  axis convention) -> NEVER inherited; the import pipeline sets it from the
  normalization it applied (rigid normalized-to-Y-up -> X+90, URDF Z-up ->
  identity).
"""

import json
import statistics
from pathlib import Path

CONSERVATIVE_DEFAULTS = {
    "is_static": False,
    "z_policy": "origin_on_table",
    "footprint_shape": "box",
    "precedent": None,
    "note": "no same-category precedent; conventions unverified",
}

X90_WXYZ = [0.7071067811865476, 0.7071067811865476, 0.0, 0.0]
IDENTITY_WXYZ = [1.0, 0.0, 0.0, 0.0]


def _load(catalog):
    if isinstance(catalog, (str, Path)):
        return json.loads(Path(catalog).read_text())
    return catalog


def _same_category_usable(category, catalog, exclude_external=True):
    out = []
    for e in _load(catalog)["entries"]:
        if e.get("category") != category:
            continue
        aid = e["asset_id"]
        if exclude_external and aid[:3].isdigit() and int(aid[:3]) >= 300:
            continue
        models = [m for m in e.get("models", []) if m.get("usable")]
        if models:
            out.append((e, models))
    return sorted(out, key=lambda pair: pair[0]["asset_id"])


def orientation_for_kind(kind):
    """kind: 'rigid_yup' (import pipeline normalizes meshes to Y-up) or
    'urdf_zup' (exported URDFs are already Z-up upright)."""
    return X90_WXYZ if kind == "rigid_yup" else IDENTITY_WXYZ


def inherit_conventions(category, reference_catalog):
    """Placement conventions from the first same-category usable exemplar."""
    found = _same_category_usable(category, reference_catalog)
    if not found:
        return dict(CONSERVATIVE_DEFAULTS)
    entry, models = found[0]
    m = models[0]
    return {
        "is_static": bool(m.get("is_static", False)),
        "z_policy": m.get("z_policy") or "origin_on_table",
        "footprint_shape": m.get("footprint_shape") or "box",
        "precedent": entry["asset_id"],
        "note": f"inherited from {entry['asset_id']}",
    }


def resolve_size(
    category,
    bbox_m,
    reference_catalog,
    policy="match_category",
    tolerance=(0.6, 1.6),
    workspace_cap_m=0.5,
):
    """Sizing decision for one asset. Returns a dict with:
    scale (uniform factor to apply), mode, actual/reference max dims,
    reference_assets, and verdict ('ok' | 'scaled' | 'no_precedent' |
    'no_precedent_oversized')."""
    actual = float(max(bbox_m))
    result = {
        "mode": policy,
        "actual_max_dim_m": actual,
        "scale": 1.0,
        "reference_max_dim_m": None,
        "reference_assets": [],
        "verdict": "ok",
    }
    if policy in (None, "none"):
        return result
    if isinstance(policy, str) and policy.startswith("absolute:"):
        target = float(policy.split(":", 1)[1])
        result["reference_max_dim_m"] = target
        if abs(actual - target) / target > 0.05:
            result["scale"] = target / actual
            result["verdict"] = "scaled"
        return result
    # match_category
    found = _same_category_usable(category, reference_catalog)
    dims = []
    for entry, models in found:
        for m in models:
            d = m.get("dimensions_m")
            if d:
                dims.append((entry["asset_id"], max(float(v) for v in d)))
    if not dims:
        result["verdict"] = (
            "no_precedent_oversized" if actual > workspace_cap_m else "no_precedent"
        )
        return result
    ref = statistics.median(v for _, v in dims)
    result["reference_max_dim_m"] = ref
    result["reference_assets"] = [a for a, _ in dims]
    ratio = actual / ref
    if tolerance[0] <= ratio <= tolerance[1]:
        return result
    result["scale"] = ref / actual
    result["verdict"] = "scaled"
    return result
