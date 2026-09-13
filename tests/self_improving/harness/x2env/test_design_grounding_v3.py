"""Measured layout tests: real meshes/CAS and explicit external model doubles only."""

import hashlib
import json

import pytest


def setup_v3(tmp_path):
    from self_improving.harness.x2env.codex import CodexBackend
    from self_improving.harness.x2env.contracts import InputBundle
    from tests.self_improving.harness.x2env.test_design_grounding_v2 import setup_v2

    def dynamic(proposal, values):
        proposal["scene"]["entities"][1]["pose"]["frame"] = "large"
        proposal["scene"]["relations"][0]["target"] = "large"
        for entity, value in zip(proposal["scene"]["entities"][1:], values["entities"][1:]):
            entity["dimensions"] = value["dimensions"]
        choices = []
        for entity in proposal["scene"]["entities"]:
            paths = ["pose.position[0]", "pose.position[1]", "pose.yaw_degrees"]
            if entity["role"] == "structural_support":
                paths += ["dimensions[0]", "dimensions[1]"]
            choices += [
                dict(entity_id=entity["id"], path=p, value=1 if p.startswith("dimensions") else 0)
                for p in paths
            ]
        values.clear()
        values["choices"] = choices

    store, backend, request, bundle, _, assets = setup_v2(tmp_path, dynamic)
    program = backend.executable
    program.write_text(
        program.read_text().replace(
            '"entities" in schema["properties"]', '"choices" in schema["properties"]'
        )
    )
    backend = CodexBackend(
        program, hashlib.sha256(program.read_bytes()).hexdigest(), "gpt-6-astra", store
    )
    original = backend.interpret(
        InputBundle.model_validate_json(store.read_artifact(bundle)),
        output_root=tmp_path / "interpret-v3",
        timeout=10,
    )
    assert original.status == "completed"
    proposal = store.write_artifact(original.model_dump_json().encode(), "application/json")
    return store, backend, request, bundle, proposal, assets


def test_managed_grounding_v3_persists_actual_candidate_and_geometric_proofs(tmp_path):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.contracts import ArtifactRef, SceneIR
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    store, backend, _, bundle, proposal, assets = setup_v3(tmp_path)
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
    assert result.status == "completed", result.error_code
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["schema_version"] == "x2env.scene_grounding.v3"
    candidate = SceneIR.model_validate_json(
        store.read_artifact(ArtifactRef.model_validate(receipt["candidate_scene_ref"]))
    )
    assert candidate.entities[1].pose.position[2] is None
    assert result.proposed_scene.entities[1].pose.position[2] == pytest.approx(0.105)
    assert "support/small.surface.json" in receipt["geometry_proofs"]
    assert all(c["path"] != "pose.position[2]" for c in receipt["design_choices"])
    original_pose_provenance = candidate.entities[1].provenance.pose
    actual = result.proposed_scene.entities[1].provenance.pose
    assert actual[:-1] == original_pose_provenance
    assert actual[-1].kind == "inferred"
    assert "measured support" in actual[-1].note


