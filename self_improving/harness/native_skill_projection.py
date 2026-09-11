"""Honest Registry-v1 projection for Skills with native typed qualification reports.

The adapter report proves only projection/admission identity.  It keeps the native
typed report intact and delegates production execution to that Skill's own durable
application and SQLite child authority.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    SkillEvidencePolicy,
    _create_golden_execution_binding,
    _create_golden_execution_evidence_hook,
)
from .golden_image2env_execution import _require_native_image_execution_binding
from .golden_image2env_policy import NativeImageEvidencePolicy
from .golden_native_terminal_resolver import (
    NativeTerminalEvidenceRoots,
    NativeTerminalResolver,
)
from .qualification import QualificationCheckV1, QualificationReportV1
from .registry import ControlledRunPendingError
from .run_store import ControlledRunLifecycle, SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    Invocation,
    RegisteredSkillDescriptor,
    RunState,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.registry_snapshot import QualifiedSkillSnapshot, RegistrySnapshot
from .schemas.typed_qualification_admission import TypedQualificationAdmissionV1
from .schemas.workflow_operation import QualifiedSkillExecutionV2
from .typed_qualification_admission import (
    VerifiedTypedQualificationAdmission,
    verify_typed_qualification_admission,
)

_PROJECTION_TOKEN = object()
_EMPTY_DIRECT_DEPENDENCY_TREE_SHA256 = hashlib.sha256(b"").hexdigest()
_ADAPTER_CASE_ID = "native-typed-admission-projection"
_ADAPTER_REGRESSION_COMMAND = (
    "pytest -q tests/self_improving/harness/test_native_skill_projection.py"
)
_SOURCE_FILES = (
    ("golden_execution.py", "golden-execution.py"),
    ("golden_image2env_execution.py", "golden-image2env-execution.py"),
    ("golden_image2env_policy.py", "golden-image2env-policy.py"),
    ("golden_native_terminal_resolver.py", "golden-native-terminal-resolver.py"),
    ("golden_run.py", "golden-run.py"),
    ("native_terminal_admission.py", "native-terminal-admission.py"),
    ("native_skill_projection.py", "native-skill-projection.py"),
    (
        "schemas/native_terminal_admission.py",
        "native-terminal-admission-schema.py",
    ),
    ("schemas/typed_qualification_admission.py", "typed-qualification-admission-schema.py"),
    ("typed_qualification_admission.py", "typed-qualification-admission.py"),
)
_NATIVE_SKILLS = frozenset(
    {
        "image2env.compile@2.0.0",
        "image2env.replay@2.0.0",
        "genesis.observe@3.0.0",
        "image2env.validate@2.0.0",
    }
)


class NativeSkillProjectionError(ValueError):
    """A native typed qualification cannot be projected without changing its meaning."""


@dataclass(frozen=True)
class _VerifiedProjectionDocuments:
    admission: VerifiedTypedQualificationAdmission
    native_descriptor: RegisteredSkillDescriptor
    adapter_descriptor: RegisteredSkillDescriptor
    adapter_qualification: SkillQualification
    adapter_report: QualificationReportV1
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    report_ref: ArtifactRef
    implementation_sources: tuple[ArtifactRef, ...]
    registry_entry: QualifiedSkillSnapshot


class NativeSkillProjection:
    """Opaque verified projection and its sealed Golden execution binding."""

    __slots__ = (
        "_admission_ref",
        "_binding",
        "_descriptor_ref",
        "_implementation_sources",
        "_qualification_ref",
        "_qualification_report_ref",
        "_registry_entry",
        "_terminal_admission_enabled",
        "_token",
    )

    def __init__(
        self,
        *,
        admission_ref: ArtifactRef,
        descriptor_ref: ArtifactRef,
        qualification_ref: ArtifactRef,
        qualification_report_ref: ArtifactRef,
        implementation_sources: tuple[ArtifactRef, ...],
        registry_entry: QualifiedSkillSnapshot,
        binding: GoldenExecutionBinding,
        terminal_admission_enabled: bool = False,
        _token: object,
    ) -> None:
        if _token is not _PROJECTION_TOKEN:
            raise TypeError("native projections are issued only by their deep verifier")
        object.__setattr__(self, "_admission_ref", admission_ref)
        object.__setattr__(self, "_descriptor_ref", descriptor_ref)
        object.__setattr__(self, "_qualification_ref", qualification_ref)
        object.__setattr__(self, "_qualification_report_ref", qualification_report_ref)
        object.__setattr__(self, "_implementation_sources", implementation_sources)
        object.__setattr__(self, "_registry_entry", registry_entry)
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_terminal_admission_enabled", terminal_admission_enabled)
        object.__setattr__(self, "_token", _token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("native projections are immutable")

    @property
    def admission_ref(self) -> ArtifactRef:
        return self._admission_ref

    @property
    def descriptor_ref(self) -> ArtifactRef:
        return self._descriptor_ref

    @property
    def qualification_ref(self) -> ArtifactRef:
        return self._qualification_ref

    @property
    def qualification_report_ref(self) -> ArtifactRef:
        return self._qualification_report_ref

    @property
    def implementation_sources(self) -> tuple[ArtifactRef, ...]:
        return self._implementation_sources

    @property
    def registry_entry(self) -> QualifiedSkillSnapshot:
        return self._registry_entry

    @property
    def binding(self) -> GoldenExecutionBinding:
        return self._binding

    @property
    def terminal_admission_enabled(self) -> bool:
        return self._terminal_admission_enabled

    def _is_verified_projection(self) -> bool:
        return self._token is _PROJECTION_TOKEN


@dataclass(frozen=True)
class _ProjectionAuthority:
    artifact_store: LocalArtifactStore
    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef
    native_binding: GoldenExecutionBinding
    terminal_resolver: NativeTerminalResolver | None

    def verify(self) -> _VerifiedProjectionDocuments:
        return _verify_projection_documents(
            artifact_store=self.artifact_store,
            admission_ref=self.admission_ref,
            descriptor_ref=self.descriptor_ref,
            qualification_ref=self.qualification_ref,
            qualification_report_ref=self.qualification_report_ref,
            native_binding=self.native_binding,
            terminal_resolver=self.terminal_resolver,
        )


@dataclass(frozen=True)
class _NativeProjectedApplication:
    authority: _ProjectionAuthority
    native_application: object
    child_authority: SQLiteRunStore
    descriptor: RegisteredSkillDescriptor
    policy: SkillEvidencePolicy

    @property
    def skill_ref(self) -> str:
        return f"{self.descriptor.skill_id}@{self.descriptor.version}"

    @property
    def artifact_root(self) -> Path:
        return self.authority.artifact_store.root

    @property
    def authority_path(self) -> Path:
        return self.child_authority.path

    @property
    def skills(self) -> tuple[RegisteredSkillDescriptor, ...]:
        self.authority.verify()
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self.authority.verify()
        _require(type(parameters) is self.policy.input_model, "native adapter input type changed")
        try:
            returned = self.native_application.invoke(parameters, run_id=run_id)
        except ControlledRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error
        persisted = self.child_authority.read_run_state(run_id)
        _require(returned == persisted, "native application returned a non-authoritative terminal")
        return cast(RunState, returned)

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.child_authority.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.child_authority.read_run_state(run_id)


@dataclass(frozen=True)
class _NativeProjectionEvidenceHook:
    authority: _ProjectionAuthority

    def verify_registry_entry(self, entry: QualifiedSkillSnapshot) -> None:
        _require(
            type(entry) is QualifiedSkillSnapshot,
            "native projection received a foreign registry entry",
        )
        verified = self.authority.verify()
        _require(entry == verified.registry_entry, "native projection registry entry changed")

    def verify_child_lifecycle(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        command_parameters: Mapping[str, object],
        qualified: QualifiedSkillExecutionV2,
    ) -> tuple[ArtifactRef, ...]:
        verified = self.authority.verify()
        admission = verified.admission.admission
        descriptor = verified.native_descriptor
        adapter = verified.adapter_descriptor
        intent = lifecycle.intent
        state = lifecycle.terminal
        _require(intent is not None and state is not None, "native child intent is incomplete")
        skill_id, version = skill_ref.split("@", maxsplit=1)
        parameters_digest = _canonical_sha256(command_parameters)
        _require(
            qualified.skill_ref == skill_ref
            and qualified.descriptor == verified.descriptor_ref
            and qualified.implementation_sha256 == adapter.implementation_sha256
            and qualified.qualification_artifact == verified.qualification_ref
            and qualified.qualification_report == verified.report_ref,
            "native child adapter execution identity changed",
        )
        policy = self.authority.native_binding.policy
        _require(
            qualified.evidence_policy_id == policy.evidence_policy_id
            and qualified.evidence_policy_version == policy.evidence_policy_version
            and qualified.evidence_policy_implementation_sha256
            == policy.evidence_policy_implementation_sha256,
            "native child evidence policy identity changed",
        )
        _require(
            intent.run_id == child_run_id
            and intent.skill_id == skill_id
            and intent.skill_version == version
            and intent.input_schema == descriptor.input_schema
            and intent.raw_parameters_sha256 == parameters_digest
            and intent.effective_parameters_sha256 == parameters_digest
            and intent.descriptor_sha256 == _canonical_sha256(descriptor.model_dump(mode="json"))
            and intent.implementation_sha256 == admission.implementation_sha256
            and intent.qualification_sha256 == admission.qualification.sha256
            and intent.max_attempts == descriptor.max_attempts
            and state.run_id == child_run_id
            and state.skill_id == skill_id
            and state.skill_version == version
            and state.max_attempts == descriptor.max_attempts,
            "native child differs from its typed admission intent",
        )
        invocation = lifecycle.invocation
        _require(
            invocation is None
            or (
                invocation.effective_parameters == command_parameters
                and invocation.dependencies == intent.dependencies
            ),
            "native child invocation differs from its typed admission intent",
        )
        resolver = self.authority.terminal_resolver
        if resolver is None or state.status.value != "succeeded":
            return ()
        roots = resolver.admit(
            lifecycle=lifecycle,
            skill_ref=skill_ref,
            child_run_id=child_run_id,
        )
        return roots.as_artifacts()

    def verify_committed_evidence(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
    ) -> None:
        resolver = self.authority.terminal_resolver
        if resolver is None:
            _require(not evidence, "qualification-only projection has terminal evidence")
            return
        state = lifecycle.terminal
        _require(state is not None, "committed native lifecycle lost its terminal")
        if state.status.value != "succeeded":
            _require(not evidence, "unsuccessful native terminal has admission evidence")
            return
        roots = _terminal_roots(evidence)
        resolver.reload(
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=roots,
        )

    def resolve_artifact_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
        sha256: str,
    ) -> ArtifactRef | None:
        resolver = self.authority.terminal_resolver
        if resolver is None:
            _require(not evidence, "qualification-only projection has terminal evidence")
            return None
        return resolver.resolve_artifact_member(
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=_terminal_roots(evidence),
            sha256=sha256,
        )


def issue_native_skill_projection(
    *,
    artifact_store: LocalArtifactStore,
    admission: VerifiedTypedQualificationAdmission,
    native_binding: GoldenExecutionBinding,
    terminal_resolver: NativeTerminalResolver | None = None,
) -> NativeSkillProjection:
    """Issue adapter documents only from one opaque, freshly reverified admission."""

    _require_exact_inputs(artifact_store, native_binding, admission, terminal_resolver)
    if (
        type(admission) is not VerifiedTypedQualificationAdmission
        or not admission._is_verifier_issued()
    ):
        raise TypeError("admission must be a verified typed admission capability")
    try:
        reverified = verify_typed_qualification_admission(
            artifact_store=artifact_store,
            admission=admission.admission,
        )
        _require(
            reverified.admission == admission.admission
            and reverified.admission_ref == admission.admission_ref,
            "verified typed admission changed during projection",
        )
        native_descriptor = _native_descriptor(
            reverified,
            artifact_store,
            native_binding.application,
        )
        sources = _publish_implementation_sources(artifact_store)
        documents = _build_documents(
            reverified,
            native_descriptor,
            sources,
            cast(NativeImageEvidencePolicy, native_binding.policy),
            terminal_resolver,
        )
        report_ref = _publish_model(
            artifact_store,
            documents.adapter_report,
            name="native-skill-projection-report.json",
            schema_version="harness.skill_qualification_report.v1",
        )
        qualification_ref = _publish_model(
            artifact_store,
            documents.adapter_qualification,
            name="native-skill-projection-qualification.json",
            schema_version="harness.skill_qualification.v1",
        )
        descriptor_ref = _publish_model(
            artifact_store,
            documents.adapter_descriptor,
            name="native-skill-projection-descriptor.json",
            schema_version=_descriptor_schema(documents.adapter_descriptor),
        )
        _require(
            report_ref == documents.report_ref
            and qualification_ref == documents.qualification_ref
            and descriptor_ref == documents.descriptor_ref,
            "native adapter publication identity changed",
        )
        return load_native_skill_projection(
            artifact_store=artifact_store,
            admission_ref=reverified.admission_ref,
            descriptor_ref=descriptor_ref,
            qualification_ref=qualification_ref,
            qualification_report_ref=report_ref,
            native_binding=native_binding,
            terminal_resolver=terminal_resolver,
        )
    except (
        ArtifactResolutionError,
        AttributeError,
        KeyError,
        ValidationError,
        TypeError,
        ValueError,
    ) as error:
        raise NativeSkillProjectionError(f"native projection issuance failed: {error}") from error


def load_native_skill_projection(
    *,
    artifact_store: LocalArtifactStore,
    admission_ref: ArtifactRef,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
    native_binding: GoldenExecutionBinding,
    terminal_resolver: NativeTerminalResolver | None = None,
) -> NativeSkillProjection:
    """Recover a projection from CAS by recomputing admission and source identities."""

    _require_exact_inputs(artifact_store, native_binding, None, terminal_resolver)
    authority = _ProjectionAuthority(
        artifact_store=artifact_store,
        admission_ref=admission_ref,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=qualification_report_ref,
        native_binding=native_binding,
        terminal_resolver=terminal_resolver,
    )
    try:
        verified = authority.verify()
        policy = native_binding.policy
        source_application = native_binding.application
        child_authority = native_binding.child_authority
        _require(
            source_application is not None and child_authority is not None,
            "sealed native binding lost its execution authority",
        )
        application = _NativeProjectedApplication(
            authority=authority,
            native_application=source_application,
            child_authority=child_authority,
            descriptor=verified.adapter_descriptor,
            policy=policy,
        )
        hook = _create_golden_execution_evidence_hook(
            _NativeProjectionEvidenceHook(authority=authority)
        )
        binding = _create_golden_execution_binding(
            policy=policy,
            application=application,
            child_authority=child_authority,
            evidence_hook=hook,
        )
        return NativeSkillProjection(
            admission_ref=admission_ref,
            descriptor_ref=descriptor_ref,
            qualification_ref=qualification_ref,
            qualification_report_ref=qualification_report_ref,
            implementation_sources=verified.implementation_sources,
            registry_entry=verified.registry_entry,
            binding=binding,
            terminal_admission_enabled=terminal_resolver is not None,
            _token=_PROJECTION_TOKEN,
        )
    except (
        ArtifactResolutionError,
        AttributeError,
        KeyError,
        ValidationError,
        TypeError,
        ValueError,
    ) as error:
        raise NativeSkillProjectionError(
            f"native projection source or evidence verification failed: {error}"
        ) from error


def publish_native_registry_snapshot(
    *,
    artifact_store: LocalArtifactStore,
    projections: tuple[NativeSkillProjection, ...],
) -> ArtifactRef:
    """Freeze exact projected entries into the unchanged RegistrySnapshotV1 contract."""

    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    if type(projections) is not tuple:
        raise TypeError("projections must be an exact tuple")
    entries: list[QualifiedSkillSnapshot] = []
    for projection in projections:
        if (
            type(projection) is not NativeSkillProjection
            or not projection._is_verified_projection()
        ):
            raise TypeError("registry entries require verified native projections")
        binding = projection.binding
        application = binding.application
        if application is None or application.artifact_root != artifact_store.root:
            raise NativeSkillProjectionError("projection and Registry must share one exact CAS")
        authority = cast(_NativeProjectedApplication, application).authority
        verified = authority.verify()
        _require(
            verified.registry_entry == projection.registry_entry,
            "native projection changed before Registry publication",
        )
        entries.append(verified.registry_entry)
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=tuple(sorted(entries, key=lambda entry: entry.mcp_tool_name)),
    )
    return _publish_model(
        artifact_store,
        snapshot,
        name="registry-snapshot.json",
        schema_version="harness.registry_snapshot.v1",
    )


def publish_native_terminal_registry_snapshot(
    *,
    artifact_store: LocalArtifactStore,
    projections: tuple[NativeSkillProjection, ...],
) -> ArtifactRef:
    """Freeze only projections whose successful terminals add deep admission roots."""

    if type(projections) is not tuple or not projections:
        raise TypeError("terminal Registry requires a non-empty exact projection tuple")
    if any(
        type(projection) is not NativeSkillProjection
        or not projection._is_verified_projection()
        or not projection.terminal_admission_enabled
        for projection in projections
    ):
        raise TypeError("terminal Registry requires terminal-enabled native projections")
    return publish_native_registry_snapshot(
        artifact_store=artifact_store,
        projections=projections,
    )


def _verify_projection_documents(
    *,
    artifact_store: LocalArtifactStore,
    admission_ref: ArtifactRef,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
    native_binding: GoldenExecutionBinding,
    terminal_resolver: NativeTerminalResolver | None,
) -> _VerifiedProjectionDocuments:
    _require_native_image_execution_binding(native_binding)
    admission = _read_native_admission(artifact_store, admission_ref)
    verified_admission = verify_typed_qualification_admission(
        artifact_store=artifact_store,
        admission=admission,
    )
    _require(
        verified_admission.admission_ref == admission_ref,
        "typed admission CAS identity changed",
    )
    _require(
        terminal_resolver is None
        or terminal_resolver.owns(
            binding=native_binding,
            qualification=verified_admission,
        ),
        "terminal resolver does not own this native binding and admission",
    )
    native_descriptor = _native_descriptor(
        verified_admission,
        artifact_store,
        native_binding.application,
    )
    sources = _current_implementation_sources()
    for source in sources:
        artifact_store.resolve(source)
    expected = _build_documents(
        verified_admission,
        native_descriptor,
        sources,
        cast(NativeImageEvidencePolicy, native_binding.policy),
        terminal_resolver,
    )
    _require(
        descriptor_ref == expected.descriptor_ref
        and qualification_ref == expected.qualification_ref
        and qualification_report_ref == expected.report_ref,
        "native projection document reference changed",
    )
    adapter_report = _read_canonical_model(
        artifact_store,
        qualification_report_ref,
        QualificationReportV1,
    )
    adapter_qualification = _read_canonical_model(
        artifact_store,
        qualification_ref,
        SkillQualification,
    )
    descriptor_model = (
        SkillDescriptor
        if descriptor_ref.schema_version == "harness.skill_descriptor.v1"
        else SkillDescriptorV2
    )
    adapter_descriptor = cast(
        RegisteredSkillDescriptor,
        _read_canonical_model(artifact_store, descriptor_ref, descriptor_model),
    )
    _require(
        adapter_report == expected.adapter_report
        and adapter_qualification == expected.adapter_qualification
        and adapter_descriptor == expected.adapter_descriptor,
        "native projection documents changed",
    )
    return expected


def _read_native_admission(
    artifact_store: LocalArtifactStore,
    admission_ref: ArtifactRef,
) -> TypedQualificationAdmissionV1:
    _require(
        admission_ref.schema_version == "harness.typed_qualification_admission.v1",
        "typed admission reference has the wrong schema",
    )
    payload = artifact_store.resolve(admission_ref).path.read_bytes()
    admission = TypedQualificationAdmissionV1.model_validate_json(payload)
    _require(payload == _canonical_bytes(admission), "typed admission bytes are not canonical")
    return admission


def _native_descriptor(
    admission: VerifiedTypedQualificationAdmission,
    artifact_store: LocalArtifactStore,
    native_application: object,
) -> RegisteredSkillDescriptor:
    descriptor_ref = admission.admission.descriptor
    descriptor_model = (
        SkillDescriptor
        if descriptor_ref.schema_version == "harness.skill_descriptor.v1"
        else SkillDescriptorV2
    )
    descriptor = cast(
        RegisteredSkillDescriptor,
        descriptor_model.model_validate_json(
            artifact_store.resolve(descriptor_ref).path.read_bytes()
        ),
    )
    skills = getattr(native_application, "skills")
    _require(
        type(skills) is tuple and skills == (descriptor,),
        "foreign native application descriptor differs from typed admission",
    )
    _require(
        admission.admission.skill_ref in _NATIVE_SKILLS,
        "typed admission names an unsupported native projection",
    )
    getattr(native_application, "invoke")
    return descriptor


def _build_documents(
    admission: VerifiedTypedQualificationAdmission,
    native_descriptor: RegisteredSkillDescriptor,
    sources: tuple[ArtifactRef, ...],
    policy: NativeImageEvidencePolicy,
    terminal_resolver: NativeTerminalResolver | None,
) -> _VerifiedProjectionDocuments:
    policy_identity = {
        "id": policy.evidence_policy_id,
        "version": policy.evidence_policy_version,
        "implementation_sha256": policy.evidence_policy_implementation_sha256,
    }
    terminal_identity = None if terminal_resolver is None else terminal_resolver.identity_sha256
    implementation_sha256 = _canonical_sha256(
        {
            "typed_admission": admission.admission_ref.model_dump(mode="json"),
            "native_evidence_policy": policy_identity,
            "native_terminal_resolver_sha256": terminal_identity,
            "sources": [source.model_dump(mode="json") for source in sources],
        }
    )
    evidence = {
        "domain": "adapter_has_no_direct_scene_gen_or_ledger_contract_dependency",
        "scene_gen_tree_sha256": _EMPTY_DIRECT_DEPENDENCY_TREE_SHA256,
        "ledger_contract_tree_sha256": _EMPTY_DIRECT_DEPENDENCY_TREE_SHA256,
        "typed_admission": admission.admission_ref.model_dump(mode="json"),
        "native_descriptor": admission.admission.descriptor.model_dump(mode="json"),
        "native_qualification": admission.admission.qualification.model_dump(mode="json"),
        "native_qualification_report": (
            admission.admission.qualification_report.model_dump(mode="json")
        ),
        "native_evidence_policy": policy_identity,
        "native_terminal_resolver_sha256": terminal_identity,
        "implementation_sources": [source.model_dump(mode="json") for source in sources],
    }
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=admission.admission.skill_ref,
        status="pass",
        deterministic_case_id=_ADAPTER_CASE_ID,
        regression_command=_ADAPTER_REGRESSION_COMMAND,
        implementation_sha256=implementation_sha256,
        scene_gen_tree_sha256=_EMPTY_DIRECT_DEPENDENCY_TREE_SHA256,
        ledger_contract_tree_sha256=_EMPTY_DIRECT_DEPENDENCY_TREE_SHA256,
        checks=(
            QualificationCheckV1(name="native_typed_admission", status="pass", evidence=evidence),
        ),
    )
    report_ref = _model_ref(
        report,
        name="native-skill-projection-report.json",
        schema_version="harness.skill_qualification_report.v1",
    )
    qualification = SkillQualification(
        skill_ref=admission.admission.skill_ref,
        status="pass",
        deterministic_case_id=_ADAPTER_CASE_ID,
        regression_command=_ADAPTER_REGRESSION_COMMAND,
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _model_ref(
        qualification,
        name="native-skill-projection-qualification.json",
        schema_version="harness.skill_qualification.v1",
    )
    common = {
        "skill_id": native_descriptor.skill_id,
        "version": native_descriptor.version,
        "mcp_tool_name": native_descriptor.mcp_tool_name,
        "input_schema": native_descriptor.input_schema,
        "output_schema": native_descriptor.output_schema,
        "implementation_name": "harness.native_typed_projection",
        "implementation_version": "1",
        "implementation_sha256": implementation_sha256,
        "max_attempts": native_descriptor.max_attempts,
        "qualification_artifact": qualification_ref,
    }
    adapter_descriptor: RegisteredSkillDescriptor = (
        SkillDescriptor(**common, deterministic=True)
        if type(native_descriptor) is SkillDescriptor
        else SkillDescriptorV2(
            **common,
            reproducibility=cast(SkillDescriptorV2, native_descriptor).reproducibility,
        )
    )
    descriptor_ref = _model_ref(
        adapter_descriptor,
        name="native-skill-projection-descriptor.json",
        schema_version=_descriptor_schema(adapter_descriptor),
    )
    registry_entry = QualifiedSkillSnapshot(
        skill_ref=admission.admission.skill_ref,
        mcp_tool_name=admission.admission.mcp_tool_name,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=report_ref,
    )
    return _VerifiedProjectionDocuments(
        admission=admission,
        native_descriptor=native_descriptor,
        adapter_descriptor=adapter_descriptor,
        adapter_qualification=qualification,
        adapter_report=report,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        report_ref=report_ref,
        implementation_sources=sources,
        registry_entry=registry_entry,
    )


def _publish_implementation_sources(
    artifact_store: LocalArtifactStore,
) -> tuple[ArtifactRef, ...]:
    result = []
    root = Path(__file__).parent
    for logical_path, name in _SOURCE_FILES:
        result.append(
            artifact_store.put_file(
                root / logical_path,
                name=name,
                media_type="text/x-python",
                schema_version=None,
            )
        )
    return tuple(result)


def _current_implementation_sources() -> tuple[ArtifactRef, ...]:
    root = Path(__file__).parent
    return tuple(
        _payload_ref(
            (root / logical_path).read_bytes(),
            name=name,
            media_type="text/x-python",
            schema_version=None,
        )
        for logical_path, name in _SOURCE_FILES
    )


def _require_exact_inputs(
    artifact_store: LocalArtifactStore,
    native_binding: GoldenExecutionBinding,
    admission: VerifiedTypedQualificationAdmission | None,
    terminal_resolver: NativeTerminalResolver | None,
) -> None:
    if type(artifact_store) is not LocalArtifactStore:
        raise TypeError("artifact_store must be the exact LocalArtifactStore")
    binding = _require_native_image_execution_binding(native_binding)
    application = binding.application
    if application.artifact_root != artifact_store.root:
        raise TypeError("native binding and projection require the same exact CAS")
    if terminal_resolver is None:
        return
    if type(terminal_resolver) is not NativeTerminalResolver:
        raise TypeError("terminal resolver requires its exact sealed capability")
    if terminal_resolver.artifact_store is not artifact_store:
        raise TypeError("terminal resolver and projection require one exact CAS instance")
    if admission is not None and not terminal_resolver.owns(
        binding=binding,
        qualification=admission,
    ):
        raise TypeError("terminal resolver does not own this native binding and admission")


def _read_canonical_model(
    artifact_store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[BaseModel],
) -> BaseModel:
    payload = artifact_store.resolve(ref).path.read_bytes()
    parsed = model.model_validate_json(payload)
    _require(payload == _canonical_model_bytes(parsed), "adapter document is not canonical JSON")
    return parsed


def _publish_model(
    artifact_store: LocalArtifactStore,
    model: BaseModel,
    *,
    name: str,
    schema_version: str,
) -> ArtifactRef:
    payload = _canonical_model_bytes(model)
    with tempfile.TemporaryDirectory(prefix="native-skill-projection-") as directory:
        path = Path(directory) / name
        path.write_bytes(payload)
        return artifact_store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=schema_version,
        )


def _model_ref(model: BaseModel, *, name: str, schema_version: str) -> ArtifactRef:
    return _payload_ref(
        _canonical_model_bytes(model),
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _payload_ref(
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=len(payload),
        schema_version=schema_version,
    )


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return _canonical_bytes(model) + b"\n"


def _canonical_bytes(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _terminal_roots(evidence: tuple[ArtifactRef, ...]) -> NativeTerminalEvidenceRoots:
    if type(evidence) is not tuple or len(evidence) != 2:
        raise ValueError("successful native terminal requires exactly two evidence roots")
    admissions = tuple(
        ref for ref in evidence if ref.schema_version == "harness.native_terminal_admission.v1"
    )
    indexes = tuple(
        ref for ref in evidence if ref.schema_version == "harness.native_read_closure.v1"
    )
    if len(admissions) != 1 or len(indexes) != 1:
        raise ValueError("native terminal evidence roots have the wrong typed schemas")
    roots = NativeTerminalEvidenceRoots(admission=admissions[0], read_closure=indexes[0])
    if roots.as_artifacts() != evidence:
        raise ValueError("native terminal evidence roots are not canonical")
    return roots


def _descriptor_schema(descriptor: RegisteredSkillDescriptor) -> str:
    return (
        "harness.skill_descriptor.v1"
        if type(descriptor) is SkillDescriptor
        else "harness.skill_descriptor.v2"
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NativeSkillProjectionError(message)


__all__ = [
    "NativeSkillProjection",
    "NativeSkillProjectionError",
    "issue_native_skill_projection",
    "load_native_skill_projection",
    "publish_native_registry_snapshot",
]
