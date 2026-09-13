"""Compiler consumes real immutable normalized bytes, never acquires assets."""

import json

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.contracts import SceneIR
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


def test_explicit_structural_geometry_does_not_claim_deployment_defaults(tmp_path):
    import copy

    from self_improving.harness.x2env.compile import (
        ResolvedAsset,
        ResolvedAssetSet,
        StructuralPolicy,
        compile_scene,
    )
    from tests.self_improving.harness.x2env.test_resolver import inputs

    store, registry, version, ref, _ = inputs(tmp_path)
    scene = SceneIR.model_validate_json(store.read_artifact(ref)).model_dump(mode="json")
    support = copy.deepcopy(scene["entities"][0])
    support.update(id="table", category="table", role="structural_support")
    scene["entities"].append(support)
    support["dimensions"] = [0.9, 0.7, 0.03]
    support["pose"]["position"] = [0.2, 0.3, 0.6]
    support["pose"]["yaw_degrees"] = 0
    ref = store.write_artifact(
        SceneIR.model_validate_json(json.dumps(scene)).model_dump_json().encode(),
        "application/json",
    )
    result = compile_scene(
        ref,
        ResolvedAssetSet(
            scene_ir=ref,
            assets=(
                ResolvedAsset(
                    entity_id="box",
                    version_sha256=version.version_sha256,
                    acquisition_source="local",
                    selection="exact",
                ),
            ),
        ),
        registry=registry,
        store=store,
        output_root=tmp_path / "compiled",
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    actual = next(e for e in result.runtime_scene.entities if e.id == support["id"])
    assert actual.size_m == (0.9, 0.7, 0.03)
    assert actual.position_m == pytest.approx((0.2, 0.3, 0.585))
    assert not any("deployment.structural_policy" in entry for entry in result.defaults_applied)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "dimensions",
        "scene_binding",
        "missing_asset",
        "inside",
        "yaw",
        "articulation",
        "relative_root",
        "symbolic_root",
        "normalized_dimensions",
        "missing_physics",
        "structural_frame",
        "structural_width",
        "multiple_support",
        "unresolved_position",
    ],
)
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
    if fault == "normalized_dimensions":
        report["dimensions_m"] = [0, 0.08, 0.08]
    if fault == "missing_physics":
        report["files"] = [row for row in report["files"] if row["path"] != "physics.json"]
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
    if fault in {
        "dimensions",
        "inside",
        "yaw",
        "articulation",
        "structural_frame",
        "structural_width",
        "multiple_support",
        "unresolved_position",
    }:
        document = scene.model_dump(mode="json")
        if fault == "dimensions":
            document["entities"][1]["dimensions"][0] = 0.09
        elif fault == "inside":
            document["relations"][0]["relation"] = "inside"
        elif fault == "yaw":
            document["entities"][1]["pose"]["yaw_degrees"] = None
        elif fault == "articulation":
            document["entities"][1]["articulation_state"] = {"state": "open"}
        elif fault == "structural_frame":
            document["entities"][0]["pose"]["frame"] = "box"
            document["entities"][1]["pose"]["frame"] = "world"
            document["entities"][1]["pose"]["position"] = [0, 0, 0.1]
            document["relations"] = []
        elif fault == "structural_width":
            document["entities"][0]["dimensions"][0] = None
        elif fault == "multiple_support":
            document["relations"] *= 2
        else:
            document["relations"] = []
        scene = SceneIR.model_validate_json(json.dumps(document))
        scene_ref = store.write_artifact(scene.model_dump_json().encode(), "application/json")
        assets = assets.model_copy(update={"scene_ir": scene_ref})
    if fault == "scene_binding":
        assets = assets.model_copy(update={"scene_ir": evidence})
    if fault == "missing_asset":
        assets = assets.model_copy(update={"assets": ()})
    if fault == "relative_root":
        from pathlib import Path

        output = Path("must-not-materialize-relative-compile-root")
    if fault == "symbolic_root":
        link = tmp_path / "symbolic"
        link.symlink_to(root, target_is_directory=True)
        output = link / "output"
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
