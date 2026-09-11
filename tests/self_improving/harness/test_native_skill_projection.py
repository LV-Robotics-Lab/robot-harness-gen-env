"""Typed native qualifications enter Registry v1 and Golden without relabeling."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.golden_execution import create_golden_execution_catalog
from self_improving.harness.golden_run import GoldenRunCorruptionError, GoldenRunHarness
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.mcp_adapter import MCPToolCall, create_harness_mcp_adapter
from self_improving.harness.native_skill_projection import (
    NativeSkillProjectionError,
    issue_native_skill_projection,
    load_native_skill_projection,
    publish_native_registry_snapshot,
)
from self_improving.harness.qualification import QualificationReportV1
from self_improving.harness.registry import (
    HandlerResult,
    SkillRegistry,
    StaticDependencyResolver,
    _EvidenceInvariantRegistration,
)
from self_improving.harness.run_store import SQLiteRunStore
from self_improving.harness.schema_catalog import schema_model
from self_improving.harness.schemas.common import (
    ArtifactRef,
    RegisteredSkillDescriptor,
    SkillDescriptorV2,
)
from self_improving.harness.typed_qualification_admission import (
    freeze_typed_qualification_admission,
    verify_typed_qualification_admission,
)
from tests.self_improving.harness import test_golden_run as golden_base
from tests.self_improving.harness.test_typed_qualification_admission import (
    STAMP,
    _fixture,
)


def _read(store: LocalArtifactStore, ref: ArtifactRef, model: type[BaseModel]):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _admission(tmp_path: Path, kind: str):
    fixture = _fixture(tmp_path / f"{kind}-qualification", kind)
    return fixture, freeze_typed_qualification_admission(
        artifact_store=fixture.store,
        child_authority=fixture.authority,
        descriptor_ref=fixture.descriptor,
        qualification_ref=fixture.qualification,
        qualification_report_ref=fixture.report,
        implementation_members=fixture.implementation,
    )


@dataclass(frozen=True)
class _ProjectionOnlyNativeApplication:
    descriptor: RegisteredSkillDescriptor

    @property
    def skills(self):
        return (self.descriptor,)

    def invoke(self, parameters, *, run_id):  # pragma: no cover - listing-only fixture
        raise AssertionError((parameters, run_id))

    invoke_typed = invoke


class _KernelHandler:
    def __init__(self, output: BaseModel, output_ref: ArtifactRef) -> None:
        self.output = output
        self.output_ref = output_ref

    def __call__(self, _parameters, _context):
        return HandlerResult(self.output, (self.output_ref,))


class _TypedRegistrationPolicy:
    def __init__(
        self,
        *,
        descriptor: SkillDescriptorV2,
        handler: _KernelHandler,
        resolver: StaticDependencyResolver,
        qualification_bytes: bytes,
        report_bytes: bytes,
    ) -> None:
        self.descriptor = descriptor
        self.handler = handler
        self.resolver = resolver
        self.qualification_bytes = qualification_bytes
        self.report_bytes = report_bytes

    def verify_and_build(self, request, *, artifact_resolver):
        if request != self.descriptor.qualification_artifact:
            raise ValueError("wrong typed qualification")
        artifact_resolver.resolve(request)
        return _EvidenceInvariantRegistration(
            self.descriptor,
            self.handler,
            self.resolver,
            self.qualification_bytes,
            self.report_bytes,
        )


class _KernelNativeApplication:
    """Real Registry/SQLite child kernel behind the public projection API."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        authority: SQLiteRunStore,
        admission,
        advertised_descriptor: SkillDescriptorV2 | None = None,
        child_implementation_sha256: str | None = None,
    ) -> None:
        descriptor = _read(store, admission.admission.descriptor, SkillDescriptorV2)
        first = admission.admission.candidate_intents[0]
        output_model = schema_model(descriptor.output_schema).model_validate(first.run_state.output)
        dependencies = first.invocation.dependencies
        resolver = StaticDependencyResolver({admission.admission.skill_ref: dependencies})
        child_descriptor = (
            descriptor
            if child_implementation_sha256 is None
            else descriptor.model_copy(
                update={"implementation_sha256": child_implementation_sha256}
            )
        )
        registration = _TypedRegistrationPolicy(
            descriptor=child_descriptor,
            handler=_KernelHandler(output_model, first.output),
            resolver=resolver,
            qualification_bytes=store.resolve(admission.admission.qualification).path.read_bytes(),
            report_bytes=store.resolve(admission.admission.qualification_report).path.read_bytes(),
        )
        self.registry = SkillRegistry(
            artifact_resolver=store,
            dependency_resolver=resolver,
            event_sink=SQLiteEventJournal(authority.path),
            clock=lambda: STAMP,
            run_id_factory=lambda: UUID("91000000-0000-4000-8000-000000000099"),
            run_store=authority,
            evidence_invariant_policy=registration,
        )
        self.registry.register_evidence_invariant(child_descriptor.qualification_artifact)
        self._advertised = advertised_descriptor or descriptor

    @property
    def skills(self):
        return (self._advertised,)

    def invoke(self, parameters, *, run_id):
        skill_id, version = f"{self._advertised.skill_id}@{self._advertised.version}".split("@")
        return self.registry.invoke_with_run_id(
            skill_id,
            version,
            parameters.model_dump(mode="json"),
            run_id=run_id,
        )

    invoke_typed = invoke


