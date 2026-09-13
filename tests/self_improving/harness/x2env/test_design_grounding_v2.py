"""Generated-layout public seams; real meshes/Store, explicit executable model double."""

import hashlib
import json
import sys

import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.compile import ResolvedAsset, ResolvedAssetSet, StructuralPolicy
from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.input import ingest
from self_improving.harness.x2env.normalization import normalize_mesh
from self_improving.harness.x2env.store import Store


@pytest.mark.parametrize("bounds", [(0.05, 0.05), (5.0, 0.05)])
def test_generated_extent_interval_requires_strictly_increasing_bounds(bounds):
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy

    with pytest.raises(ValueError, match="invalid generated extent range"):
        GeneratedLayoutPolicy(enabled=True, support_extent_range_m=bounds)


def setup_v2(tmp_path, mutate=None):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)

    def put(v):
        return store.write_artifact(json.dumps(v).encode(), "application/json")

    request = X2EnvRequest(
        text="Two different boxes on a table; choose a simulation layout.",
        seed=23,
        idempotency_key="v2",
        output_dir=str(tmp_path / "out"),
    )
    bundle = ingest(request, store)
    proof = store.write_artifact(
        b"explicit unit fixture provenance, not production license", "text/plain"
    )
    versions = []
    for i, dimensions in enumerate(((0.05, 0.06, 0.07), (0.1, 0.12, 0.14))):
        source = tmp_path / f"raw-{i}.glb"
        trimesh.creation.box().export(source)
        normalized = tmp_path / f"normalized-{i}"
        report = normalize_mesh(
            source, normalized, dimensions_m=dimensions, up_axis="Z", mass_kg=0.1, friction=0.5
        )
        versions.append(
            registry.register(
                f"fixture-{i}",
                "box",
                normalized,
                "asset.urdf",
                files=tuple(m["path"] for m in report["files"]),
                normalization_report=put(report),
                license=AssetLicense(
                    spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=proof
                ),
                source=AssetSource(
                    kind="web", provider="unit_fixture", source_ref=str(i), evidence=proof
                ),
                receipt=proof,
            )
        )
    provenance = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
    entities = []
    for key, role in [
        ("table", "structural_support"),
        ("small", "foreground"),
        ("large", "foreground"),
    ]:
        entities.append(
            {
                "id": key,
                "role": role,
                "category": "table" if key == "table" else "box",
                "dimensions": None,
                "color": None,
                "material": None,
                "articulation_state": None,
                "pose": {
                    "frame": "world" if key == "table" else "table",
                    "position": [None, None, None],
                    "yaw_degrees": None,
                },
                "provenance": {
                    k: provenance
                    for k in (
                        "category",
                        "dimensions",
                        "color",
                        "material",
                        "pose",
                        "articulation_state",
                    )
                },
            }
        )
    proposal = {
        "scene": {
            "revision": 0,
            "input_sha256": bundle.request_sha256,
            "entities": entities,
            "relations": [
                {
                    "source": key,
                    "target": "table",
                    "relation": "on",
                    "distance": None,
                    "provenance": provenance,
                }
                for key in ("small", "large")
            ],
        },
        "unknowns": [
            {
                "field": "scene.entities[*].pose",
                "critical": False,
                "reason_kind": "unspecified",
                "reason": "generated simulation choice",
                "provenance": provenance,
            }
        ],
    }
    values = {
        "entities": [
            {
                "id": key,
                "dimensions": dimensions,
                "frame": frame,
                "position": position,
                "yaw_degrees": yaw,
            }
            for key, dimensions, frame, position, yaw in [
                ("table", [0.9, 0.7, 0.04], "world", [0, 0, 0.75], 0),
                ("small", [0.05, 0.06, 0.07], "table", [-0.2, 0, 0.035], 15),
                ("large", [0.1, 0.12, 0.14], "table", [0.2, 0, 0.07], -25),
            ]
        ]
    }
    if mutate:
        mutate(proposal, values)
    program = tmp_path / "managed-executable-double"
    program.write_text(
        f"#!{sys.executable}\nimport sys,json,pathlib\nsys.stdin.read()\n"
        'schema=json.loads(pathlib.Path(sys.argv[sys.argv.index("--output-schema")+1]).read_bytes())\n'
        f'answer={proposal!r} if "scene" in schema["properties"] else '
        f'{values!r} if "entities" in schema["properties"] else '
        '{"object":"box","match":True,"colors":[],"materials":[],"confidence":.99,"same_kind":True,"plausible":True,"suggests":None}\n'
        'pathlib.Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps(answer))\n'
        'print(json.dumps({"type":"turn.completed"}))\n'
    )
    program.chmod(0o700)
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "double", store
    )
    original = backend.interpret(bundle, output_root=tmp_path / "interpret", timeout=10)
    assert original.status == "completed", original
    scene_ref = put(original.proposal.scene.model_dump(mode="json"))
    assets = ResolvedAssetSet(
        scene_ir=scene_ref,
        assets=tuple(
            ResolvedAsset(
                entity_id=key,
                version_sha256=version.version_sha256,
                acquisition_source="local",
                selection="exact",
            )
            for key, version in zip(("small", "large"), versions)
        ),
    )
    return (
        store,
        backend,
        request,
        put(bundle.model_dump(mode="json")),
        put(original.model_dump(mode="json")),
        put(assets.model_dump(mode="json")),
    )


