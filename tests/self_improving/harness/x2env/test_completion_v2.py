"""Public completion audit with explicit external model/runtime doubles, not qualification."""

import json

import pytest

from self_improving.harness.x2env.completion import materialize_completion
from tests.self_improving.harness.x2env.test_completion import completed_fixture


def replay_external_producer(tmp_path, mutate):
    """Replay an explicit synthetic producer via public Store, never patch verifier internals.

    Copies real CAS bytes into an independent Store and uses its actual operation IDs.
    The inherited short transport process is a test executable, not a real Codex run.
    """
    from self_improving.harness.x2env.artifacts import artifact_closure
    from self_improving.harness.x2env.assets import AssetRegistry
    from self_improving.harness.x2env.compile import ResolvedAssetSet
    from self_improving.harness.x2env.contracts import ArtifactRef, ToolResult
    from self_improving.harness.x2env.store import Store

    source_root = tmp_path / "producer"
    source_root.mkdir()
    source, prior = completed_fixture(
        source_root, grounding=True, structural_grounding=True, generated_grounding=True
    )
    store = Store(tmp_path / "replayed-producer")
    receipt = json.loads(source.read_artifact(prior.grounding))
    initial = ArtifactRef.model_validate(receipt["assets_ref"])
    assets = ResolvedAssetSet.model_validate_json(source.read_artifact(initial))
    roots = [ref for operation in prior.operations for ref in operation.result.outputs]
    for asset in assets.assets:
        version = AssetRegistry(source).inspect(asset.version_sha256)
        record = source.asset_version(version.version_sha256)
        roots.append(source.write_artifact(record.encode(), "application/json"))
        store.register_asset(version.version_sha256, version.asset_id, version.category, record)
    for ref in artifact_closure(source, roots):
        assert store.write_artifact(source.read_artifact(ref), ref.media_type) == ref

    def put(value):
        return store.write_artifact(json.dumps(value).encode(), "application/json")

    snapshot = store.claim(store.submit(prior.request).workflow_id)
    fields = {
        "ingest": {},
        "codex.interpret": {"proposal": prior.proposal, "pending_scene_ir": assets.scene_ir},
        "asset.resolve": {"resolved_assets": initial},
        "x2env.compile": {"compiled_scene": prior.compiled_scene},
        "x2env.replay": {"replay_result": prior.replay_result},
        "observe": {"observation": prior.observation},
        "codex.diagnose": {"diagnosis": prior.diagnosis},
        "x2env.validate": {"validation": prior.validation},
    }
    for index, old in enumerate(prior.operations):
        if index:
            snapshot = store.begin_operation(snapshot, old.capability)
        current = snapshot.operations[-1]
        outputs = old.result.outputs
        if old.capability == "codex.ground":
            authorization = {
                "schema_version": "x2env.design_authorization.v2",
                "workflow_id": snapshot.workflow_id,
                "operation_id": current.operation_id,
                "proposal_ref": prior.proposal.model_dump(),
                "pending_scene_ref": assets.scene_ir.model_dump(),
                "assets_ref": initial.model_dump(),
                "policy": receipt["policy"],
                "structural_policy": receipt["structural_policy"],
            }
            authorization = json.loads(json.dumps(authorization))
            mutate(store, receipt, authorization, put)
            ref = put(receipt)
            fields[old.capability] = {
                "scene_ir": prior.scene_ir,
                "resolved_assets": prior.resolved_assets,
                "grounding": ref,
            }
            outputs = (ref, prior.scene_ir, prior.resolved_assets, put(authorization))
        snapshot = store.complete_operation(
            snapshot,
            ToolResult(operation_id=current.operation_id, status="succeeded", outputs=outputs),
            prior.input_bundle,
            status="active",
            **fields[old.capability],
        )
    return store, snapshot


def test_independent_external_producer_can_complete_with_bound_v2_evidence(tmp_path):
    store, snapshot = replay_external_producer(tmp_path, lambda *args: None)
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    assert result.status == "materialized", result


