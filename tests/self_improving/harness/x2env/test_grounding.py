"""Public bounded design grounding; executable double, never a real model claim."""

import hashlib
import json

import pytest
from PIL import Image

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import InputMedia, X2EnvRequest
from self_improving.harness.x2env.input import ingest
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_local_catalog import fixture


@pytest.mark.parametrize("fault", [None, "default", "derived", "known_height", "no_media_layout"])
def test_text_structural_design_uses_real_geometry_and_fixed_policy(tmp_path, fault):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.grounding import SceneDesignPolicy, ground_scene

    store, _, _, old_proposal, old_assets = setup(tmp_path, measured=True)
    bundle = ingest(
        X2EnvRequest(
            text="A container on a worktop",
            seed=41,
            idempotency_key="text-design",
            output_dir=str(tmp_path / "out-text"),
        ),
        store,
    )

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    proposal = json.loads(store.read_artifact(old_proposal))
    scene = proposal["proposal"]["scene"]
    scene["input_sha256"] = bundle.request_sha256
    support, obj = scene["entities"]
    support["dimensions"] = [0.8, 0.6, None]
    support["pose"]["position"] = [None, None, None]
    obj["dimensions"] = [0.05, 0.06, 0.07]
    obj["pose"] = {"frame": "support", "position": [-0.15, 0.1, None], "yaw_degrees": 45}
    if fault == "known_height":
        obj["pose"]["position"][2] = 0.2
    if fault == "no_media_layout":
        obj["pose"]["position"][0] = None
    provenance = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
    for entity in scene["entities"]:
        entity["provenance"] = {k: provenance for k in entity["provenance"]}
    scene["relations"][0]["provenance"] = provenance
    unknowns = [
        {
            "field": field,
            "reason_kind": kind,
            "critical": True,
            "reason": "unit missing",
            "provenance": provenance,
        }
        for field, kind in [
            ("scene.entities.support.dimensions[2]", "unspecified"),
            ("scene.entities.support.pose", "unspecified"),
        ]
    ]
    if fault != "known_height":
        unknowns.append(
            {
                "field": "scene.entities.object.pose.position[2]",
                "reason_kind": "pose_unobservable",
                "critical": True,
                "reason": "on geometry",
                "provenance": provenance,
            }
        )
    proposal["proposal"]["unknowns"] = unknowns
    assets = json.loads(store.read_artifact(old_assets))
    assets["scene_ir"] = put(scene).model_dump()
    response = {
        "entities": [
            {
                "id": "support",
                "dimensions": [0.8, 0.6, 0.04],
                "frame": "world",
                "position": [0, 0, 0.75],
                "yaw_degrees": 0,
            },
            {
                "id": "object",
                "dimensions": [0.05, 0.06, 0.07],
                "frame": "support",
                "position": [-0.15, 0.1, 0.035],
                "yaw_degrees": 45,
            },
        ]
    }
    if fault == "default":
        response["entities"][0]["position"][0] = 0.1
    if fault == "derived":
        response["entities"][1]["position"][2] = 0.04
    path = executable(tmp_path, response)
    backend = CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store)
    result = ground_scene(
        put(bundle.model_dump(mode="json")),
        put(proposal),
        put(assets),
        SceneDesignPolicy(
            enabled=True,
            structural_defaults_enabled=True,
            world_anchor_xy=(0, 0),
            world_anchor_yaw_degrees=0,
        ),
        structural_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        store=store,
        backend=backend,
        output_root=tmp_path / "text-attempt",
    )
    if fault:
        expected = {
            "default": "grounding_changed_authoritative_design_value",
            "derived": "grounding_changed_authoritative_design_value",
            "known_height": "known_height_conflicts_with_on_geometry",
            "no_media_layout": "grounding_requires_media",
        }[fault]
        assert result.status == "blocked" and result.error_code == expected
    else:
        assert result.status == "completed", result
        assert result.proposed_scene.entities[1].pose.position == (-0.15, 0.1, 0.035)
        receipt = json.loads(store.read_artifact(result.receipt))
        assert receipt["fixed_values"]["object.pose.position[2]"] == 0.035
        assert receipt["structural_policy"]["thickness_m"] == 0.04
        assert receipt["original_unknowns"][0]["critical"]
        assert not receipt["design_plan"]["requires_media"]
        assert (
            json.loads((tmp_path / "text-attempt" / "invocation.json").read_bytes())["media"] == []
        )