def test_harness_commits_v3_authorization_and_managed_grounding(tmp_path, monkeypatch):
    from PIL import Image

    from self_improving.harness.x2env import package_loader
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.deployment import Deployment, build_harness
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy

    store, backend, request, *_ = setup_v3(tmp_path)

    def external_runtime(payload, **kwargs):
        if payload["entities"][0]["id"] != "candidate":
            return dict(
                status="failed",
                simulator_executed=False,
                error_code="explicit_runtime_double_stop",
                wall_seconds=0.0,
            )
        output = kwargs["output"]
        output.mkdir()
        Image.new("RGB", (4, 4), "red").save(output / "preview.png")
        return dict(
            status="passed",
            simulator_executed=True,
            wall_seconds=0.0,
            media={
                "frames": [
                    {
                        "path": "preview.png",
                        "png_sha256": hashlib.sha256(
                            (output / "preview.png").read_bytes()
                        ).hexdigest(),
                    }
                ]
            },
        )

    monkeypatch.setattr(package_loader, "launch_child", external_runtime)
    harness = build_harness(
        Deployment(
            state_dir=str(tmp_path / "state"),
            codex={"executable": str(backend.executable), "sha256": backend.executable_sha},
            scene_design_policy=GeneratedLayoutPolicy(enabled=True),
            compile_policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
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
    ground = next(op for op in result.operations if op.capability == "codex.ground")
    assert ground.status == "succeeded", result.stop_reason
    records = [json.loads(store.read_artifact(ref)) for ref in ground.result.outputs]
    authorization = next(
        r for r in records if r.get("schema_version") == "x2env.design_authorization.v3"
    )
    assert authorization["operation_id"] == ground.operation_id
    assert authorization["workflow_id"] == result.workflow_id
    assert (
        json.loads(store.read_artifact(result.grounding))["schema_version"]
        == "x2env.scene_grounding.v3"
    )
    assert result.compiled_scene is not None


def test_version_dispatch_retains_structured_failure_for_missing_original_proposal(tmp_path):
    from self_improving.harness.x2env.contracts import BackendProposal
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene

    store, backend, _, bundle, proposal_ref, assets = setup_v3(tmp_path)
    original = BackendProposal.model_validate_json(store.read_artifact(proposal_ref))
    bad = original.model_copy(
        update={"status": "failed", "proposal": None, "error_code": "fixture_failure"}
    )
    ref = store.write_artifact(bad.model_dump_json().encode(), "application/json")
    result = ground_scene(
        bundle,
        ref,
        assets,
        GeneratedLayoutPolicy(enabled=True),
        store=store,
        backend=backend,
        output_root=tmp_path / "failed-original",
        timeout=10,
    )
    assert result.status == "blocked"
    assert result.error_code == "grounding_requires_original_intent"


def test_measured_candidate_only_fills_authorized_choices_and_leaves_z_for_geometry(tmp_path):
    from self_improving.harness.x2env.compile import ResolvedAssetSet, StructuralPolicy
    from self_improving.harness.x2env.contracts import BackendProposal
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.design_grounding_v3 import (
        MeasuredLayoutValues,
        bind_measured_assets,
        build_measured_candidate,
        classify_measured_design,
    )
    from tests.self_improving.harness.x2env.test_design_grounding_v2 import setup_v2

    def dynamic(proposal, values):
        proposal["scene"]["entities"][1]["pose"]["frame"] = "large"
        proposal["scene"]["relations"][0]["target"] = "large"

    store, backend, request, bundle, proposal_ref, assets_ref = setup_v2(tmp_path, dynamic)
    original = BackendProposal.model_validate_json(store.read_artifact(proposal_ref))
    assets = ResolvedAssetSet.model_validate_json(store.read_artifact(assets_ref))
    policy = GeneratedLayoutPolicy(enabled=True)
    plan = classify_measured_design(
        original.proposal,
        policy,
        StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    bindings, fixed = bind_measured_assets(store, original.proposal.scene, assets, plan)
    choices = [
        {
            "entity_id": rule["entity_id"],
            "path": rule["path"],
            "value": 1 if rule["path"].startswith("dimensions") else 0,
        }
        for rule in plan["rules"]
        if rule["basis"] == "simulation_design_choice"
    ]
    values = MeasuredLayoutValues.model_validate_json(json.dumps({"choices": choices}))
    candidate = build_measured_candidate(original.proposal.scene, values, policy, plan, fixed)
    assert candidate.entities[1].pose.position == (0, 0, None)
    assert candidate.entities[1].dimensions == pytest.approx((0.05, 0.06, 0.07))
    assert "small.pose.position[2]" not in fixed
    assert candidate.relations == original.proposal.scene.relations
    assert len(bindings) == 2
    with pytest.raises(ValueError):
        MeasuredLayoutValues.model_validate_json(
            json.dumps(
                {
                    "choices": [
                        *choices,
                        {"entity_id": "small", "path": "pose.position[2]", "value": 0.1},
                    ]
                }
            )
        )
    from self_improving.harness.x2env.design_grounding_v2 import classify_generated_design

    with pytest.raises(ValueError, match="unsupported_dynamic_support_geometry"):
        classify_generated_design(
            original.proposal,
            policy,
            StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        )


@pytest.mark.parametrize(
    "fault", ["extra_known", "duplicate", "missing", "extent", "position", "yaw"]
)
def test_measured_candidate_rejects_unauthorized_or_out_of_range_choices(tmp_path, fault):
    from self_improving.harness.x2env.compile import ResolvedAssetSet, StructuralPolicy
    from self_improving.harness.x2env.contracts import BackendProposal
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.design_grounding_v3 import (
        MeasuredLayoutValues,
        bind_measured_assets,
        build_measured_candidate,
        classify_measured_design,
    )

    store, _, _, _, proposal_ref, assets_ref = setup_v3(tmp_path)
    proposal = BackendProposal.model_validate_json(store.read_artifact(proposal_ref)).proposal
    policy = GeneratedLayoutPolicy(enabled=True)
    plan = classify_measured_design(
        proposal, policy, StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    )
    _, fixed = bind_measured_assets(
        store,
        proposal.scene,
        ResolvedAssetSet.model_validate_json(store.read_artifact(assets_ref)),
        plan,
    )
    choices = [
        dict(
            entity_id=r["entity_id"],
            path=r["path"],
            value=1 if r["path"].startswith("dimensions") else 0,
        )
        for r in plan["rules"]
        if r["basis"] == "simulation_design_choice"
    ]
    if fault == "extra_known":
        choices.append(dict(entity_id="small", path="dimensions[0]", value=0.05))
    elif fault == "duplicate":
        choices.append(choices[0])
    elif fault == "missing":
        choices.pop()
    else:
        path = {
            "extent": "dimensions[0]",
            "position": "pose.position[0]",
            "yaw": "pose.yaw_degrees",
        }[fault]
        next(c for c in choices if c["path"] == path)["value"] = 10000
    values = MeasuredLayoutValues.model_validate_json(json.dumps({"choices": choices}))
    with pytest.raises(ValueError):
        build_measured_candidate(proposal.scene, values, policy, plan, fixed)