def test_verified_context_bytes_keep_json_key_order_without_reconstructing_prompt(tmp_path):
    from self_improving.harness.x2env.contracts import ArtifactRef

    def mutate(store, receipt, authorization, put):
        for ref in receipt["evidence"]:
            if ref["media_type"] != "application/json":
                continue
            raw = store.read_artifact(ArtifactRef.model_validate(ref))
            value = json.loads(raw)
            if isinstance(value, dict) and value.get("schema_version") == (
                "x2env.generated_layout_context.v2"
            ):
                context_ref, context_raw, context = ref, raw, value
                break
        reordered = json.dumps(context, sort_keys=True).encode()
        replacement = store.write_artifact(reordered, "application/json").model_dump()
        old_prompt = receipt["transport"]["prompt.txt"]
        prompt = store.read_artifact(ArtifactRef.model_validate(old_prompt))
        assert context_raw in prompt
        new_prompt = store.write_artifact(
            prompt.replace(context_raw, reordered), "text/plain"
        ).model_dump()
        receipt["transport"]["prompt.txt"] = new_prompt
        receipt["evidence"] = [
            replacement if ref == context_ref else new_prompt if ref == old_prompt else ref
            for ref in receipt["evidence"]
        ]

    store, snapshot = replay_external_producer(tmp_path, mutate)
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    assert result.status == "materialized", result


@pytest.mark.parametrize("hostile", [False, True])
def test_fixed_router_transport_allowlist_does_not_accept_arbitrary_overrides(tmp_path, hostile):
    from self_improving.harness.x2env.contracts import ArtifactRef
    from self_improving.harness.x2env.deployment import PrivateModelRouter

    def mutate(store, receipt, authorization, put):
        old = receipt["transport"]["invocation.json"]
        value = json.loads(store.read_artifact(ArtifactRef.model_validate(old)))
        argv = value["argv"]
        argv[10] = "openai/gpt-5.6-terra"
        index = argv.index("--output-last-message") + 2
        argv[index:index] = PrivateModelRouter(api_key_file="/unused").arguments()
        if hostile:
            argv[index:index] = ["-c", "features.shell_tool=true"]
        replacement = put(value).model_dump()
        receipt["transport"]["invocation.json"] = replacement
        receipt["evidence"] = [replacement if ref == old else ref for ref in receipt["evidence"]]

    store, snapshot = replay_external_producer(tmp_path, mutate)
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    assert (result.status == "materialized") is (not hostile)