def setup(tmp_path, *, measured=False, response_fault=None, conflict=False):

    store, registry, version = fixture(tmp_path)
    if measured:
        import trimesh

        from self_improving.harness.x2env.assets import AssetLicense, AssetSource
        from self_improving.harness.x2env.normalization import normalize_mesh

        source = tmp_path / "fixture.glb"
        trimesh.creation.box().export(source)
        normalized = tmp_path / "measured"
        report = normalize_mesh(
            source,
            normalized,
            dimensions_m=(0.05, 0.06, 0.07),
            up_axis="Z",
            mass_kg=0.1,
            friction=0.5,
        )
        version = registry.register(
            "measured-unit-fixture",
            "container",
            normalized,
            "asset.urdf",
            files=tuple(m["path"] for m in report["files"]),
            normalization_report=store.write_artifact(
                json.dumps(report).encode(), "application/json"
            ),
            license=AssetLicense(
                spdx="CC0-1.0",
                source_url="https://example.org/unit",
                evidence=version.license.evidence,
            ),
            source=AssetSource(
                kind="local",
                provider="unit",
                source_ref="fixture",
                evidence=version.source.evidence,
            ),
            receipt=version.receipt,
        )
    image = tmp_path / "input.png"
    Image.new("RGB", (8, 8), "red").save(image)
    bundle = ingest(
        X2EnvRequest(
            images=(InputMedia(path=str(image)),),
            seed=23,
            idempotency_key="ground",
            output_dir=str(tmp_path / "out"),
        ),
        store,
    )

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    provenance = [
        {
            "source": "image",
            "input_sha256": bundle.images[0].canonical.sha256,
            "kind": "inferred",
            "media_index": 0,
        }
    ]

    def entity(id, category, role):
        return {
            "id": id,
            "category": category,
            "role": role,
            "color": None,
            "dimensions": None,
            "material": None,
            "pose": {"frame": "world", "position": [None, None, None], "yaw_degrees": None},
            "articulation_state": None,
            "provenance": {
                key: provenance
                for key in [
                    "category",
                    "color",
                    "dimensions",
                    "material",
                    "pose",
                    "articulation_state",
                ]
            },
        }

    scene = {
        "revision": 0,
        "input_sha256": bundle.request_sha256,
        "entities": [
            entity("support", "table", "structural_support"),
            entity("object", "container", "foreground"),
        ],
        "relations": [
            {
                "source": "object",
                "relation": "on",
                "target": "support",
                "distance": None,
                "provenance": provenance,
            }
        ],
    }
    scene["entities"][0]["pose"]["position"][0] = 0.1
    proposal = put(
        {
            "status": "completed",
            "proposal": {
                "scene": scene,
                "unknowns": [
                    {
                        "field": "scene.entities[*].dimensions",
                        "reason": "metric scale unavailable",
                        "critical": True,
                        "reason_kind": "conflict" if conflict else "scale_unobservable",
                        "provenance": provenance,
                    }
                ],
            },
            "evidence": [],
            "error_code": None,
            "elapsed_seconds": 0.0,
        }
    )
    scene_ref = put(scene)
    resolved = put(
        {
            "scene_ir": scene_ref.model_dump(),
            "assets": [
                {
                    "entity_id": "object",
                    "version_sha256": version.version_sha256,
                    "acquisition_source": "local",
                    "selection": "exact",
                }
            ],
        }
    )
    bundle_ref = put(bundle.model_dump(mode="json"))
    # Prepared registry fixture has no dimensions metric: failed evidence must not
    # be repaired by inventing a convenient anchor scale.
    response = {
        "entities": [
            {
                "id": "support",
                "dimensions": [0.8, 0.6, 0.04],
                "frame": "world",
                "position": [0.1, 0.0, 0.75],
                "yaw_degrees": 0.0,
            },
            {
                "id": "object",
                "dimensions": [0.05, 0.06, 0.07],
                "frame": "world",
                "position": [0.02, -0.03, 0.035],
                "yaw_degrees": 17.0,
            },
        ]
    }
    if response_fault == "explicit":
        response["entities"][0]["position"][0] = 0.2
    if response_fault == "anchor":
        response["entities"][1]["dimensions"][0] = 0.1
    exe = executable(tmp_path, response)
    backend = CodexBackend(
        exe, hashlib.sha256(exe.read_bytes()).hexdigest(), "external-double", store
    )
    return store, backend, bundle_ref, proposal, resolved


