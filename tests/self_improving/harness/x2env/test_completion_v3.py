"""Public completion attacks; real managed-transport double, deliberately no replay."""

import json

import pytest


def pending_completion(tmp_path, fault):
    from self_improving.harness.x2env.assets import AssetRegistry
    from self_improving.harness.x2env.compile import (
        ResolvedAssetSet,
        StructuralPolicy,
        compile_scene,
    )
    from self_improving.harness.x2env.contracts import ToolResult
    from self_improving.harness.x2env.design_grounding_v2 import GeneratedLayoutPolicy
    from self_improving.harness.x2env.grounding import ground_scene
    from tests.self_improving.harness.x2env.test_design_grounding_v3 import setup_v3

    store, backend, request, bundle, proposal, asset_ref = setup_v3(tmp_path)

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    assets = ResolvedAssetSet.model_validate_json(store.read_artifact(asset_ref))
    policy = GeneratedLayoutPolicy(enabled=True)
    structural = StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    grounded = ground_scene(
        bundle,
        proposal,
        asset_ref,
        policy,
        store=store,
        backend=backend,
        output_root=tmp_path / "ground",
        timeout=10,
        structural_policy=structural,
    )
    assert grounded.status == "completed", grounded.error_code
    scene_ref = store.write_artifact(
        grounded.proposed_scene.model_dump_json().encode(), "application/json"
    )
    rebound = assets.model_copy(update={"scene_ir": scene_ref})
    rebound_ref = put(rebound.model_dump(mode="json"))
    compiled = compile_scene(
        scene_ref,
        rebound,
        registry=AssetRegistry(store),
        store=store,
        output_root=tmp_path / "compiled",
        policy=structural,
        seed=request.seed,
    )
    compiled_ref = put(compiled.model_dump(mode="json"))
    receipt = json.loads(store.read_artifact(grounded.receipt))
    placeholder = put({"not_run": "explicitly no replay/observation/diagnosis/validation fixture"})
    snapshot = store.claim(store.submit(request).workflow_id)
    stages = [
        ("ingest", (bundle,), {}),
        (
            "codex.interpret",
            (proposal, assets.scene_ir),
            dict(proposal=proposal, pending_scene_ir=assets.scene_ir),
        ),
        ("asset.resolve", (asset_ref,), dict(resolved_assets=asset_ref)),
        ("codex.ground", (), {}),
        ("x2env.compile", (compiled_ref,), dict(compiled_scene=compiled_ref)),
        ("x2env.replay", (placeholder,), dict(replay_result=placeholder)),
        ("observe", (placeholder,), dict(observation=placeholder)),
        ("codex.diagnose", (placeholder,), dict(diagnosis=placeholder)),
        ("x2env.validate", (placeholder,), dict(validation=placeholder)),
    ]
    for index, (capability, outputs, fields) in enumerate(stages):
        if index:
            snapshot = store.begin_operation(snapshot, capability)
        operation = snapshot.operations[-1]
        if capability == "codex.ground":
            auth = dict(
                schema_version="x2env.design_authorization.v3",
                workflow_id=snapshot.workflow_id,
                operation_id=operation.operation_id,
                proposal_ref=proposal.model_dump(),
                pending_scene_ref=assets.scene_ir.model_dump(),
                assets_ref=asset_ref.model_dump(),
                policy=policy.model_dump(mode="json"),
                structural_policy=structural.model_dump(mode="json"),
            )
            if fault == "authorization":
                auth["operation_id"] = "forged"
            if fault == "candidate":
                receipt["candidate_scene_ref"] = scene_ref.model_dump()
            if fault == "proofs":
                receipt["geometry_proofs"] = {}
            if fault == "context":
                receipt["fixed_values"] = {}
            if fault == "transport":
                receipt["transport"]["proposal.json"] = put({"choices": []}).model_dump()
            ground_ref = put(receipt)
            outputs = (ground_ref, scene_ref, rebound_ref, put(auth))
            fields = dict(grounding=ground_ref, scene_ir=scene_ref, resolved_assets=rebound_ref)
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(operation_id=operation.operation_id, status="succeeded", outputs=outputs),
            bundle,
            status="active",
            **fields,
        )
    return store, snapshot


@pytest.mark.parametrize(
    "fault,expected",
    [
        (None, "ReplayResult"),
        ("authorization", "completion_grounding_authorization_mismatch"),
        ("candidate", "completion_grounding_candidate_mismatch"),
        ("proofs", "completion_grounding_geometry_proof_mismatch"),
        ("context", "completion_grounding_design_recomputation_mismatch"),
        ("transport", "completion_grounding_transport_unbound"),
    ],
)
def test_completion_recomputes_v3_then_refuses_absent_runtime(tmp_path, fault, expected):
    from self_improving.harness.x2env.completion import materialize_completion

    store, snapshot = pending_completion(tmp_path, fault)
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    assert result.status == "failed"
    assert expected in result.error_code, result.error_code
    assert result.package_path is None
