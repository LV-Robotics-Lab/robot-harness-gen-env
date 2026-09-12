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


def _new_development_service(preview_inputs, tmp_path):
    from types import SimpleNamespace

    from self_improving.harness.limited_image_preview_development import (
        DevelopmentArtifactStore,
        capture_development_execution_context,
    )
    from tests.self_improving.harness.test_golden_run import _qualified_registry
    from tests.self_improving.harness.test_limited_image_preview_development import _git_repository

    repository = _git_repository(tmp_path / "repository")
    settings = tmp_path / "development-settings.json"
    settings.write_text('{"execution_mode":"development"}\n')
    context = capture_development_execution_context(
        artifact_store=preview_inputs.store,
        repository_root=repository,
        settings_path=settings,
    )
    journal = SQLiteGoldenRunStore(tmp_path / "golden.sqlite")
    artifacts = DevelopmentArtifactStore(
        artifact_store=preview_inputs.store,
        root=tmp_path / "development-artifacts",
        context_ref=context.ref,
    )
    service = LimitedImagePreviewService(
        artifact_store=preview_inputs.store,
        workflow_store=journal,
        input_builder=preview_inputs.builder,
        registry_snapshot=_qualified_registry(preview_inputs.store, tmp_path),
        principal_id="test/development-terminal-read",
        execution_mode="development",
        development_context=context,
        development_artifacts=artifacts,
    )
    started = service.start(
        raw_image_ref=preview_inputs.raw,
        user_text="mouse on table",
        idempotency_key="development-terminal-read",
    )
    return SimpleNamespace(
        service=service,
        journal=journal,
        artifacts=artifacts,
        context=context,
        started=started,
        workflow_id=started["workflow_id"],
    )


def _terminal_development_service(preview_inputs, tmp_path):
    from datetime import timedelta

    from self_improving.harness.limited_image_preview_development import (
        build_development_execution_record,
    )
    from self_improving.harness.limited_image_preview_input_evidence import (
        read_limited_preview_input,
    )
    from self_improving.harness.schemas.common import ArtifactRef, RunStatus
    from self_improving.harness.schemas.workflow import OperationSnapshot, RunSnapshot
    from self_improving.harness.trusted_state import TrustedWorldState
    from tests.self_improving.harness.test_limited_image_preview_development import _artifact

    case = _new_development_service(preview_inputs, tmp_path)
    initial = RunSnapshot.model_validate(case.started["snapshot"])
    command = _artifact(
        preview_inputs.store,
        tmp_path,
        "terminal-command.json",
        "harness.run_command_request.v1",
    )
    qualified = _artifact(
        preview_inputs.store,
        tmp_path,
        "terminal-qualified.json",
        "harness.qualified_skill_execution.v2",
    )
    tool_result = _artifact(
        preview_inputs.store,
        tmp_path,
        "terminal-tool-result.json",
        "harness.tool_result.v2",
    )
    receipt = _artifact(
        preview_inputs.store,
        tmp_path,
        "terminal-receipt.json",
        "harness.workflow_operation_receipt.v2",
    )
    log = _artifact(preview_inputs.store, tmp_path, "terminal-log.json", None)
    physical = _artifact(
        preview_inputs.store,
        tmp_path,
        "terminal-physical.json",
        "harness.genesis_scene_report.v1",
    )
    from uuid import uuid4

    child = uuid4()
    operation_id = uuid4()
    ended = initial.updated_at + timedelta(microseconds=1)
    running = OperationSnapshot(
        schema_version="harness.operation_snapshot.v1",
        operation_id=operation_id,
        workflow_run_id=initial.workflow_run_id,
        principal_id=initial.principal_id,
        idempotency_key="terminal-validate",
        command=command,
        qualified_execution=qualified,
        skill_ref="image2env.validate@3.0.0",
        status=RunStatus.RUNNING,
        base_revision=0,
        committed_revision=None,
        child_run_id=child,
        tool_result=None,
        started_at=initial.updated_at,
        ended_at=None,
    )
    case.journal.reserve_operation(
        principal_id=initial.principal_id,
        workflow_run_id=initial.workflow_run_id,
        skill_ref=running.skill_ref,
        idempotency_key=running.idempotency_key,
        command_sha256=command.sha256,
        expected_workflow_revision=0,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        operation=running,
    )
    terminal = OperationSnapshot.model_validate(
        running.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "committed_revision": 1,
                "tool_result": tool_result,
                "ended_at": ended,
            }
        ).model_dump(mode="python")
    )
    snapshot = RunSnapshot.model_validate(
        initial.model_copy(
            update={
                "revision": 1,
                "turn_seq": 1,
                "receipt_head": receipt,
                "active_operation": None,
                "child_runs": (*initial.child_runs, child),
                "updated_at": ended,
            }
        ).model_dump(mode="python")
    )
    case.journal.commit_operation(
        principal_id=initial.principal_id,
        running=running,
        terminal=terminal,
        snapshot=snapshot,
    )
    state = TrustedWorldState.model_validate_json(
        preview_inputs.store.resolve(initial.state_ref).path.read_bytes()
    )
    input_ref = ArtifactRef.model_validate(
        next(fact.value for fact in state.facts if fact.key == "request.preview_input")
    )
    _document, input_roots = read_limited_preview_input(
        preview_inputs.store, input_ref, initial.workflow_run_id
    )
    record = build_development_execution_record(
        workflow_run_id=initial.workflow_run_id,
        principal_id=initial.principal_id,
        idempotency_key="terminal-development-package",
        workflow_head=receipt,
        context=case.context.context,
        context_ref=case.context.ref,
        input_artifacts=input_roots,
        output_artifacts=(snapshot.state_ref, receipt, command, tool_result),
        log_artifacts=(log,),
        physical_evidence=(physical,),
        exact_skill_evidence=(qualified,),
        created_at=ended,
    )
    record_ref = case.artifacts.commit(record)
    case.snapshot = snapshot
    case.operation = terminal
    case.record = record
    case.record_ref = record_ref
    case.input_ref = input_ref
    case.association = case.artifacts.root / f"{initial.workflow_run_id}.json"
    return case