def _start_harness(
    tmp_path: Path,
    store: LocalArtifactStore,
    registry: ArtifactRef,
    bindings,
    *,
    child_run_id: UUID = UUID("92000000-0000-4000-8000-000000000001"),
):
    objective = golden_base._put(
        store,
        tmp_path,
        name="objective.json",
        payload=golden_base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(bindings=bindings),
        clock=lambda: golden_base.NOW,
        workflow_id_factory=lambda: golden_base.WORKFLOW_ID,
        child_run_id_factory=lambda: child_run_id,
    )
    return harness, harness.start(golden_base._request(objective, registry))


def test_four_native_typed_reports_project_as_four_exact_mcp_tools(tmp_path: Path) -> None:
    central = LocalArtifactStore(tmp_path / "central-cas")
    projections = []
    native_report_schemas = set()
    for kind in ("compile", "replay", "observe", "validate"):
        fixture, issued = _admission(tmp_path / kind, kind)
        shutil.copytree(fixture.store.root, central.root, dirs_exist_ok=True)
        admission = verify_typed_qualification_admission(
            artifact_store=central,
            admission=issued.admission,
        )
        descriptor_model = (
            SkillDescriptorV2
            if admission.admission.descriptor.schema_version == "harness.skill_descriptor.v2"
            else schema_model("harness.skill_descriptor.v1")
        )
        descriptor = _read(central, admission.admission.descriptor, descriptor_model)
        projection = issue_native_skill_projection(
            artifact_store=central,
            admission=admission,
            native_application=_ProjectionOnlyNativeApplication(descriptor),
            child_authority=SQLiteRunStore(tmp_path / kind / "production.sqlite3"),
        )
        projections.append(projection)
        generic_report = _read(
            central,
            projection.qualification_report_ref,
            QualificationReportV1,
        )
        evidence = generic_report.checks[0].evidence
        assert evidence["typed_admission"] == admission.admission_ref.model_dump(mode="json")
        assert evidence["native_qualification_report"] == (
            admission.admission.qualification_report.model_dump(mode="json")
        )
        native_report_schemas.add(admission.admission.qualification_report.schema_version)

    registry = publish_native_registry_snapshot(
        artifact_store=central,
        projections=tuple(projections),
    )
    harness, started = _start_harness(
        tmp_path,
        central,
        registry,
        tuple(projection.binding for projection in projections),
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=registry,
    )

    assert tuple(tool.name for tool in adapter.list_tools(registry)[3:]) == (
        "genesis_observe_v3_0_0",
        "image2env_compile_v2_0_0",
        "image2env_replay_v2_0_0",
        "image2env_validate_v2_0_0",
    )
    assert all(tool.name != "skill.invoke" for tool in adapter.list_tools(registry))
    assert native_report_schemas == {
        "harness.image2env_genesis_skill_qualification_report.v1",
        "harness.genesis_scene_skill_qualification_report.v2",
        "harness.genesis_fresh_capture_qualification.v1",
        "harness.genesis_scene_validation_qualification.v1",
    }


def test_projected_native_child_executes_and_recovers_through_golden_and_mcp(
    tmp_path: Path,
) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "production.sqlite3")
    application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
    )
    projection = issue_native_skill_projection(
        artifact_store=fixture.store,
        admission=admission,
        native_application=application,
        child_authority=authority,
    )
    registry = publish_native_registry_snapshot(
        artifact_store=fixture.store,
        projections=(projection,),
    )
    harness, started = _start_harness(
        tmp_path,
        fixture.store,
        registry,
        (projection.binding,),
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=registry,
    )
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "93000000-0000-4000-8000-000000000001",
        "workflow_run_id": str(started.workflow_run_id),
        "idempotency_key": "native-replay-through-mcp-001",
        "expected_workflow_revision": started.revision,
        "expected_state_sha256": started.state_sha256,
        "expected_receipt_head_sha256": started.receipt_head.sha256,
        "parameters": admission.admission.candidate_intents[0].invocation.effective_parameters,
    }

    result = adapter.call_tool(
        MCPToolCall(name="image2env_replay_v2_0_0", arguments=arguments)
    )
    assert result.is_error is False
    assert result.structured_content["status"] == "succeeded"
    child = authority.read_controlled_lifecycle(
        UUID("92000000-0000-4000-8000-000000000001")
    )
    assert child.intent is not None
    assert child.intent.implementation_sha256 == admission.admission.implementation_sha256
    assert child.intent.qualification_sha256 == admission.admission.qualification.sha256

    restarted_application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
    )
    restarted_projection = load_native_skill_projection(
        artifact_store=fixture.store,
        admission_ref=projection.admission_ref,
        descriptor_ref=projection.descriptor_ref,
        qualification_ref=projection.qualification_ref,
        qualification_report_ref=projection.qualification_report_ref,
        native_application=restarted_application,
        child_authority=authority,
    )
    restarted_harness = GoldenRunHarness(
        artifact_store=fixture.store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        execution_catalog=create_golden_execution_catalog(
            bindings=(restarted_projection.binding,)
        ),
        clock=lambda: golden_base.NOW,
    )
    restarted_adapter = create_harness_mcp_adapter(
        harness=restarted_harness,
        principal_id=started.principal_id,
        registry_snapshot=registry,
    )
    assert restarted_adapter.call_tool(
        MCPToolCall(name="image2env_replay_v2_0_0", arguments=arguments)
    ) == result


