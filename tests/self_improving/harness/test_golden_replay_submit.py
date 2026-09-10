"""Public S1 tracer for one qualified compile then replay workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from self_improving.harness.application import CompileApplicationSettings, create_compile_application
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.golden_compile_execution import (
    create_text2env_compile_execution_binding,
)
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_replay_execution import (
    create_text2env_replay_execution_binding,
)
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.handlers.text2env_replay import (
    build_text2env_replay_wiring,
    text2env_replay_descriptor,
)
from self_improving.harness.package_store import PackageStore
from self_improving.harness.qualification import ImplementationManifestV1
from self_improving.harness.registry import (
    SkillRegistry,
    _EvidenceInvariantRegistration,
)
from self_improving.harness.replay_application import ReplayApplication
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schemas.common import ArtifactRef, RunStatus
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.harness.schemas.workflow import (
    RunCommandRequest,
    RunReadRequest,
)
from self_improving.harness.schemas.workflow_operation import (
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.trusted_state import TrustedWorldState
from tests.self_improving.harness import test_golden_run as base
from tests.self_improving.harness import test_text2env_replay_handler as replay_fixture


ROOT = Path(__file__).resolve().parents[3]
REQUEST = "Place a can on top of a plate."
COMPILE_RUN_ID = UUID("30000000-0000-4000-8000-000000000001")
REPLAY_RUN_ID = UUID("30000000-0000-4000-8000-000000000002")


@dataclass(frozen=True)
class _ReplayRegistrationTestAdapter:
    descriptor: object
    handler: object
    dependency_resolver: object
    qualification_bytes: bytes
    report_bytes: bytes

    def verify_and_build(self, request: object, *, artifact_resolver: object):
        del request, artifact_resolver
        return _EvidenceInvariantRegistration(
            descriptor=self.descriptor,
            handler=self.handler,
            dependency_resolver=self.dependency_resolver,
            qualification_bytes=self.qualification_bytes,
            report_bytes=self.report_bytes,
        )


def _put_json(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    schema_version: str,
) -> ArtifactRef:
    return base._put(
        store,
        root,
        name=name,
        payload=payload,
        schema_version=schema_version,
    )


def _replay_application(
    tmp_path: Path,
    *,
    store: LocalArtifactStore,
    source: replay_fixture.Fixture,
) -> tuple[ReplayApplication, replay_fixture.RecordingExecutor]:
    executor = replay_fixture.RecordingExecutor(
        tmp_path / "runtime-test-adapter",
        replay_fixture._capability_document(),
    )
    media = replay_fixture.RecordingMediaVerifier()
    wiring = build_text2env_replay_wiring(
        artifact_store=store,
        package_store=PackageStore(store),
        runtime_executor=executor,
        media_verifier=media,
        work_root=tmp_path / "replay-work",
        dependency_work_root=tmp_path / "replay-dependencies",
        allowed_asset_roots=(source.robotwin_root,),
        expected_capability_sha256=executor.capability_sha256,
        checkpoint_steps=120,
    )
    bundle = ROOT / "self_improving/harness/qualified_skills/text2env.replay/1.0.0"
    qualification_bytes = (bundle / "qualification.json").read_bytes()
    report_bytes = (bundle / "report.json").read_bytes()
    qualification = _put_json(
        store,
        tmp_path,
        name="replay-qualification.json",
        payload=qualification_bytes,
        schema_version="harness.skill_qualification.v1",
    )
    _put_json(
        store,
        tmp_path,
        name="replay-qualification-report.json",
        payload=report_bytes,
        schema_version="harness.skill_qualification_report.v1",
    )
    manifest = ImplementationManifestV1.model_validate_json((bundle / "manifest.json").read_bytes())
    descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification,
        implementation_sha256=manifest.bundle_sha256,
    )
    database = tmp_path / "replay.sqlite3"
    journal = SQLiteEventJournal(database)
    runs = SQLiteRunStore(database)
    policy = _ReplayRegistrationTestAdapter(
        descriptor=descriptor,
        handler=wiring.handler,
        dependency_resolver=wiring.dependency_resolver,
        qualification_bytes=qualification_bytes,
        report_bytes=report_bytes,
    )
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=None,
        event_sink=journal,
        clock=lambda: base.NOW,
        run_id_factory=uuid4,
        run_store=runs,
        evidence_invariant_policy=policy,
    )
    registry.register_evidence_invariant(object())
    return (
        ReplayApplication(
            artifact_store=store,
            event_journal=journal,
            run_store=runs,
            registry=registry,
        ),
        executor,
    )


def _registry_snapshot(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    compile_application: object,
    replay_application: ReplayApplication,
) -> ArtifactRef:
    entries = []
    for label, application in (
        ("compile", compile_application),
        ("replay", replay_application),
    ):
        descriptor = application.skills[0]
        bundle = ROOT / f"self_improving/harness/qualified_skills/text2env.{label}/1.0.0"
        report = _put_json(
            store,
            tmp_path,
            name=f"{label}-report.json",
            payload=(bundle / "report.json").read_bytes(),
            schema_version="harness.skill_qualification_report.v1",
        )
        descriptor_ref = _put_json(
            store,
            tmp_path,
            name=f"{label}-descriptor.json",
            payload=base._canonical_bytes(descriptor),
            schema_version=(
                "harness.skill_descriptor.v2"
                if label == "replay"
                else "harness.skill_descriptor.v1"
            ),
        )
        entries.append(
            QualifiedSkillSnapshot(
                skill_ref=f"text2env.{label}@1.0.0",
                mcp_tool_name=f"text2env_{label}_v1_0_0",
                descriptor_ref=descriptor_ref,
                qualification_ref=descriptor.qualification_artifact,
                qualification_report_ref=report,
            )
        )
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=tuple(entries),
    )
    return _put_json(
        store,
        tmp_path,
        name="compile-replay-registry.json",
        payload=base._canonical_bytes(snapshot),
        schema_version="harness.registry_snapshot.v1",
    )


def _read(store: LocalArtifactStore, ref: ArtifactRef, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def test_same_golden_workflow_runs_qualified_compile_then_replay(tmp_path: Path) -> None:
    source = replay_fixture._fixture(tmp_path / "source")
    compile_settings = CompileApplicationSettings(
        state_root=tmp_path / "compile-state",
        external_catalog_roots=(tmp_path / "source",),
        allowed_asset_roots=(source.robotwin_root,),
        admission_date=date(2026, 9, 10),
        asset_library_root=tmp_path / "asset-library",
    )
    compile_application = create_compile_application(compile_settings)
    store = LocalArtifactStore(compile_application.artifact_root)
    replay_application, executor = _replay_application(
        tmp_path,
        store=store,
        source=source,
    )
    registry = _registry_snapshot(
        store,
        tmp_path,
        compile_application=compile_application,
        replay_application=replay_application,
    )
    objective = _put_json(
        store,
        tmp_path,
        name="objective.json",
        payload=base.OBJECTIVE_EVIDENCE.replace(b"Place a can on a plate.", REQUEST.encode()),
        schema_version="harness.world_fact_evidence.v1",
    )
    child_ids = iter((COMPILE_RUN_ID, REPLAY_RUN_ID))
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(
                create_text2env_compile_execution_binding(application=compile_application),
                create_text2env_replay_execution_binding(application=replay_application),
            )
        ),
        clock=lambda: base.NOW,
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        child_run_id_factory=lambda: next(child_ids),
    )

    revision_zero = harness.start(base._request(objective, registry))
    assert revision_zero.revision == 0
    catalog = compile_application.snapshot_asset_catalog(tmp_path / "source/catalog.json")
    shared_key = "qualified-step-001"
    compile_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("20000000-0000-4000-8000-000000000001"),
            principal_id=revision_zero.principal_id,
            workflow_run_id=revision_zero.workflow_run_id,
            idempotency_key=shared_key,
            expected_workflow_revision=0,
            expected_state_sha256=revision_zero.state_sha256,
            expected_receipt_head_sha256=revision_zero.receipt_head.sha256,
            skill_ref="text2env.compile@1.0.0",
            parameters=Text2EnvCompileInput(
                request=REQUEST,
                seed=7,
                asset_catalog=catalog,
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
    assert revision_one.revision == 1
    trusted = _read(store, revision_one.state_ref, TrustedWorldState)
    package_fact = next(fact for fact in trusted.facts if fact.key == "environment.package")
    replay_input = Text2EnvReplayInput(
        environment_package=package_fact.value,
        runtime_config=RuntimeConfig(
            precheck_steps=0,
            settle_steps=900,
            contact_window_steps=120,
            video_frames=120,
            fps=12,
        ),
    )
    replay_operation = harness.submit(
        RunCommandRequest(
            schema_version="harness.run_command_request.v1",
            command_id=UUID("20000000-0000-4000-8000-000000000002"),
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
            idempotency_key=shared_key,
            expected_workflow_revision=1,
            expected_state_sha256=revision_one.state_sha256,
            expected_receipt_head_sha256=revision_one.receipt_head.sha256,
            skill_ref="text2env.replay@1.0.0",
            parameters=replay_input.model_dump(mode="json"),
        )
    )

    assert replay_operation.status is RunStatus.SUCCEEDED
    assert replay_operation.committed_revision == 2
    assert len(executor.calls) == 1
    revision_two = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=revision_one.principal_id,
            workflow_run_id=revision_one.workflow_run_id,
        )
    )
    assert revision_two.revision == 2
    assert revision_two.child_runs == (COMPILE_RUN_ID, REPLAY_RUN_ID)
    replay_receipt = _read(store, revision_two.receipt_head, WorkflowOperationReceiptV2)
    assert replay_receipt.predecessor_receipt == revision_one.receipt_head
    assert replay_operation.tool_result is not None
    result = _read(store, replay_operation.tool_result, ToolResultV2)
    output = Text2EnvReplayOutput.model_validate(result.typed_output)
    assert result.fresh_observations == ()
    assert len({(ref.sha256, ref.media_type, ref.schema_version) for ref in output.replay_artifacts}) == len(
        output.replay_artifacts
    )
    tool_receipt = _read(store, result.trusted_receipt, TrustedToolReceiptV2)
    base_state = _read(store, tool_receipt.base_state, TrustedWorldState)
    linked_package = next(fact for fact in base_state.facts if fact.key == "environment.package")
    assert linked_package.value == replay_input.environment_package.model_dump(mode="json")
    assert linked_package.source_artifact == _read(
        store,
        revision_one.receipt_head,
        WorkflowOperationReceiptV2,
    ).tool_receipt
    assert result.state_delta is not None
    delta = _read(store, result.state_delta, StateDeltaV2)
    assert len(delta.mutations) == 1
    assert delta.mutations[0].key == "environment.replay.result"
    assert delta.mutations[0].fact is not None
    assert delta.mutations[0].fact.value == output.model_dump(mode="json")
    assert hashlib.sha256(store.resolve(result.state_delta).path.read_bytes()).hexdigest() == (
        result.state_delta.sha256
    )