def test_explicit_design_scale_uses_anchor_geometry_not_recovered_metric_scale(tmp_path):
    from self_improving.harness.x2env.grounding import SceneDesignPolicy, ground_scene

    store, backend, bundle_ref, proposal, resolved = setup(tmp_path)
    denied = ground_scene(
        bundle_ref,
        proposal,
        resolved,
        SceneDesignPolicy(),
        store=store,
        backend=backend,
        output_root=tmp_path / "disabled",
    )
    assert denied.status == "blocked" and denied.error_code == "design_grounding_disabled"
    failed = ground_scene(
        bundle_ref,
        proposal,
        resolved,
        SceneDesignPolicy(enabled=True),
        store=store,
        backend=backend,
        output_root=tmp_path / "attempt",
    )
    assert failed.status == "blocked" and failed.error_code == "missing_measured_anchor_dimensions"
    assert not (tmp_path / "attempt" / "process.json").exists()


def test_measured_anchor_and_media_design_returns_auditable_scene(tmp_path):
    from self_improving.harness.x2env.grounding import SceneDesignPolicy

    store, backend, bundle, proposal, assets = setup(tmp_path, measured=True)
    result = backend.ground_scene(
        bundle, proposal, assets, SceneDesignPolicy(enabled=True), output_root=tmp_path / "attempt"
    )
    assert result.status == "completed", result
    assert result.proposed_scene.entities[1].pose.position == (0.02, -0.03, 0.035)
    assert result.proposed_scene.entities[0].pose.position[0] == 0.1
    assert result.proposed_scene.entities[1].dimensions == (0.05, 0.06, 0.07)
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["original_unknowns"][0]["critical"] is True
    assert receipt["resolved_unknowns"] == [0] and receipt["real_world_scale_recovered"] is False
    assert all(c["normalization_report"] and c["media_selection"] for c in receipt["changes"])
    assert (
        json.loads((tmp_path / "attempt" / "process-terminal.json").read_bytes())["returncode"] == 0
    )


def test_design_cannot_change_explicit_axis_or_anchor_and_conflicts_do_not_call_model(tmp_path):
    from self_improving.harness.x2env.grounding import SceneDesignPolicy, ground_scene

    for fault in ["explicit", "anchor", "conflict"]:
        root = tmp_path / fault
        root.mkdir()
        store, backend, bundle, proposal, assets = setup(
            root, measured=True, response_fault=fault, conflict=fault == "conflict"
        )
        result = ground_scene(
            bundle,
            proposal,
            assets,
            SceneDesignPolicy(enabled=True),
            store=store,
            backend=backend,
            output_root=root / "attempt",
        )
        assert result.status == "blocked" and result.proposed_scene is None
        assert (
            result.error_code
            == {
                "explicit": "grounding_changed_explicit_axis",
                "anchor": "grounding_changed_anchor_scale",
                "conflict": "grounding_requires_clarification",
            }[fault]
        )
        if fault == "conflict":
            assert not (root / "attempt" / "process.json").exists()
