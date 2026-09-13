"""Compiler consumes real immutable normalized bytes, never acquires assets."""

import json

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.contracts import SceneIR
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


def dynamic_stack_inputs(tmp_path, *, offsets=None, reported_box_dimensions=None):
    """Real registered mesh bytes; synthetic intent, not model/runtime evidence."""
    from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet

    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    versions = {}
    evidence = store.write_artifact(b"synthetic compiler fixture", "text/plain")
    for name, dimensions in (("plate", (0.6, 0.5, 0.1)), ("box", (0.08, 0.08, 0.08))):
        source = tmp_path / f"{name}.glb"
        trimesh.creation.box(extents=dimensions).export(source)
        output = tmp_path / name
        report = normalize_mesh(
            source, output, dimensions_m=dimensions, up_axis="Z", mass_kg=0.2, friction=0.5
        )
        if name == "box" and reported_box_dimensions is not None:
            report["dimensions_m"] = reported_box_dimensions
        if offsets and name in offsets:
            import hashlib
            import xml.etree.ElementTree as ET

            document = ET.parse(output / "asset.urdf")
            for kind in ("visual", "collision"):
                document.find(f"link/{kind}/origin").set("xyz", " ".join(map(str, offsets[name])))
            document.write(output / "asset.urdf")
            # Synthetic authored offset fixture, with actual updated closure bytes.
            for member in report["files"]:
                raw = (output / member["path"]).read_bytes()
                member.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        versions[name] = registry.register(
            name,
            name,
            output,
            "asset.urdf",
            files=tuple(f["path"] for f in report["files"]),
            normalization_report=store.write_artifact(
                json.dumps(report).encode(), "application/json"
            ),
            license=AssetLicense(
                spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=evidence
            ),
            source=AssetSource(
                kind="local", provider="fixture", source_ref="synthetic", evidence=evidence
            ),
            receipt=evidence,
        )
    provenance = [{"source": "text", "input_sha256": "a" * 64, "kind": "explicit"}]

    def entity(name, role, dims, frame, pos):
        return dict(
            id=name,
            category=name,
            role=role,
            color=None,
            material=None,
            articulation_state=None,
            dimensions=dims,
            pose=dict(frame=frame, position=pos, yaw_degrees=0),
            provenance={
                key: provenance
                for key in (
                    "category",
                    "color",
                    "material",
                    "dimensions",
                    "pose",
                    "articulation_state",
                )
            },
        )

    scene = SceneIR.model_validate_json(
        json.dumps(
            dict(
                revision=0,
                input_sha256="a" * 64,
                entities=[
                    entity("box", "foreground", [0.08] * 3, "plate", [0, 0, None]),
                    entity("plate", "foreground", [0.6, 0.5, 0.1], "table", [0, 0, None]),
                    entity("table", "structural_support", [1, 1, 0.04], "world", [0, 0, 0.75]),
                ],
                relations=[
                    dict(source="box", target="plate", relation="on", provenance=provenance),
                    dict(source="plate", target="table", relation="on", provenance=provenance),
                ],
            )
        )
    )
    ref = store.write_artifact(scene.model_dump_json().encode(), "application/json")
    assets = ResolvedAssetSet(
        scene_ir=ref,
        assets=tuple(
            ResolvedAsset(
                entity_id=name,
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            )
            for name, version in versions.items()
        ),
    )
    return store, registry, versions, ref, assets


