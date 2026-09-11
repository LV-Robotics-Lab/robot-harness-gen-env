"""Preview start and resources are one existing Golden workflow, not a second journal."""

import json

import pytest

from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.limited_image_preview_service import LimitedImagePreviewService
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401
from tests.self_improving.harness.test_limited_image_preview_inputs import inputs  # noqa: F401


def test_start_retry_lookup_and_audited_resources_share_one_workflow(inputs, tmp_path):  # noqa: F811
    from tests.self_improving.harness.test_golden_run import _qualified_registry

    registry = _qualified_registry(inputs.store, tmp_path)
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite")
    service = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=journal,
        input_builder=inputs.builder,
        registry_snapshot=registry,
        principal_id="test/preview",
    )
    started = service.start(
        raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one"
    )
    with pytest.raises(ValueError, match="audited lookup"):
        service.compile(
            workflow_id=started["workflow_id"],
            asset_id="mouse",
            idempotency_key="compile",
            expected_skill_ref="image2env.compile@2.0.0",
        )
    assert started["snapshot"]["workflow_run_id"] == started["workflow_id"]
    assert (
        service.start(raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key="one")
        == started
    )
    looked = service.asset_lookup(workflow_id=started["workflow_id"], query="mouse")
    assert looked["asset"]["asset_id"] == "mouse"
    assert looked["resource_links"]
    resource = service.read_resource(started["workflow_id"], looked["resource_links"][0]["uri"])
    assert resource["contents"]
    status = service.status(workflow_id=started["workflow_id"])
    assert status["snapshot"] == started["snapshot"]
    assert status["asset"] is None
    snapshot = journal.read_snapshot(principal_id="test/preview", workflow_run_id=inputs.workflow)
    assert snapshot is None  # The fixture's unrelated UUID cannot be selected by the caller.
    state_ref = started["snapshot"]["state_ref"]
    from self_improving.harness.schemas.common import ArtifactRef

    state = json.loads(
        inputs.store.resolve(ArtifactRef.model_validate(state_ref)).path.read_bytes()
    )
    fact = next(f for f in state["facts"] if f["key"] == "request.preview_input")
    record = json.loads(
        inputs.store.resolve(ArtifactRef.model_validate(fact["value"])).path.read_bytes()
    )
    assert record["workflow_id"] == started["workflow_id"]
    assert service.read_resource(started["workflow_id"], resource["contents"][1]["uri"])["contents"]
    image_uri = f"harness://workflows/{started['workflow_id']}/artifacts/{inputs.raw.sha256}"
    assert "blob" in service.read_resource(started["workflow_id"], image_uri)["contents"][0]
    with pytest.raises(ValueError, match="different input"):
        service.start(
            raw_image_ref=inputs.raw, user_text="different mouse request", idempotency_key="one"
        )
    with pytest.raises(ValueError, match="exact workflow"):
        service.read_resource(inputs.workflow, image_uri)
    with pytest.raises(ValueError, match="exactly one"):
        service.start(user_text="mouse", idempotency_key="missing")
    with pytest.raises(ValueError, match="explicit XY"):
        service.compile(
            workflow_id=started["workflow_id"],
            asset_id="mouse",
            idempotency_key="revise",
            expected_skill_ref="image2env.compile@3.0.0",
        )
    with pytest.raises(ValueError, match="current committed"):
        service.compile(
            workflow_id=started["workflow_id"],
            asset_id="mouse",
            idempotency_key="revise",
            expected_skill_ref="image2env.compile@3.0.0",
            position_x_m=0.0,
            position_y_m=0.0,
            yaw_rad=0.0,
        )
    with pytest.raises(ValueError, match="exact replay"):
        service.replay(
            workflow_id=started["workflow_id"],
            idempotency_key="replay",
            expected_skill_ref="other@1.0.0",
        )
    with pytest.raises(ValueError, match="committed predecessor"):
        service.observe(workflow_id=started["workflow_id"], idempotency_key="observe")
    with pytest.raises(ValueError, match="three explicit"):
        service.validate(
            workflow_id=started["workflow_id"],
            idempotency_key="validate",
            expected_position_x_m=0.0,
        )
    from self_improving.harness.golden_run import GoldenRunRegistryError

    with pytest.raises(GoldenRunRegistryError):
        service.compile(
            workflow_id=started["workflow_id"],
            asset_id="mouse",
            idempotency_key="compile",
            expected_skill_ref="image2env.compile@2.0.0",
        )


