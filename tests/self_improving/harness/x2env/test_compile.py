"""Compiler consumes real immutable normalized bytes, never acquires assets."""

import json

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.contracts import SceneIR
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


@pytest.mark.parametrize("fault", [None, "dimensions", "scene_binding", "missing_asset", "inside"])
def test_compile_preserves_layout_and_copies_bound_asset_closure(tmp_path, fault):
    from self_improving.harness.x2env.compile import (
        ResolvedAsset,
        ResolvedAssetSet,
        StructuralPolicy,
        compile_scene,
    )

    store = Store(tmp_path / "state")
    source = tmp_path / "box.glb"
    trimesh.creation.box().export(source)
    root = tmp_path / "normalized"
    report = normalize_mesh(
        source, root, dimensions_m=(0.08, 0.08, 0.08), up_axis="Z", mass_kg=0.1, friction=0.4
    )
    report_ref = store.write_artifact(json.dumps(report).encode(), "application/json")
    evidence = store.write_artifact(b"fixture only", "text/plain")
    registry = AssetRegistry(store)
    version = registry.register(
        "box",
        "box",
        root,
        "asset.urdf",
        files=tuple(f["path"] for f in report["files"]),
        normalization_report=report_ref,
        license=AssetLicense(
            spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=evidence
        ),
        source=AssetSource(kind="local", provider="fixture", source_ref="test", evidence=evidence),
        receipt=evidence,
    )
    field_provenance = {
        key: [{"source": "text", "input_sha256": "a" * 64, "kind": "explicit"}]
        for key in ("category", "color", "dimensions", "material", "pose", "articulation_state")
    }

    def entity(id, category, role, dimensions, frame, position, yaw):
        return {
            "id": id,
            "category": category,
            "role": role,
            "color": None,
            "material": None,
            "articulation_state": None,
            "dimensions": dimensions,
            "pose": {"frame": frame, "position": position, "yaw_degrees": yaw},
            "provenance": field_provenance,
        }

    scene = SceneIR.model_validate_json(
        json.dumps(
            {
                "revision": 0,
                "input_sha256": "b" * 64,
                "entities": [
                    entity(
                        "table",
                        "table",
                        "structural_support",
                        [0.9, 0.7, None],
                        "world",
                        [None] * 3,
                        0,
                    ),
                    entity("box", "box", "foreground", [0.08] * 3, "table", [0.1, -0.08, None], 30),
                ],
                "relations": [
                    {
                        "source": "box",
                        "target": "table",
                        "relation": "on",
                        "provenance": field_provenance["pose"],
                    }
                ],
            }
        )
    )
    scene_ref = store.write_artifact(scene.model_dump_json().encode(), "application/json")
    assets = ResolvedAssetSet(
        scene_ir=scene_ref,
        assets=(
            ResolvedAsset(
                entity_id="box",
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            ),
        ),
    )
    output = tmp_path / "compiled"
    if fault in {"dimensions", "inside"}:
        document = scene.model_dump(mode="json")
        if fault == "dimensions":
            document["entities"][1]["dimensions"][0] = 0.09
        else:
            document["relations"][0]["relation"] = "inside"
        scene = SceneIR.model_validate_json(json.dumps(document))
        scene_ref = store.write_artifact(scene.model_dump_json().encode(), "application/json")
        assets = assets.model_copy(update={"scene_ir": scene_ref})
    if fault == "scene_binding":
        assets = assets.model_copy(update={"scene_ir": evidence})
    if fault == "missing_asset":
        assets = assets.model_copy(update={"assets": ()})
    if fault:
        with pytest.raises(ValueError):
            compile_scene(
                scene_ref,
                assets,
                registry=registry,
                store=store,
                output_root=output,
                policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
                seed=11,
            )
        assert not output.exists()
        return
    compiled = compile_scene(
        scene_ref,
        assets,
        registry=registry,
        store=store,
        output_root=output,
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    by_id = {e.id: e for e in compiled.runtime_scene.entities}
    assert by_id["table"].position_m == pytest.approx((0, 0, 0.73))
    assert by_id["box"].position_m == pytest.approx((0.1, -0.08, 0.75))
    assert by_id["box"].orientation_wxyz == pytest.approx((0.965925826, 0, 0, 0.258819045))
    assert len(compiled.runtime_scene.members) == 4
    assert (output / by_id["box"].urdf_path).is_file()
    assert compiled.physical_evaluated is False
    assert compiled.runtime_scene.seed == 11
    assert scene.entities[0].dimensions[2] is None
    assert compiled.defaults_applied
    document = scene.model_dump(mode="json")
    document["revision"] = 1
    document["entities"][1]["pose"]["position"][0] = 0.2
    revised = SceneIR.model_validate_json(json.dumps(document))
    revised_ref = store.write_artifact(revised.model_dump_json().encode(), "application/json")
    revised_assets = assets.model_copy(update={"scene_ir": revised_ref})
    new = compile_scene(
        revised_ref,
        revised_assets,
        registry=registry,
        store=store,
        output_root=tmp_path / "revised",
        policy=compiled.policy,
        seed=11,
    )
    assert new.runtime_scene.entities[1].position_m[0] == pytest.approx(0.2)
    assert (output / "scene.json").read_bytes() != (
        tmp_path / "revised" / "scene.json"
    ).read_bytes()
    assert registry.inspect(version.version_sha256) == version