@pytest.mark.parametrize(
    "fault",
    [
        "authority",
        "policy",
        "structural_policy",
        "original_proposal",
        "assets_ref",
        "context_fixed",
        "context_missing",
        "evidence_nonobject",
        "transport_list",
        "process_nonobject",
        "process_root_nonstring",
        "invocation_nonobject",
        "argv_nonlist",
        "schema",
        "missing_completed",
        "tool_event",
        "item_type_nonstring",
        "authorization_workflow",
        "authorization_operation",
        "authorization_assets",
        "response_known_axis",
        "terminal_not_reaped",
        "process_relative_root",
        "executable_identity",
        "invented_design_choice",
    ],
)
def test_completion_rejects_independently_persisted_v2_attacks(tmp_path, fault):
    from self_improving.harness.x2env.contracts import ArtifactRef

    def attack(store, receipt, authorization, put):
        def replace_record(name, transform):
            old = receipt["transport"][name]
            value = json.loads(store.read_artifact(ArtifactRef.model_validate(old)))
            changed = put(transform(value)).model_dump()
            receipt["evidence"] = [changed if ref == old else ref for ref in receipt["evidence"]]
            receipt["transport"][name] = changed

        if fault == "authority":
            receipt["authority"] = "qualification"
        elif fault == "invented_design_choice":
            receipt["design_choices"] = [
                {"entity_id": "item", "path": "pose.position[0]", "value": 0.2}
            ]
        elif fault == "policy":
            receipt["policy"]["position_abs_max_m"] = 500.0
        elif fault == "structural_policy":
            receipt["structural_policy"]["surface_height_m"] = 1.5
        elif fault == "original_proposal":
            receipt["proposal_ref"] = receipt["assets_ref"]
        elif fault == "assets_ref":
            receipt["assets_ref"] = receipt["proposal_ref"]
        elif fault in {"context_fixed", "context_missing"}:
            for ref in tuple(receipt["evidence"]):
                if ref["media_type"] != "application/json":
                    continue
                value = json.loads(store.read_artifact(ArtifactRef.model_validate(ref)))
                if (
                    isinstance(value, dict)
                    and value.get("schema_version") == "x2env.generated_layout_context.v2"
                ):
                    receipt["evidence"].remove(ref)
                    if fault == "context_fixed":
                        value["fixed_values"]["item.pose.position[2]"] = 0.7
                        receipt["evidence"].append(put(value).model_dump())
        elif fault == "evidence_nonobject":
            receipt["evidence"].append(None)
        elif fault == "transport_list":
            receipt["transport"] = list(receipt["transport"])
        elif fault == "process_nonobject":
            replace_record("process.json", lambda value: None)
        elif fault == "process_root_nonstring":
            replace_record("process.json", lambda value: {**value, "attempt_root": []})
        elif fault == "invocation_nonobject":
            replace_record("invocation.json", lambda value: [])
        elif fault == "argv_nonlist":
            replace_record("invocation.json", lambda value: {**value, "argv": "/bin/true"})
        elif fault == "schema":
            replace_record("proposal.schema.json", lambda value: {"type": "object"})
        elif fault == "response_known_axis":

            def change_axis(value):
                value["entities"][0]["position"][0] = 0.2
                return value

            replace_record("proposal.json", change_axis)
        elif fault == "terminal_not_reaped":
            replace_record("process-terminal.json", lambda value: {**value, "reaped": False})
        elif fault == "process_relative_root":
            replace_record("process.json", lambda value: {**value, "attempt_root": "relative"})
        elif fault == "executable_identity":
            replace_record("process.json", lambda value: {**value, "executable_sha256": "f" * 64})
        elif fault in {"missing_completed", "tool_event", "item_type_nonstring"}:
            events = {
                "missing_completed": [{"type": "thread.started"}],
                "tool_event": [
                    {"type": "turn.completed"},
                    {"type": "item.completed", "item": {"type": "command_execution"}},
                ],
                "item_type_nonstring": [
                    {"type": "turn.completed"},
                    {"type": "item.completed", "item": {"type": []}},
                ],
            }[fault]
            old = receipt["transport"]["codex.jsonl"]
            changed = store.write_artifact(
                b"\n".join(json.dumps(e).encode() for e in events), "text/plain"
            ).model_dump()
            receipt["transport"]["codex.jsonl"] = changed
            receipt["evidence"] = [changed if ref == old else ref for ref in receipt["evidence"]]
        elif fault.startswith("authorization_"):
            key = {
                "authorization_workflow": "workflow_id",
                "authorization_operation": "operation_id",
                "authorization_assets": "assets_ref",
            }[fault]
            authorization[key] = "wrong" if key != "assets_ref" else receipt["proposal_ref"]

    store, snapshot = replay_external_producer(tmp_path, attack)
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    assert result.status == "failed", result
    assert result.receipt is not None and store.read_artifact(result.receipt)
    if fault in {"evidence_nonobject", "transport_list", "item_type_nonstring"}:
        assert result.error_code.startswith("completion_grounding_"), result


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "authorization",
        "missing_authorization",
        "unknowns",
        "assets",
        "model",
        "v2_geometry",
        "v2_fixed",
        "v2_unknown_indices",
        "v2_transport_response",
        "v2_transport_missing",
        "v2_transport_path",
        "v2_event_list",
        "v2_event_null",
        "v2_event_item_null",
        "v2_argv_nonstring",
    ],
)
def test_completion_reaudits_generated_design_controller_authority(tmp_path, fault):
    store, snapshot = completed_fixture(
        tmp_path,
        grounding=True,
        structural_grounding=True,
        generated_grounding=True,
        grounding_fault=fault,
    )
    result = materialize_completion(snapshot, store, tmp_path / "delivery")
    if fault is None:
        assert result.status == "materialized", result
    else:
        assert result.status == "failed" and "grounding" in result.error_code, result
        if fault.startswith("v2_event_"):
            assert result.error_code == "completion_grounding_transport_event_invalid"
        elif fault == "v2_argv_nonstring":
            assert result.error_code == "completion_grounding_transport_unbound"