@pytest.mark.parametrize("change", ["workflow", "extra", "prompt", "catalog", "missing"])
def test_preview_input_resource_reader_rejects_foreign_or_incomplete_roots(inputs, change):  # noqa: F811
    from uuid import uuid4

    from self_improving.harness.limited_image_preview_input_evidence import (
        read_limited_preview_input,
    )

    ref = inputs.builder.start(
        workflow_id=inputs.workflow, raw_image_ref=inputs.raw, user_text="mouse"
    )
    payload = json.loads(inputs.store.resolve(ref).path.read_bytes())
    if change == "workflow":
        payload["workflow_id"] = str(uuid4())
    elif change == "extra":
        payload["arbitrary"] = inputs.raw.model_dump(mode="json")
    elif change == "prompt":
        payload["effective_prompt"]["schema_version"] = None
    elif change == "catalog":
        payload["catalog_ref"]["schema_version"] = None
    else:
        inputs.store.resolve(inputs.builder.asset_catalog).path.unlink()
    invalid = inputs.publish(payload, "invalid-preview-input.json", None)
    with pytest.raises(ValueError):
        read_limited_preview_input(inputs.store, invalid, inputs.workflow)


def test_restarted_status_restores_asset_after_successful_initial_compile(inputs, tmp_path):  # noqa: F811
    """The repository's sealed native projection supplies the successful compile."""
    import hashlib
    import shutil
    from datetime import datetime, timezone
    from uuid import UUID

    from self_improving.harness.golden_execution import (
        _create_golden_execution_binding,
        create_golden_execution_catalog,
    )
    from self_improving.harness.native_skill_projection import (
        issue_native_skill_projection,
        publish_native_registry_snapshot,
    )
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileReceiptV1,
    )
    from self_improving.harness.typed_qualification_admission import (
        verify_typed_qualification_admission,
    )
    from tests.self_improving.harness.test_native_skill_projection import (
        _admission,
        _ControlledV1NativeApplication,
        _native_image_binding,
        _put_model,
    )

    principal, key = "test/repair-asset", "repair-asset"
    identity = json.dumps([principal, key], separators=(",", ":")).encode()
    workflow_id = UUID(
        bytes=hashlib.sha256(b"limited-preview-start\0" + identity).digest()[:16], version=4
    )
    input_ref = inputs.builder.start(
        workflow_id=workflow_id, raw_image_ref=inputs.raw, user_text="mouse on table"
    )
    lookup_ref = inputs.builder.lookup(workflow_id=workflow_id, input_ref=input_ref)
    request_ref = inputs.builder.build_initial(
        workflow_id=workflow_id,
        input_ref=input_ref,
        lookup_ref=lookup_ref,
        asset_id="mouse",
    )
    selected = json.loads(inputs.store.resolve(lookup_ref).path.read_bytes())
    fixture, issued = _admission(tmp_path / "native", "compile")
    shutil.copytree(fixture.store.root, inputs.store.root, dirs_exist_ok=True)
    admission = verify_typed_qualification_admission(
        artifact_store=inputs.store, admission=issued.admission
    )
    compiled_receipt = Image2EnvGenesisCompileReceiptV1.model_validate(
        admission.admission.candidate_intents[0].run_state.output
    )
    output = Image2EnvGenesisCompileReceiptV1.model_validate(
        {
            **compiled_receipt.model_dump(mode="json"),
            "request": request_ref.model_dump(mode="json"),
            "qualification": selected["qualification"],
            "source_object_id": "mouse_001",
            "proposal_object_id": "mouse_001",
        }
    )
    output_ref = _put_model(inputs.store, tmp_path, "mouse-compile.json", output)
    child = SQLiteRunStore(tmp_path / "compile.sqlite")
    application = _ControlledV1NativeApplication(
        store=inputs.store,
        authority=child,
        admission=admission,
        output=output,
        output_ref=output_ref,
        clock=lambda: datetime.now(timezone.utc),
    )
    binding = _native_image_binding(
        store=inputs.store,
        authority=child,
        skill_ref="image2env.compile@2.0.0",
        application=application,
    )
    projection = issue_native_skill_projection(
        artifact_store=inputs.store, admission=admission, native_binding=binding
    )
    registry = publish_native_registry_snapshot(
        artifact_store=inputs.store, projections=(projection,)
    )
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite")
    service = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=journal,
        input_builder=inputs.builder,
        registry_snapshot=registry,
        principal_id=principal,
        execution_catalog=create_golden_execution_catalog(bindings=(projection.binding,)),
    )
    started = service.start(
        raw_image_ref=inputs.raw, user_text="mouse on table", idempotency_key=key
    )
    looked = service.asset_lookup(workflow_id=started["workflow_id"])
    compiled = service.compile(
        workflow_id=started["workflow_id"],
        asset_id=looked["asset"]["asset_id"],
        idempotency_key="compile",
        expected_skill_ref="image2env.compile@2.0.0",
    )
    assert compiled["status"] == "succeeded"
    restored = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=SQLiteGoldenRunStore(journal.path),
        input_builder=inputs.builder,
        registry_snapshot=registry,
        principal_id=principal,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=binding.policy, application=None, child_authority=child
                ),
            )
        ),
    )

    assert restored.status(workflow_id=started["workflow_id"])["asset"] == looked["asset"]