@pytest.mark.parametrize("two_supports", [False, True])
def test_three_entity_text_design_uses_each_immutable_asset(tmp_path, two_supports):
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    def extra_support(proposal, values):
        if two_supports:
            support = json.loads(json.dumps(proposal["scene"]["entities"][0]))
            support["id"] = "second_table"
            proposal["scene"]["entities"].append(support)
            proposal["scene"]["entities"][2]["pose"]["frame"] = "second_table"
            proposal["scene"]["relations"][1]["target"] = "second_table"
            values["entities"][2]["frame"] = "second_table"
            values["entities"].append(
                {**values["entities"][0], "id": "second_table", "position": [1.5, 0, 0.75]}
            )

    store, backend, _, bundle, proposal, assets = setup_v2(tmp_path, extra_support)
    result = ground_scene(
        bundle,
        proposal,
        assets,
        GeneratedLayoutPolicy(enabled=True),
        store=store,
        backend=backend,
        output_root=tmp_path / "ground",
        timeout=10,
        structural_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    assert result.status == "completed", result
    assert [e.dimensions for e in result.proposed_scene.entities[:3]] == [
        (0.9, 0.7, 0.04),
        (0.05, 0.06, 0.07),
        (0.1, 0.12, 0.14),
    ]
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["schema_version"] == "x2env.scene_grounding.v2"
    assert len(receipt["asset_bindings"]) == 2
    assert receipt["fixed_values"]["small.pose.position[2]"] == 0.035
    assert receipt["fixed_values"]["large.pose.position[2]"] == 0.07
    assert receipt["media_selection"] is None
    assert receipt["real_world_scale_recovered"] is False
    assert receipt["original_unknowns"][0]["critical"] is False


@pytest.mark.parametrize("compile_configured", [True, False])
def test_generated_layout_harness_commits_three_entity_design(
    tmp_path, monkeypatch, compile_configured
):
    from PIL import Image

    from self_improving.harness.x2env import package_loader
    from self_improving.harness.x2env.deployment import Deployment, build_harness
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy

    def known_dimensions(proposal, values):
        for entity, value in zip(proposal["scene"]["entities"][1:], values["entities"][1:]):
            entity["dimensions"] = value["dimensions"]
        if not compile_configured:
            support = proposal["scene"]["entities"][0]
            support["dimensions"] = values["entities"][0]["dimensions"]
            support["pose"]["position"] = values["entities"][0]["position"]
            support["pose"]["yaw_degrees"] = values["entities"][0]["yaw_degrees"]

    store, backend, request, _, _, _ = setup_v2(tmp_path, known_dimensions)

    def external_runtime(payload, **kwargs):
        if payload["entities"][0]["id"] != "candidate":
            return {
                "status": "failed",
                "simulator_executed": False,
                "error_code": "explicit_runtime_double_stop",
                "wall_seconds": 0.0,
            }
        output = kwargs["output"]
        output.mkdir()
        Image.new("RGB", (4, 4), "red").save(output / "preview.png")
        return {
            "status": "passed",
            "simulator_executed": True,
            "wall_seconds": 0.0,
            "media": {
                "frames": [
                    {
                        "path": "preview.png",
                        "png_sha256": hashlib.sha256(
                            (output / "preview.png").read_bytes()
                        ).hexdigest(),
                    }
                ]
            },
        }

    monkeypatch.setattr(package_loader, "launch_child", external_runtime)
    harness = build_harness(
        Deployment(
            state_dir=str(tmp_path / "state"),
            codex={"executable": str(backend.executable), "sha256": backend.executable_sha},
            scene_design_policy=GeneratedLayoutPolicy(enabled=True),
            compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
            if compile_configured
            else None,
            genesis={
                "runtime_roots": {
                    k: str(tmp_path / k)
                    for k in ("interpreter", "stdlib", "distributions", "genesis", "native")
                },
                "denied_roots": (str(tmp_path / "denied"),),
            },
        )
    )
    handle = harness.submit(request)
    result = harness.resume(handle.workflow_id, timeout=30)
    ground_operation = next(op for op in result.operations if op.capability == "codex.ground")
    authorizations = [
        json.loads(store.read_artifact(ref))
        for ref in ground_operation.result.outputs
        if json.loads(store.read_artifact(ref)).get("schema_version")
        == "x2env.design_authorization.v2"
    ]
    assert len(authorizations) == 1
    assert authorizations[0]["operation_id"] == ground_operation.operation_id
    assert authorizations[0]["workflow_id"] == result.workflow_id
    assert authorizations[0]["policy"]["mode"] == "generated_layout"
    if not compile_configured:
        assert result.status == "blocked" and result.stop_reason == "missing_structural_policy"
        assert result.scene_ir is None and result.compiled_scene is None
        assert not (
            tmp_path / "state" / "attempts" / result.workflow_id / ground_operation.operation_id
        ).exists()
        return
    assert result.scene_ir and result.compiled_scene, result
    assert result.pending_scene_ir is None
    assert any(
        op.capability == "codex.ground" and op.status == "succeeded" for op in result.operations
    )
    assert (
        json.loads(store.read_artifact(result.grounding))["schema_version"]
        == "x2env.scene_grounding.v2"
    )


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("wrong_asset", "grounding_changed_authoritative_design_value"),
        ("wrong_z", "grounding_changed_authoritative_design_value"),
        ("changed_known", "grounding_changed_authoritative_design_value"),
        ("changed_frame", "grounding_changed_frame"),
        ("duplicate_id", "grounding_changed_entities"),
        ("extent", "generated_extent_out_of_bounds"),
        ("position", "generated_position_out_of_bounds"),
        ("dynamic_target", "unsupported_dynamic_support_geometry"),
        ("world_unknown_z", "unsupported_on_coordinate_frame"),
        ("conflict", "grounding_requires_clarification"),
        ("missing_identity", "grounding_unknown_field_not_designable"),
        ("unbounded_parent_path", "grounding_unknown_field_not_designable"),
        ("known_height", "known_height_conflicts_with_on_geometry"),
    ],
)
def test_generated_design_rejects_unauthorized_numeric_or_semantic_changes(
    tmp_path, fault, expected
):
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    def attack(proposal, values):
        small = proposal["scene"]["entities"][1]
        output = values["entities"][1]
        if fault == "wrong_asset":
            values["entities"][2]["dimensions"] = output["dimensions"]
        elif fault == "wrong_z":
            output["position"][2] = 0.2
        elif fault == "changed_known":
            small["pose"]["position"][0] = 0.4
        elif fault == "changed_frame":
            output["frame"] = "world"
        elif fault == "duplicate_id":
            values["entities"][2]["id"] = "small"
        elif fault == "extent":
            values["entities"][0]["dimensions"][0] = 6.0
        elif fault == "position":
            output["position"][0] = 6.0
        elif fault == "dynamic_target":
            proposal["scene"]["relations"][0]["target"] = "large"
        elif fault == "world_unknown_z":
            small["pose"]["frame"] = "world"
        elif fault == "conflict":
            proposal["unknowns"][0]["reason_kind"] = "conflict"
        elif fault == "missing_identity":
            proposal["unknowns"][0].update(field="small.identity", critical=True)
        elif fault == "unbounded_parent_path":
            proposal["unknowns"][0].update(field="scene", critical=True)
        elif fault == "known_height":
            small["pose"]["position"][2] = 0.2

    store, backend, _, bundle, proposal, assets = setup_v2(tmp_path, attack)
    result = ground_scene(
        bundle,
        proposal,
        assets,
        GeneratedLayoutPolicy(enabled=True),
        store=store,
        backend=backend,
        output_root=tmp_path / "ground",
        timeout=10,
        structural_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    assert result.status == "blocked" and result.error_code == expected
    assert result.proposed_scene is None


@pytest.mark.parametrize("enabled,structural", [(False, True), (True, False)])
def test_generation_requires_explicit_authority_before_model(tmp_path, enabled, structural):
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    store, backend, _, bundle, proposal, assets = setup_v2(tmp_path)
    result = ground_scene(
        bundle,
        proposal,
        assets,
        GeneratedLayoutPolicy(enabled=enabled),
        store=store,
        backend=backend,
        output_root=tmp_path / "ground",
        timeout=10,
        structural_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
        if structural
        else None,
    )
    assert result.status == "blocked"
    assert not (tmp_path / "ground/invocation.json").exists()


@pytest.mark.parametrize("known_extent", [8.0, 150.0])
def test_generation_bounds_do_not_clamp_known_input_axes(tmp_path, known_extent):
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    def known(proposal, values):
        proposal["scene"]["entities"][0]["dimensions"] = [known_extent, 0.7, None]
        proposal["scene"]["entities"][0]["pose"]["position"][0] = 8.0
        values["entities"][0]["dimensions"][0] = known_extent
        values["entities"][0]["position"][0] = 8.0

    store, backend, _, bundle, proposal, assets = setup_v2(tmp_path, known)
    result = ground_scene(
        bundle,
        proposal,
        assets,
        GeneratedLayoutPolicy(enabled=True),
        store=store,
        backend=backend,
        output_root=tmp_path / "ground",
        timeout=10,
        structural_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    assert result.status == "completed", result
    assert result.proposed_scene.entities[0].dimensions[0] == known_extent
    assert result.proposed_scene.entities[0].pose.position[0] == 8.0
