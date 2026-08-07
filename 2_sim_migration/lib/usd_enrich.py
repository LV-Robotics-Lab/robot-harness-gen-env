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

from dataclasses import replace
from pathlib import Path
from typing import Mapping

from agenticsim.openxsim.ir import AssetRepresentation, EnvironmentPackage


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
    enriched_asset_ids: set[str] = set()
    new_assets = []
    for asset in package.assets:
        key = (asset.source.get("asset_id"), asset.source.get("model_id"))
        usd = usd_lookup.get(key)
        if usd and Path(usd).is_file():
            asset = replace(
                asset,
                representations=asset.representations
                + (
                    AssetRepresentation(
                        format="usd",
                        uri=str(usd),
                        backend="isaacsim",
                        role="visual_and_collision",
                    ),
                ),
            )
            enriched_asset_ids.add(asset.asset_id)
        new_assets.append(asset)

    # Neutralize scale on objects whose USD is now pre-baked at that scale (see
    # module docstring): re-applying mesh_scale to a baked USD would double-scale.
    new_objects = tuple(
        replace(obj, scale=(1.0, 1.0, 1.0))
        if obj.asset_id in enriched_asset_ids
        else obj
        for obj in package.env.objects
    )
    return replace(
        package,
        assets=tuple(new_assets),
        env=replace(package.env, objects=new_objects),
    )