def test_compile_dynamic_stack_binds_measured_plane_and_keeps_v1_serialization(tmp_path):
    from self_improving.harness.x2env.compile import CompiledScene, StructuralPolicy, compile_scene
    from self_improving.harness.x2env.genesis_runtime import RuntimeScene, parse_runtime_scene

    original = RuntimeScene(
        seed=1,
        scene_ir_sha256="a" * 64,
        entities=(
            {
                "id": "table",
                "category": "table",
                "kind": "structural_box",
                "position_m": [0, 0, 0],
                "orientation_wxyz": [1, 0, 0, 0],
                "size_m": [1, 1, 0.1],
                "friction": 0.5,
            },
        ),
    )
    assert (
        parse_runtime_scene(original.model_dump(mode="json")).model_dump_json()
        == original.model_dump_json()
    )
    assert set(original.model_dump()) == {
        "schema_version",
        "seed",
        "scene_ir_sha256",
        "entities",
        "relations",
        "members",
    }
    store, registry, versions, ref, assets = dynamic_stack_inputs(tmp_path)
    compiled = compile_scene(
        ref,
        assets,
        registry=registry,
        store=store,
        output_root=tmp_path / "compiled",
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    runtime = compiled.runtime_scene
    assert runtime.schema_version == "x2env.runtime_scene.v2"
    assert CompiledScene.model_validate_json(compiled.model_dump_json()) == compiled
    by_id = {e.id: e for e in runtime.entities}
    assert by_id["plate"].position_m[2] == pytest.approx(0.75)
    assert by_id["box"].position_m[2] == pytest.approx(0.85)
    edge = next(b for b in runtime.support_bindings if b.source_id == "box")
    assert edge.target_version_sha256 == versions["plate"].version_sha256
    proof = json.loads((tmp_path / "compiled" / edge.surface_path).read_bytes())
    assert proof["plane_z_m"] == pytest.approx(0.1)
    assert not proof["physical_evaluated"]
    assert {b.source_id for b in runtime.support_bindings} == {"box", "plate"}
    assert edge.selection_receipt_path in {m.path for m in runtime.members}


def test_measured_layout_resolution_is_read_only_and_compile_uses_same_geometry(tmp_path):
    import hashlib

    from self_improving.harness.x2env.compile import (
        StructuralPolicy,
        compile_scene,
        resolve_measured_layout,
    )

    store, registry, versions, ref, assets = dynamic_stack_inputs(tmp_path)

    def inventory():
        return {
            str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = inventory()
    policy = StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    resolved = resolve_measured_layout(
        ref, assets, registry=registry, store=store, policy=policy, seed=11
    )
    assert inventory() == before
    by_id = {e.id: e for e in resolved["scene"].entities}
    assert by_id["box"].pose.position == pytest.approx((0, 0, 0.09))
    assert by_id["plate"].pose.position == pytest.approx((0, 0, 0.05))
    original = SceneIR.model_validate_json(store.read_artifact(ref))
    assert resolved["scene"].relations == original.relations
    assert by_id["box"].provenance == original.entities[0].provenance
    compiled = compile_scene(
        ref,
        assets,
        registry=registry,
        store=store,
        output_root=tmp_path / "compiled",
        policy=policy,
        seed=11,
    )
    assert resolved["runtime_scene"] == compiled.runtime_scene
    for path, raw in resolved["support_proofs"].items():
        assert (tmp_path / "compiled" / path).read_bytes() == raw


def test_dynamic_layout_rejects_report_dimensions_not_measured_in_actual_mesh(tmp_path):
    from self_improving.harness.x2env.compile import StructuralPolicy, resolve_measured_layout

    store, registry, versions, ref, assets = dynamic_stack_inputs(
        tmp_path, reported_box_dimensions=[0.2, 0.2, 0.2]
    )
    document = json.loads(store.read_artifact(ref))
    document["entities"][0]["dimensions"] = None
    ref = store.write_artifact(json.dumps(document).encode(), "application/json")
    assets = assets.model_copy(update={"scene_ir": ref})
    with pytest.raises(ValueError, match="dimensions disagree with authored geometry"):
        resolve_measured_layout(
            ref,
            assets,
            registry=registry,
            store=store,
            policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
            seed=11,
        )


@pytest.mark.parametrize(
    "fault", [None, "known_z", "world_unknown_z", "unknown_xy", "multiple_target", "combined_cycle"]
)
def test_dynamic_compile_preserves_known_axes_and_rejects_invalid_graph(tmp_path, fault):
    from self_improving.harness.x2env.compile import StructuralPolicy, compile_scene

    store, registry, versions, ref, assets = dynamic_stack_inputs(
        tmp_path, offsets={"plate": (0.2, -0.1, 0.3), "box": (-0.05, 0.03, -0.02)}
    )
    scene = json.loads(store.read_artifact(ref))
    box, plate = scene["entities"][:2]
    plate["pose"]["yaw_degrees"] = 90
    if fault == "known_z":
        box["pose"]["position"][2] = 0.9
    elif fault == "world_unknown_z":
        box["pose"]["frame"] = "world"
    elif fault == "unknown_xy":
        box["pose"]["position"][0] = None
    elif fault == "multiple_target":
        scene["relations"].append({**scene["relations"][0], "target": "table"})
    elif fault == "combined_cycle":
        plate["pose"]["frame"] = "box"
    ref = store.write_artifact(json.dumps(scene).encode(), "application/json")
    assets = assets.model_copy(update={"scene_ir": ref})
    args = dict(
        registry=registry,
        store=store,
        output_root=tmp_path / "compiled",
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        seed=11,
    )
    if fault:
        with pytest.raises(ValueError):
            compile_scene(ref, assets, **args)
        assert not (tmp_path / "compiled").exists()
    else:
        result = compile_scene(ref, assets, **args)
        entities = {e.id: e for e in result.runtime_scene.entities}
        # Scene centres are at XY=0. Rotated URDF offsets are not geometry centres.
        assert entities["plate"].position_m == pytest.approx((-0.1, -0.2, 0.45))
        assert entities["box"].position_m == pytest.approx((0.03, 0.05, 0.87))
        proof = json.loads((tmp_path / "compiled/support/box.surface.json").read_bytes())
        assert proof["plane_z_m"] == pytest.approx(0.4)


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
        "unresolved_support_x",
        "unresolved_support_y",
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
        "unresolved_support_x",
        "unresolved_support_y",
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
        elif fault in {"unresolved_support_x", "unresolved_support_y"}:
            axis = 0 if fault == "unresolved_support_x" else 1
            document["entities"][1]["pose"]["position"][axis] = None
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