@pytest.mark.parametrize("expired", [False, True])
def test_status_recovers_capture_and_remaining_ttl_from_existing_golden_operation(
    inputs,  # noqa: F811
    tmp_path,
    expired,
):
    """External controlled camera output; real Registry, SQLite, CAS and Golden delivery."""
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from self_improving.harness.event_journal import SQLiteEventJournal
    from self_improving.harness.golden_execution import (
        _create_golden_execution_binding,
        create_golden_execution_catalog,
    )
    from self_improving.harness.registry import (
        HandlerResult,
        SkillRegistry,
        StaticDependencyResolver,
    )
    from self_improving.harness.run_store import SQLiteRunStore
    from self_improving.harness.schemas.common import ArtifactRef, SkillDescriptor
    from self_improving.harness.schemas.genesis_fresh_capture import GenesisFreshCaptureReceiptV1
    from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
    from self_improving.harness.schemas.workflow import RunCommandRequest
    from tests.self_improving.harness.test_golden_control_v2_runtime import base, native_case

    case = native_case(tmp_path)
    frozen = RegistrySnapshot.model_validate_json(
        inputs.store.resolve(case.head.registry_snapshot).path.read_bytes()
    )
    descriptor = SkillDescriptor.model_validate_json(
        inputs.store.resolve(frozen.qualified_skills[0].descriptor_ref).path.read_bytes()
    )
    captured = datetime.now(timezone.utc) - timedelta(seconds=400 if expired else 0)
    observation = GenesisFreshCaptureReceiptV1(
        schema_version="harness.genesis_fresh_capture_receipt.v1",
        replay_run_id=uuid4(),
        replay_receipt=case.native,
        request=case.native,
        capture_result=case.native,
        image=inputs.raw,
        captured_at=captured,
        checked_at=captured,
        expires_at=captured + timedelta(seconds=300),
        ttl_seconds=300,
        authority="observed_restored_final_pose",
        process_continuation=False,
    )
    ref = base._put_binary(
        inputs.store,
        tmp_path,
        name="wall-capture.json",
        payload=json.dumps(
            observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode(),
        media_type="application/json",
        schema_version=observation.schema_version,
    )
    child = SQLiteRunStore(tmp_path / "wall-camera.sqlite")
    registry = SkillRegistry(
        artifact_resolver=inputs.store,
        dependency_resolver=StaticDependencyResolver({"genesis.observe@3.0.0": ()}),
        event_sink=SQLiteEventJournal(child.path),
        clock=lambda: datetime.now(timezone.utc),
        run_id_factory=uuid4,
        run_store=child,
    )
    registry.register(
        descriptor, lambda parameters, context: HandlerResult(observation, (ref, inputs.raw))
    )

    class Camera:
        skill_ref = "genesis.observe@3.0.0"
        artifact_root = inputs.store.root
        authority_path = child.path
        policy = case.policy
        skills = (descriptor,)

        def invoke(self, parameters, *, run_id):
            return registry.invoke_with_run_id(
                "genesis.observe", "3.0.0", parameters.model_dump(mode="json"), run_id=run_id
            )

        def invocation(self, run_id):
            return child.read_invocation(run_id)

        def run_state(self, run_id):
            return child.read_run_state(run_id)

    catalog = create_golden_execution_catalog(
        bindings=(
            _create_golden_execution_binding(
                policy=case.policy, application=Camera(), child_authority=child
            ),
        )
    )
    journal = SQLiteGoldenRunStore(tmp_path / "preview-wall.sqlite")
    service = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=journal,
        input_builder=inputs.builder,
        registry_snapshot=case.head.registry_snapshot,
        principal_id="test/status",
        execution_catalog=catalog,
    )
    started = service.start(
        raw_image_ref=inputs.raw, user_text="mouse", idempotency_key="status-capture"
    )
    snapshot = started["snapshot"]
    service.harness.submit(
        RunCommandRequest.model_validate(
            {
                "schema_version": "harness.run_command_request.v1",
                "command_id": uuid4(),
                "principal_id": snapshot["principal_id"],
                "workflow_run_id": snapshot["workflow_run_id"],
                "idempotency_key": "controlled-camera",
                "expected_workflow_revision": snapshot["revision"],
                "expected_state_sha256": snapshot["state_sha256"],
                "expected_receipt_head_sha256": snapshot["receipt_head"]["sha256"],
                "skill_ref": "genesis.observe@3.0.0",
                "parameters": {
                    "schema_version": "harness.genesis_fresh_capture_input.v1",
                    "replay_run_id": str(observation.replay_run_id),
                    "ttl_seconds": 300,
                },
            }
        )
    )
    restored = LimitedImagePreviewService(
        artifact_store=inputs.store,
        workflow_store=journal,
        input_builder=inputs.builder,
        registry_snapshot=case.head.registry_snapshot,
        principal_id="test/status",
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                _create_golden_execution_binding(
                    policy=case.policy, application=None, child_authority=child
                ),
            )
        ),
    )
    status = restored.status(workflow_id=started["workflow_id"])
    assert status["stage"] == "status"
    assert ArtifactRef.model_validate(status["observation"]["receipt"]) == ref
    assert (status["ttl_remaining_seconds"] < 0) is expired
    assert status["status"] == ("waiting_input" if expired else "succeeded")
    assert any(item["uri"].endswith(inputs.raw.sha256) for item in status["resource_links"])
    assert any(
        item["uri"].endswith(started["source_prompt"]["sha256"])
        for item in status["resource_links"]
    )
    # A new service reads the committed result and image using SQLite/CAS only.
    image_uri = next(
        item["uri"] for item in status["resource_links"] if item["uri"].endswith(inputs.raw.sha256)
    )
    assert restored.read_resource(started["workflow_id"], image_uri)["contents"][0]["blob"]