def test_projection_rejects_substitution_foreign_admission_and_missing_source(
    tmp_path: Path,
) -> None:
    replay_fixture, replay = _admission(tmp_path / "replay", "replay")
    observe_fixture, observe = _admission(tmp_path / "observe", "observe")
    with pytest.raises(TypeError, match="verified typed admission"):
        issue_native_skill_projection(
            artifact_store=replay_fixture.store,
            admission=replay.admission,
            native_application=_ProjectionOnlyNativeApplication(
                _read(replay_fixture.store, replay.admission.descriptor, SkillDescriptorV2)
            ),
            child_authority=SQLiteRunStore(tmp_path / "substitution.sqlite3"),
        )
    with pytest.raises(NativeSkillProjectionError, match="foreign"):
        issue_native_skill_projection(
            artifact_store=replay_fixture.store,
            admission=replay,
            native_application=_ProjectionOnlyNativeApplication(
                _read(observe_fixture.store, observe.admission.descriptor, SkillDescriptorV2)
            ),
            child_authority=SQLiteRunStore(tmp_path / "foreign.sqlite3"),
        )

    application = _ProjectionOnlyNativeApplication(
        _read(replay_fixture.store, replay.admission.descriptor, SkillDescriptorV2)
    )
    authority = SQLiteRunStore(tmp_path / "relocated.sqlite3")
    projection = issue_native_skill_projection(
        artifact_store=replay_fixture.store,
        admission=replay,
        native_application=application,
        child_authority=authority,
    )
    relocated_root = tmp_path / "relocated-cas"
    shutil.copytree(replay_fixture.store.root, relocated_root)
    relocated = LocalArtifactStore(relocated_root)
    assert load_native_skill_projection(
        artifact_store=relocated,
        admission_ref=projection.admission_ref,
        descriptor_ref=projection.descriptor_ref,
        qualification_ref=projection.qualification_ref,
        qualification_report_ref=projection.qualification_report_ref,
        native_application=application,
        child_authority=authority,
    ).registry_entry == projection.registry_entry
    relocated.resolve(projection.implementation_sources[0]).path.unlink()
    with pytest.raises(NativeSkillProjectionError, match="source"):
        load_native_skill_projection(
            artifact_store=relocated,
            admission_ref=projection.admission_ref,
            descriptor_ref=projection.descriptor_ref,
            qualification_ref=projection.qualification_ref,
            qualification_report_ref=projection.qualification_report_ref,
            native_application=application,
            child_authority=authority,
        )


def test_golden_hook_rejects_native_child_implementation_drift(tmp_path: Path) -> None:
    fixture, admission = _admission(tmp_path, "replay")
    authority = SQLiteRunStore(tmp_path / "drifted-production.sqlite3")
    application = _KernelNativeApplication(
        store=fixture.store,
        authority=authority,
        admission=admission,
        child_implementation_sha256="f" * 64,
    )
    projection = issue_native_skill_projection(
        artifact_store=fixture.store,
        admission=admission,
        native_application=application,
        child_authority=authority,
    )
    registry = publish_native_registry_snapshot(
        artifact_store=fixture.store,
        projections=(projection,),
    )
    harness, started = _start_harness(
        tmp_path,
        fixture.store,
        registry,
        (projection.binding,),
        child_run_id=UUID("94000000-0000-4000-8000-000000000001"),
    )
    adapter = create_harness_mcp_adapter(
        harness=harness,
        principal_id=started.principal_id,
        registry_snapshot=registry,
    )
    arguments = {
        "schema_version": "harness.run_command_request.v1",
        "command_id": "95000000-0000-4000-8000-000000000001",
        "workflow_run_id": str(started.workflow_run_id),
        "idempotency_key": "drifted-native-child-001",
        "expected_workflow_revision": started.revision,
        "expected_state_sha256": started.state_sha256,
        "expected_receipt_head_sha256": started.receipt_head.sha256,
        "parameters": admission.admission.candidate_intents[0].invocation.effective_parameters,
    }
    with pytest.raises(GoldenRunCorruptionError, match="native child"):
        adapter.call_tool(MCPToolCall(name="image2env_replay_v2_0_0", arguments=arguments))