def test_terminal_development_status_and_package_get_use_shallow_durable_head(inputs, tmp_path):  # noqa: F811
    from types import SimpleNamespace

    case = _terminal_development_service(inputs, tmp_path)

    def forbidden_deep_read(*args, **kwargs):
        pytest.fail("terminal development read entered deployment readiness or deep Harness read")

    case.service._prepare = forbidden_deep_read
    case.service.harness = SimpleNamespace(read=forbidden_deep_read)
    original_read_snapshot = case.journal.read_snapshot
    snapshot_reads = 0

    def counted_read_snapshot(**kwargs):
        nonlocal snapshot_reads
        snapshot_reads += 1
        return original_read_snapshot(**kwargs)

    case.journal.read_snapshot = counted_read_snapshot

    status = case.service.status(workflow_id=case.workflow_id)
    package = case.service.package_get(workflow_id=case.workflow_id)

    assert status["status"] == package["status"] == "validated_unreleased"
    assert status["stage"] == "status"
    assert package["stage"] == "package_get"
    assert status["package"] == package["package"] == case.record_ref.model_dump(mode="json")
    assert snapshot_reads == 2


@pytest.mark.parametrize("mode", ["development", "release"])
@pytest.mark.parametrize("endpoint", ["status", "package_get"])
def test_active_or_release_read_still_enters_deep_harness_path(inputs, tmp_path, mode, endpoint):  # noqa: F811
    if mode == "development":
        case = _new_development_service(inputs, tmp_path)
        service, workflow_id = case.service, case.workflow_id
    else:
        from tests.self_improving.harness.test_golden_run import _qualified_registry

        service = LimitedImagePreviewService(
            artifact_store=inputs.store,
            workflow_store=SQLiteGoldenRunStore(tmp_path / "release.sqlite"),
            input_builder=inputs.builder,
            registry_snapshot=_qualified_registry(inputs.store, tmp_path),
            principal_id="test/release-terminal-read",
        )
        workflow_id = service.start(
            raw_image_ref=inputs.raw,
            user_text="mouse on table",
            idempotency_key="release-terminal-read",
        )["workflow_id"]

    class DeepPathCalled(Exception):
        pass

    def deep_path(*args, **kwargs):
        raise DeepPathCalled

    service._prepare = deep_path
    with pytest.raises(DeepPathCalled):
        getattr(service, endpoint)(workflow_id=workflow_id)


@pytest.mark.parametrize("endpoint", ["status", "package_get"])
@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("association", "association is corrupt"),
        ("context", "association is corrupt"),
        ("database_head", "payload checksum"),
        ("operation", "payload checksum"),
        ("input", "artifact content"),
    ],
)
def test_terminal_development_shallow_read_fails_closed_without_deep_fallback(
    inputs,  # noqa: F811
    tmp_path,
    endpoint,
    drift,
    message,
):
    import hashlib
    import sqlite3
    from types import SimpleNamespace

    case = _terminal_development_service(inputs, tmp_path)
    if drift == "association":
        case.association.write_bytes(b"{}")
    elif drift == "context":
        value = json.loads(case.association.read_bytes())
        value["context"] = value["record"]
        case.association.write_bytes(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
    elif drift == "database_head":
        with sqlite3.connect(case.journal.path) as connection:
            connection.execute(
                "UPDATE golden_workflow_starts SET snapshot_sha256=? WHERE workflow_run_id=?",
                ("0" * 64, case.workflow_id),
            )
    elif drift == "operation":
        with sqlite3.connect(case.journal.path) as connection:
            connection.execute(
                "UPDATE golden_workflow_operations SET operation_sha256=? WHERE workflow_run_id=?",
                ("0" * 64, case.workflow_id),
            )
    else:
        path = inputs.store.resolve(case.input_ref).path
        path.write_bytes(b"{}")
        assert hashlib.sha256(path.read_bytes()).hexdigest() != case.input_ref.sha256

    def forbidden_deep_read(*args, **kwargs):
        raise AssertionError("corrupt shallow terminal read fell back to deep execution")

    case.service._prepare = forbidden_deep_read
    case.service.harness = SimpleNamespace(read=forbidden_deep_read)
    with pytest.raises((ValueError, RuntimeError), match=message):
        getattr(case.service, endpoint)(workflow_id=case.workflow_id)
