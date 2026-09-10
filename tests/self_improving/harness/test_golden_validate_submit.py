"""P4 tracer: qualified compile, replay, and legacy validate in one workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from scene_gen.schema import ResolvedSceneSpec
from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_replay_execution import (
    create_text2env_replay_execution_binding,
)
from self_improving.harness.golden_run import GoldenRunHarness, GoldenRunRegistryError
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.golden_validate_execution import (
    create_text2env_validate_execution_binding,
)
from self_improving.harness.golden_validate_policy import Text2EnvValidateV2EvidencePolicy
from self_improving.harness.run_receipts import PortableRunReceiptPublisher
from self_improving.harness.run_store import ControlledRunLifecycle, SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, RunStatus
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.schemas.workflow import RunCommandRequest, RunReadRequest
from self_improving.harness.schemas.workflow_operation import (
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState
from self_improving.harness.validate_application import (
    ValidateApplicationSettings,
    create_validate_application,
)
from tests.self_improving import test_validate_v2_snapshot as snapshot_fixture
from tests.self_improving.harness import test_golden_replay_submit as replay_base
from tests.self_improving.harness import test_golden_run as base
from tests.self_improving.harness import test_text2env_replay_handler as replay_fixture

ROOT = Path(__file__).resolve().parents[3]
REQUEST = "Place a can on top of a plate."
CHILD_IDS = (
    UUID("43000000-0000-4000-8000-000000000001"),
    UUID("43000000-0000-4000-8000-000000000002"),
    UUID("43000000-0000-4000-8000-000000000003"),
)


@dataclass(frozen=True)
class ValidateCase:
    store: LocalArtifactStore
    harness: GoldenRunHarness
    compile_application: object
    replay_application: object
    validate_application: object
    revision_two: object
    validate_input: Text2EnvValidateV2Input
    validate_command: RunCommandRequest


def _put_model(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    value: object,
    schema_version: str,
) -> ArtifactRef:
    payload = (
        base._canonical_bytes(value)
        if hasattr(value, "model_dump")
        else (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )
    return replay_base._put_json(
        store,
        root,
        name=name,
        payload=payload,
        schema_version=schema_version,
    )


def _read(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _registry(
    store: LocalArtifactStore,
    tmp_path: Path,
    applications: tuple[object, ...],
) -> ArtifactRef:
    entries = []
    for application in applications:
        descriptor = application.skills[0]
        label = descriptor.skill_id.rsplit(".", maxsplit=1)[-1]
        bundle = (
            ROOT
            / "self_improving/harness/qualified_skills"
            / descriptor.skill_id
            / descriptor.version
        )
        descriptor_ref = _put_model(
            store,
            tmp_path,
            name=f"{label}-descriptor",
            value=descriptor,
            schema_version="harness.skill_descriptor.v2"
            if label == "replay"
            else "harness.skill_descriptor.v1",
        )
        report_ref = replay_base._put_json(
            store,
            tmp_path,
            name=f"{label}-qualification-report",
            payload=(bundle / "report.json").read_bytes(),
            schema_version="harness.skill_qualification_report.v1",
        )
        entries.append(
            QualifiedSkillSnapshot(
                skill_ref=f"{descriptor.skill_id}@{descriptor.version}",
                mcp_tool_name=descriptor.mcp_tool_name,
                descriptor_ref=descriptor_ref,
                qualification_ref=descriptor.qualification_artifact,
                qualification_report_ref=report_ref,
            )
        )
    return _put_model(
        store,
        tmp_path,
        name="compile-replay-validate-registry",
        value=RegistrySnapshot(
            schema_version="harness.registry_snapshot.v1",
            qualified_skills=tuple(entries),
        ),
        schema_version="harness.registry_snapshot.v1",
    )


def _validation_input(
    *,
    root: Path,
    store: LocalArtifactStore,
    compile_application,
    replay_application,
    revision_one,
    replay_operation,
) -> Text2EnvValidateV2Input:
    publisher = PortableRunReceiptPublisher(store)
    compile_portable = publisher.publish(compile_application, CHILD_IDS[0])
    replay_portable = publisher.publish(replay_application, CHILD_IDS[1])
    compile_state = compile_application.run_state(CHILD_IDS[0])
    compile_invocation = compile_application.invocation(CHILD_IDS[0])
    replay_state = replay_application.run_state(CHILD_IDS[1])
    assert compile_state is not None and compile_invocation is not None and replay_state is not None
    compile_output = Text2EnvCompileOutput.model_validate(compile_state.output)
    replay_output = Text2EnvReplayOutput.model_validate(replay_state.output)
    package_ref = _put_model(
        store,
        root,
        name="environment-package",
        value=compile_output.environment_package,
        schema_version="harness.environment_package.v1",
    )
    artifacts = {ref.schema_version: ref for ref in replay_state.artifacts}
    execution = artifacts["harness.text2env_replay_receipt.v1"]
    execution_document = json.loads(store.resolve(execution).path.read_bytes())
    media = _put_model(
        store,
        root,
        name="media-verification-receipt",
        value={
            "schema_version": "harness.replay_media_verification.v1",
            "verification": execution_document["media_verification"],
        },
        schema_version="harness.replay_media_verification.v1",
    )
    compile_history = _read(
        store,
        revision_one.receipt_head,
        WorkflowOperationReceiptV2,
    )
    compile_input = Text2EnvCompileInput.model_validate(compile_invocation.effective_parameters)
    provenance = _put_model(
        store,
        root,
        name="request-provenance",
        value={
            "schema_version": "harness.text2env_request_provenance.v1",
            "request": compile_input.request,
            "seed": compile_input.seed,
            "compile_skill_ref": "text2env.compile@1.0.0",
            "compile_run_id": str(compile_state.run_id),
            "compile_invocation_digest": compile_invocation.invocation_digest,
            "compile_trusted_receipt": compile_history.tool_receipt.model_dump(mode="json"),
            "compile_history_authority": revision_one.receipt_head.model_dump(mode="json"),
            "compile_world_state_sha256": compile_history.resulting_state_sha256,
            "environment_package": package_ref.model_dump(mode="json"),
        },
        schema_version="harness.text2env_request_provenance.v1",
    )
    assert replay_operation.tool_result is not None
    return Text2EnvValidateV2Input(
        environment_package=package_ref,
        compile_run_receipt=compile_portable,
        compile_qualification=compile_application.skills[0].qualification_artifact,
        replay_run_receipt=replay_portable,
        replay_qualification=replay_application.skills[0].qualification_artifact,
        replay_execution_receipt=execution,
        runtime_evidence=replay_output.runtime_evidence,
        runtime_validation_report=artifacts["robotwin.scene_validation.v1"],
        runtime_asset_snapshot_manifest=artifacts["harness.runtime_asset_snapshot.v1"],
        media_verification_receipt=media,
        request_provenance=provenance,
        runtime_event_transcript=artifacts["harness.runtime_event_transcript.v1"],
    )


def _case(tmp_path: Path) -> ValidateCase:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_application = create_compile_application(
        CompileApplicationSettings(
            state_root=tmp_path / "compile-state",
            external_catalog_roots=(tmp_path / "source",),
            allowed_asset_roots=(source.robotwin_root,),
            admission_date=date(2026, 9, 10),
            asset_library_root=tmp_path / "asset-library",
        )
    )
    store = LocalArtifactStore(compile_application.artifact_root)
    replay_application, executor = replay_base._replay_application(
        tmp_path,
        store=store,
        source=source,
    )
    validate_application = create_validate_application(
        ValidateApplicationSettings(
            state_root=tmp_path / "validate-state",
            evidence_artifact_root=store.root,
        )
    )
    registry = _registry(
        store,
        tmp_path,
        (compile_application, replay_application, validate_application),
    )
    objective = replay_base._put_json(
        store,
        tmp_path,
        name="objective",
        payload=base.OBJECTIVE_EVIDENCE.replace(
            b"Place a can on a plate.",
            REQUEST.encode(),
        ),
        schema_version="harness.world_fact_evidence.v1",
    )
    children = iter(CHILD_IDS)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_application),
                create_text2env_replay_execution_binding(application=replay_application),
                create_text2env_validate_execution_binding(application=validate_application),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: next(children),
    )
    revision_zero = harness.start(base._request(objective, registry))
    compile_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("42000000-0000-4000-8000-000000000001"),
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
            idempotency_key="qualified-p4-step",
            expected_workflow_revision=0,
            expected_state_sha256=revision_zero.state_sha256,
            expected_receipt_head_sha256=revision_zero.receipt_head.sha256,
            skill_ref="text2env.compile@1.0.0",
            parameters=Text2EnvCompileInput(
                request=REQUEST,
                seed=7,
                asset_catalog=compile_application.snapshot_asset_catalog(
                    tmp_path / "source/catalog.json"
                ),
                config=CompileConfig(generate_missing_assets=False),
            ).model_dump(mode="json"),
        )
    )
    assert compile_operation.status is RunStatus.SUCCEEDED
    revision_one = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
        )
    )
    state_one = _read(store, revision_one.state_ref, TrustedWorldState)
    package = next(fact.value for fact in state_one.facts if fact.key == "environment.package")
    typed_package = Text2EnvReplayInput(
        environment_package=package,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
    ).environment_package
    manifest = json.loads(store.resolve(typed_package.package_manifest).path.read_bytes())
    resolved_record = next(
        item for item in manifest["files"] if item["path"] == "resolved_scene.json"
    )
    resolved_path = (
        store.root
        / "sha256"
        / resolved_record["sha256"][:2]
        / resolved_record["sha256"]
    )
    resolved = ResolvedSceneSpec.model_validate_json(resolved_path.read_bytes())

    def complete_evidence(evidence: dict[str, object]) -> None:
        complete = snapshot_fixture._passing_runtime_evidence(
            resolved,
            snapshot_sha256=str(evidence["runtime_asset_snapshot_sha256"]),
        )
        complete.update(
            {
                "precheck_steps": 0,
                "contact_window_steps": 120,
                "base_simulation_step_count": 900,
                "simulation_step_count": 900,
                "total_physics_step_count": 900,
                "settle_converge_max": 0,
                "video_frame_count": 120,
                "unique_video_frame_count": 120,
                "video_sample_step_indices": [index * 899 // 119 for index in range(120)],
                "fps": 12,
                "images": evidence["images"],
                "video": evidence["video"],
            }
        )
        evidence.clear()
        evidence.update(complete)

    def passing_report(report: dict[str, object]) -> None:
        report.update(
            {
                "status": "pass",
                "fail_count": 0,
                "not_run_count": 0,
                "checks": [{"name": "physical_contact", "status": "pass", "evidence": {}}],
            }
        )

    executor.evidence_mutation = complete_evidence
    executor.validation_mutation = passing_report
    replay_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("42000000-0000-4000-8000-000000000002"),
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
            idempotency_key="qualified-p4-step",
            expected_workflow_revision=1,
            expected_state_sha256=revision_one.state_sha256,
            expected_receipt_head_sha256=revision_one.receipt_head.sha256,
            skill_ref="text2env.replay@1.0.0",
            parameters=Text2EnvReplayInput(
                environment_package=package,
                runtime_config=RuntimeConfig(
                    precheck_steps=0,
                    settle_steps=900,
                    contact_window_steps=120,
                    video_frames=120,
                    fps=12,
                ),
            ).model_dump(mode="json"),
        )
    )
    assert replay_operation.status is RunStatus.SUCCEEDED, _read(
        store,
        replay_operation.tool_result,
        ToolResultV2,
    ).blocker.details
    revision_two = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
        )
    )
    validate_input = _validation_input(
        root=tmp_path,
        store=store,
        compile_application=compile_application,
        replay_application=replay_application,
        revision_one=revision_one,
        replay_operation=replay_operation,
    )
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        command_id=UUID("42000000-0000-4000-8000-000000000003"),
        principal_id=revision_two.principal_id,
        workflow_run_id=revision_two.workflow_run_id,
        idempotency_key="qualified-p4-step",
        expected_workflow_revision=2,
        expected_state_sha256=revision_two.state_sha256,
        expected_receipt_head_sha256=revision_two.receipt_head.sha256,
        skill_ref="text2env.validate@2.0.0",
        parameters=validate_input.model_dump(mode="json"),
    )
    return ValidateCase(
        store=store,
        harness=harness,
        compile_application=compile_application,
        replay_application=replay_application,
        validate_application=validate_application,
        revision_two=revision_two,
        validate_input=validate_input,
        validate_command=command,
    )


def test_same_workflow_closes_qualified_compile_replay_validate_revision_three(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    operation = case.harness.submit(case.validate_command)
    assert operation.status is RunStatus.SUCCEEDED
    assert operation.committed_revision == 3
    revision_three = case.harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=case.validate_command.principal_id,
            workflow_run_id=case.validate_command.workflow_run_id,
        )
    )
    assert revision_three.revision == 3
    assert revision_three.child_runs == CHILD_IDS
    state = _read(case.store, revision_three.state_ref, TrustedWorldState)
    assert [fact.key for fact in state.facts] == [
        "assets.catalog",
        "environment.package",
        "environment.validation",
        "replay.runtime_evidence",
        "request.objective",
    ]
    receipt = _read(case.store, revision_three.receipt_head, WorkflowOperationReceiptV2)
    assert receipt.predecessor_receipt == case.revision_two.receipt_head
    result = _read(case.store, operation.tool_result, ToolResultV2)
    output = Text2EnvValidateV2Output.model_validate(result.typed_output)
    assert output.publishable is True
    assert output.validation_status.value == "pass"
    assert result.fresh_observations == ()
    assert result.state_delta is not None
    delta = _read(case.store, result.state_delta, StateDeltaV2)
    assert len(delta.mutations) == 1
    assert delta.mutations[0].key == "environment.validation"
    tool_receipt = _read(case.store, result.trusted_receipt, TrustedToolReceiptV2)
    assert tool_receipt.base_revision == 2
    assert tool_receipt.base_state == case.revision_two.state_ref
    assert len(tool_receipt.derived_fact_claims) == 1


def test_validate_rejects_mismatched_lineage_before_child_and_restarts_idempotently(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    changed_input = case.validate_input.model_copy(
        update={"replay_run_receipt": case.validate_input.compile_run_receipt}
    )
    changed = case.validate_command.model_copy(
        update={"parameters": changed_input.model_dump(mode="json")}
    )
    with pytest.raises(GoldenRunRegistryError, match="not authorized"):
        case.harness.submit(changed)
    authority = SQLiteRunStore(case.validate_application.journal_path)
    assert authority.read_controlled_lifecycle(CHILD_IDS[2]) == ControlledRunLifecycle(
        intent=None,
        invocation=None,
        terminal=None,
    )

    completed = case.harness.submit(case.validate_command)
    restarted = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=case.harness._workflow_store,
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(
                    child_authority=SQLiteRunStore(case.compile_application.journal_path)
                ),
                create_text2env_replay_execution_binding(
                    child_authority=SQLiteRunStore(case.replay_application.journal_path)
                ),
                create_text2env_validate_execution_binding(
                    child_authority=authority,
                    artifact_store=case.store,
                ),
            )
        ),
        clock=lambda: datetime.now(timezone.utc),
        child_run_id_factory=lambda: pytest.fail("idempotent resubmit cannot reserve a child"),
    )
    assert restarted.submit(case.validate_command) == completed


def test_validate_policy_rejects_wrong_runtime_fact_before_execution(tmp_path: Path) -> None:
    case = _case(tmp_path)
    state = _read(case.store, case.revision_two.state_ref, TrustedWorldState)
    policy = Text2EnvValidateV2EvidencePolicy(case.store)
    policy.authorize(state, case.validate_input)
    facts = list(state.facts)
    index = next(i for i, fact in enumerate(facts) if fact.key == "replay.runtime_evidence")
    facts[index] = facts[index].model_copy(update={"value": {"wrong": True}})
    changed = state.model_copy(update={"facts": tuple(facts)})
    with pytest.raises(ValueError, match="runtime evidence"):
        policy.authorize(changed, case.validate_input)
