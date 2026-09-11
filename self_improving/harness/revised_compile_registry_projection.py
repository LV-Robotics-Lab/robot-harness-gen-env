"""Honest Registry-v1 projection for the qualified revised image compiler.

The native compile@3 report remains the qualification authority.  This module
only publishes the generic adapter documents required by Registry v1, and every
adapter operation re-verifies the original typed admission, registered
qualification candidates, implementation bytes, CAS, and SQLite child authority.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .golden_execution import (
    GoldenExecutionBinding,
    QualifiedApplicationRunPendingError,
    _create_golden_execution_binding,
    _create_golden_execution_evidence_hook,
)
from .limited_image_preview_policy import LimitedImagePreviewPolicy
from .qualification import QualificationCheckV1, QualificationReportV1
from .registry import ControlledRunPendingError
from .revised_image_compile_application import RevisedImageCompileApplication
from .run_store import ControlledRunLifecycle, SQLiteRunStore
from .schemas.common import (
    ArtifactRef,
    Invocation,
    RunState,
    SkillDescriptor,
    SkillQualification,
)
from .schemas.registry_snapshot import QualifiedSkillSnapshot
from .schemas.revised_image_compile_application import RevisedCompileQualificationReportV1
from .schemas.typed_qualification_admission import TypedQualificationAdmissionV1
from .schemas.workflow_operation import QualifiedSkillExecutionV2
from .typed_qualification_admission import (
    VerifiedTypedQualificationAdmission,
    verify_typed_qualification_admission,
)

_TOKEN = object()
_SKILL_REF = "image2env.compile@3.0.0"
_EMPTY_DEPENDENCY_SHA256 = hashlib.sha256(b"").hexdigest()
_CASE_ID = "revised-compile-typed-admission-projection"
_REGRESSION_COMMAND = (
    "pytest -q tests/self_improving/harness/test_revised_compile_registry_projection.py"
)
_SOURCE_FILES = (
    "golden_execution.py",
    "golden_run.py",
    "limited_image_preview_policy.py",
    "revised_compile_registry_projection.py",
    "revised_image_compile_application.py",
    "typed_qualification_admission.py",
)


class RevisedCompileRegistryProjectionError(ValueError):
    """The revised compiler cannot enter Registry without changing its evidence."""


@dataclass(frozen=True)
class _VerifiedDocuments:
    admission: VerifiedTypedQualificationAdmission
    native_descriptor: SkillDescriptor
    adapter_descriptor: SkillDescriptor
    adapter_qualification: SkillQualification
    adapter_report: QualificationReportV1
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    report_ref: ArtifactRef
    implementation_sources: tuple[ArtifactRef, ...]
    registry_entry: QualifiedSkillSnapshot


class RevisedCompileRegistryProjection:
    """Opaque verified Registry entry plus its sealed Golden execution binding."""

    __slots__ = (
        "_admission_ref",
        "_binding",
        "_descriptor_ref",
        "_implementation_sources",
        "_qualification_ref",
        "_qualification_report_ref",
        "_registry_entry",
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
        _token: object,
    ) -> None:
        if _token is not _TOKEN:
            raise TypeError("revised compile projections are issued only by their verifier")
        object.__setattr__(self, "_admission_ref", admission_ref)
        object.__setattr__(self, "_descriptor_ref", descriptor_ref)
        object.__setattr__(self, "_qualification_ref", qualification_ref)
        object.__setattr__(self, "_qualification_report_ref", qualification_report_ref)
        object.__setattr__(self, "_implementation_sources", implementation_sources)
        object.__setattr__(self, "_registry_entry", registry_entry)
        object.__setattr__(self, "_binding", binding)
        object.__setattr__(self, "_token", _token)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("revised compile projections are immutable")

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

    def _is_verified(self) -> bool:
        return self._token is _TOKEN


@dataclass(frozen=True)
class _ProjectionAuthority:
    application: RevisedImageCompileApplication
    policy: LimitedImagePreviewPolicy
    admission_ref: ArtifactRef
    descriptor_ref: ArtifactRef
    qualification_ref: ArtifactRef
    qualification_report_ref: ArtifactRef

    @property
    def artifact_store(self) -> LocalArtifactStore:
        return self.application.artifact_store

    def verify(self) -> _VerifiedDocuments:
        try:
            return _verify_documents(
                application=self.application,
                policy=self.policy,
                admission_ref=self.admission_ref,
                descriptor_ref=self.descriptor_ref,
                qualification_ref=self.qualification_ref,
                qualification_report_ref=self.qualification_report_ref,
            )
        except RevisedCompileRegistryProjectionError:
            raise
        except (
            ArtifactResolutionError,
            AttributeError,
            KeyError,
            RuntimeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            raise RevisedCompileRegistryProjectionError(
                f"revised compile projection source or candidate verification failed: {error}"
            ) from error


@dataclass(frozen=True)
class _ProjectedApplication:
    authority: _ProjectionAuthority
    native_application: RevisedImageCompileApplication
    child_authority: SQLiteRunStore
    descriptor: SkillDescriptor
    policy: LimitedImagePreviewPolicy

    @property
    def skill_ref(self) -> str:
        return _SKILL_REF

    @property
    def artifact_root(self) -> Path:
        return self.authority.artifact_store.root

    @property
    def authority_path(self) -> Path:
        return self.child_authority.path

    @property
    def skills(self) -> tuple[SkillDescriptor, ...]:
        verified = self.authority.verify()
        _require(verified.adapter_descriptor == self.descriptor, "adapter descriptor changed")
        return (self.descriptor,)

    def invoke(self, parameters: BaseModel, *, run_id: UUID) -> RunState:
        self.authority.verify()
        _require(
            type(parameters) is self.policy.input_model,
            "revised adapter requires the exact invocation envelope",
        )
        try:
            returned = self.native_application.invoke(parameters, run_id=run_id)
        except ControlledRunPendingError as error:
            raise QualifiedApplicationRunPendingError(str(error)) from error
        persisted = self.child_authority.read_run_state(run_id)
        _require(returned == persisted, "revised application returned a non-authoritative state")
        return returned

    def invocation(self, run_id: UUID) -> Invocation | None:
        return self.child_authority.read_invocation(run_id)

    def run_state(self, run_id: UUID) -> RunState | None:
        return self.child_authority.read_run_state(run_id)


@dataclass(frozen=True)
class _ProjectionEvidenceHook:
    authority: _ProjectionAuthority

    def verify_registry_entry(self, entry: QualifiedSkillSnapshot) -> None:
        _require(type(entry) is QualifiedSkillSnapshot, "foreign Registry entry type")
        _require(
            entry == self.authority.verify().registry_entry,
            "revised compile Registry entry changed",
        )

    def verify_child_lifecycle(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        command_parameters: Mapping[str, object],
        qualified: QualifiedSkillExecutionV2,
    ) -> tuple[ArtifactRef, ...]:
        documents = self.authority.verify()
        _verify_child(
            lifecycle=lifecycle,
            documents=documents,
            policy=self.authority.policy,
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            command_parameters=command_parameters,
            qualified=qualified,
        )
        return ()

    def verify_committed_evidence(
        self,
        *,
        lifecycle: ControlledRunLifecycle,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
    ) -> None:
        self.authority.verify()
        _require(skill_ref == _SKILL_REF, "committed revised Skill identity changed")
        _require(lifecycle.terminal is not None, "committed revised terminal is absent")
        _require(lifecycle.terminal.run_id == child_run_id, "committed child UUID changed")
        _require(not evidence, "revised compile projection has unexpected terminal roots")

    def resolve_artifact_member(
        self,
        *,
        skill_ref: str,
        child_run_id: UUID,
        evidence: tuple[ArtifactRef, ...],
        sha256: str,
    ) -> ArtifactRef | None:
        self.verify_committed_evidence(
            lifecycle=self.authority.application.run_store.read_controlled_lifecycle(child_run_id),
            skill_ref=skill_ref,
            child_run_id=child_run_id,
            evidence=evidence,
        )
        _require(len(sha256) == 64, "artifact digest is malformed")
        return None


def issue_revised_compile_registry_projection(
    *,
    application: RevisedImageCompileApplication,
    admission: VerifiedTypedQualificationAdmission,
    policy: LimitedImagePreviewPolicy,
) -> RevisedCompileRegistryProjection:
    """Publish a Registry-v1 adapter only from the existing verified J3 authority."""

    _require_exact_inputs(application, policy)
    if (
        type(admission) is not VerifiedTypedQualificationAdmission
        or not admission._is_verifier_issued()
    ):
        raise TypeError("admission must be a verifier-issued typed admission")
    store = application.artifact_store
    try:
        reverified = verify_typed_qualification_admission(
            artifact_store=store,
            admission=admission.admission,
        )
        _require(
            reverified.admission == admission.admission
            and reverified.admission_ref == admission.admission_ref,
            "typed admission changed during projection",
        )
        native_descriptor = _native_descriptor(application, reverified)
        sources = _publish_sources(store)
        documents = _build_documents(reverified, native_descriptor, policy, sources)
        report_ref = _publish_model(
            store,
            documents.adapter_report,
            "revised-compile-projection-report.json",
        )
        qualification_ref = _publish_model(
            store,
            documents.adapter_qualification,
            "revised-compile-projection-qualification.json",
        )
        descriptor_ref = _publish_model(
            store,
            documents.adapter_descriptor,
            "revised-compile-projection-descriptor.json",
        )
        _require(
            (descriptor_ref, qualification_ref, report_ref)
            == (documents.descriptor_ref, documents.qualification_ref, documents.report_ref),
            "published projection identity changed",
        )
        return load_revised_compile_registry_projection(
            application=application,
            policy=policy,
            admission_ref=reverified.admission_ref,
            descriptor_ref=descriptor_ref,
            qualification_ref=qualification_ref,
            qualification_report_ref=report_ref,
        )
    except RevisedCompileRegistryProjectionError:
        raise
    except (
        ArtifactResolutionError,
        AttributeError,
        KeyError,
        RuntimeError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise RevisedCompileRegistryProjectionError(
            f"revised compile projection issuance failed: {error}"
        ) from error


def load_revised_compile_registry_projection(
    *,
    application: RevisedImageCompileApplication,
    policy: LimitedImagePreviewPolicy,
    admission_ref: ArtifactRef,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
) -> RevisedCompileRegistryProjection:
    """Recover the adapter from CAS and the original application's SQLite authority."""

    _require_exact_inputs(application, policy)
    for value in (admission_ref, descriptor_ref, qualification_ref, qualification_report_ref):
        if type(value) is not ArtifactRef:
            raise TypeError("projection references must be exact ArtifactRef values")
    authority = _ProjectionAuthority(
        application=application,
        policy=policy,
        admission_ref=admission_ref,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=qualification_report_ref,
    )
    documents = authority.verify()
    adapter = _ProjectedApplication(
        authority=authority,
        native_application=application,
        child_authority=application.run_store,
        descriptor=documents.adapter_descriptor,
        policy=policy,
    )
    hook = _create_golden_execution_evidence_hook(_ProjectionEvidenceHook(authority))
    binding = _create_golden_execution_binding(
        policy=policy,
        application=adapter,
        child_authority=application.run_store,
        evidence_hook=hook,
    )
    return RevisedCompileRegistryProjection(
        admission_ref=admission_ref,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=qualification_report_ref,
        implementation_sources=documents.implementation_sources,
        registry_entry=documents.registry_entry,
        binding=binding,
        _token=_TOKEN,
    )


def _require_exact_inputs(
    application: RevisedImageCompileApplication,
    policy: LimitedImagePreviewPolicy,
) -> None:
    if type(application) is not RevisedImageCompileApplication:
        raise TypeError("projection requires the exact qualified revised application")
    if type(policy) is not LimitedImagePreviewPolicy:
        raise TypeError("projection requires the exact limited preview policy")
    if policy.application is not application:
        raise TypeError("policy and projection require the same exact application")
    if (
        policy.skill_ref != _SKILL_REF
        or policy.artifact_store is not application.artifact_store
        or policy.child_authority is not application.run_store
    ):
        raise TypeError("policy lost the revised application's CAS or SQLite authority")


def _verify_documents(
    *,
    application: RevisedImageCompileApplication,
    policy: LimitedImagePreviewPolicy,
    admission_ref: ArtifactRef,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
) -> _VerifiedDocuments:
    _require_exact_inputs(application, policy)
    admission_model = _read_model(
        application.artifact_store,
        admission_ref,
        TypedQualificationAdmissionV1,
    )
    admission = verify_typed_qualification_admission(
        artifact_store=application.artifact_store,
        admission=admission_model,
    )
    _require(admission.admission_ref == admission_ref, "typed admission CAS identity changed")
    native_descriptor = _native_descriptor(application, admission)
    sources = _current_sources()
    for source in sources:
        application.artifact_store.resolve(source)
    expected = _build_documents(admission, native_descriptor, policy, sources)
    _require(
        (descriptor_ref, qualification_ref, qualification_report_ref)
        == (expected.descriptor_ref, expected.qualification_ref, expected.report_ref),
        "projection document reference changed",
    )
    report = _read_model(
        application.artifact_store,
        qualification_report_ref,
        QualificationReportV1,
        trailing_newline=True,
    )
    qualification = _read_model(
        application.artifact_store,
        qualification_ref,
        SkillQualification,
        trailing_newline=True,
    )
    descriptor = _read_model(
        application.artifact_store,
        descriptor_ref,
        SkillDescriptor,
        trailing_newline=True,
    )
    _require(
        report == expected.adapter_report
        and qualification == expected.adapter_qualification
        and descriptor == expected.adapter_descriptor,
        "projection documents changed",
    )
    return expected


def _native_descriptor(
    application: RevisedImageCompileApplication,
    admission: VerifiedTypedQualificationAdmission,
) -> SkillDescriptor:
    value = admission.admission
    _require(value.skill_ref == _SKILL_REF, "typed admission names another Skill")
    _require(
        value.qualification_report.schema_version
        == "harness.revised_compile_qualification_report.v1",
        "typed admission report is not the revised compile report",
    )
    descriptor = _read_model(application.artifact_store, value.descriptor, SkillDescriptor)
    _require(type(descriptor) is SkillDescriptor, "revised compile descriptor type changed")
    registered = application.bind(value.qualification)
    _require(
        registered == descriptor and application.skills == (descriptor,),
        "registered revised application differs from typed admission",
    )
    report = _read_model(
        application.artifact_store,
        value.qualification_report,
        RevisedCompileQualificationReportV1,
    )
    _require(
        tuple(case.run_id for case in report.cases)
        == tuple(intent.run_id for intent in value.candidate_intents),
        "registered qualification candidate order or identity changed",
    )
    return descriptor


def _build_documents(
    admission: VerifiedTypedQualificationAdmission,
    native_descriptor: SkillDescriptor,
    policy: LimitedImagePreviewPolicy,
    sources: tuple[ArtifactRef, ...],
) -> _VerifiedDocuments:
    policy_identity = {
        "id": policy.evidence_policy_id,
        "version": policy.evidence_policy_version,
        "implementation_sha256": policy.evidence_policy_implementation_sha256,
    }
    implementation_sha256 = _sha(
        {
            "typed_admission": admission.admission_ref.model_dump(mode="json"),
            "native_descriptor": admission.admission.descriptor.model_dump(mode="json"),
            "native_implementation_sha256": admission.admission.implementation_sha256,
            "evidence_policy": policy_identity,
            "sources": [source.model_dump(mode="json") for source in sources],
        }
    )
    evidence = {
        "domain": "registry_adapter_only_no_requalification_or_execution_claim",
        "typed_admission": admission.admission_ref.model_dump(mode="json"),
        "native_descriptor": admission.admission.descriptor.model_dump(mode="json"),
        "native_qualification": admission.admission.qualification.model_dump(mode="json"),
        "native_qualification_report": (
            admission.admission.qualification_report.model_dump(mode="json")
        ),
        "native_implementation_sha256": admission.admission.implementation_sha256,
        "native_implementation_members": [
            member.model_dump(mode="json") for member in admission.admission.implementation_members
        ],
        "candidate_run_ids": [
            str(intent.run_id) for intent in admission.admission.candidate_intents
        ],
        "candidate_invocation_digests": [
            intent.invocation_digest for intent in admission.admission.candidate_intents
        ],
        "evidence_policy": policy_identity,
        "adapter_implementation_sources": [source.model_dump(mode="json") for source in sources],
    }
    report = QualificationReportV1(
        schema_version="harness.skill_qualification_report.v1",
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        implementation_sha256=implementation_sha256,
        scene_gen_tree_sha256=_EMPTY_DEPENDENCY_SHA256,
        ledger_contract_tree_sha256=_EMPTY_DEPENDENCY_SHA256,
        checks=(
            QualificationCheckV1(
                name="revised_compile_typed_admission",
                status="pass",
                evidence=evidence,
            ),
        ),
    )
    report_ref = _model_ref(report, "revised-compile-projection-report.json")
    qualification = SkillQualification(
        skill_ref=_SKILL_REF,
        status="pass",
        deterministic_case_id=_CASE_ID,
        regression_command=_REGRESSION_COMMAND,
        report_sha256=report_ref.sha256,
    )
    qualification_ref = _model_ref(
        qualification,
        "revised-compile-projection-qualification.json",
    )
    descriptor = SkillDescriptor(
        skill_id=native_descriptor.skill_id,
        version=native_descriptor.version,
        mcp_tool_name=native_descriptor.mcp_tool_name,
        input_schema=native_descriptor.input_schema,
        output_schema=native_descriptor.output_schema,
        implementation_name="harness.revised_compile_typed_projection",
        implementation_version="1",
        implementation_sha256=implementation_sha256,
        deterministic=True,
        max_attempts=native_descriptor.max_attempts,
        qualification_artifact=qualification_ref,
    )
    descriptor_ref = _model_ref(descriptor, "revised-compile-projection-descriptor.json")
    entry = QualifiedSkillSnapshot(
        skill_ref=_SKILL_REF,
        mcp_tool_name=native_descriptor.mcp_tool_name,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        qualification_report_ref=report_ref,
    )
    return _VerifiedDocuments(
        admission=admission,
        native_descriptor=native_descriptor,
        adapter_descriptor=descriptor,
        adapter_qualification=qualification,
        adapter_report=report,
        descriptor_ref=descriptor_ref,
        qualification_ref=qualification_ref,
        report_ref=report_ref,
        implementation_sources=sources,
        registry_entry=entry,
    )


def _verify_child(
    *,
    lifecycle: ControlledRunLifecycle,
    documents: _VerifiedDocuments,
    policy: LimitedImagePreviewPolicy,
    skill_ref: str,
    child_run_id: UUID,
    command_parameters: Mapping[str, object],
    qualified: QualifiedSkillExecutionV2,
) -> None:
    intent = lifecycle.intent
    invocation = lifecycle.invocation
    state = lifecycle.terminal
    _require(
        intent is not None and invocation is not None and state is not None,
        "child incomplete",
    )
    admission = documents.admission.admission
    native = documents.native_descriptor
    adapter = documents.adapter_descriptor
    parameters_digest = _sha(command_parameters)
    _require(
        qualified.skill_ref == _SKILL_REF
        and qualified.descriptor == documents.descriptor_ref
        and qualified.implementation_sha256 == adapter.implementation_sha256
        and qualified.qualification_artifact == documents.qualification_ref
        and qualified.qualification_report == documents.report_ref,
        "Golden execution differs from projected Registry identity",
    )
    _require(
        qualified.evidence_policy_id == policy.evidence_policy_id
        and qualified.evidence_policy_version == policy.evidence_policy_version
        and qualified.evidence_policy_implementation_sha256
        == policy.evidence_policy_implementation_sha256,
        "Golden execution evidence policy changed",
    )
    _require(
        skill_ref == _SKILL_REF
        and child_run_id == intent.run_id == invocation.run_id == state.run_id
        and (intent.skill_id, intent.skill_version) == (native.skill_id, native.version)
        and (invocation.skill_id, invocation.skill_version) == (native.skill_id, native.version)
        and (state.skill_id, state.skill_version) == (native.skill_id, native.version)
        and intent.input_schema == native.input_schema
        and intent.raw_parameters_sha256 == parameters_digest
        and intent.effective_parameters_sha256 == parameters_digest
        and intent.descriptor_sha256 == _sha(native.model_dump(mode="json"))
        and intent.implementation_sha256 == admission.implementation_sha256
        and intent.qualification_sha256 == admission.qualification.sha256
        and intent.max_attempts
        == invocation.max_attempts
        == state.max_attempts
        == native.max_attempts,
        "registered child differs from typed admission and original descriptor",
    )
    _require(
        invocation.effective_parameters == command_parameters
        and invocation.dependencies == intent.dependencies
        and invocation.invocation_digest == state.invocation_digest
        and not any(event.stage.startswith("qualification_candidate.") for event in state.events),
        "registered child differs from its original application invocation",
    )


def _publish_sources(store: LocalArtifactStore) -> tuple[ArtifactRef, ...]:
    root = Path(__file__).parent
    return tuple(
        store.put_file(
            root / logical_path,
            name=logical_path.replace("/", "__"),
            media_type="text/x-python",
            schema_version=None,
        )
        for logical_path in _SOURCE_FILES
    )


def _current_sources() -> tuple[ArtifactRef, ...]:
    root = Path(__file__).parent
    return tuple(
        _payload_ref(
            (root / logical_path).read_bytes(),
            name=logical_path.replace("/", "__"),
            media_type="text/x-python",
            schema_version=None,
        )
        for logical_path in _SOURCE_FILES
    )


def _read_model(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[BaseModel],
    *,
    trailing_newline: bool = False,
):
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    expected = _canonical(value) + (b"\n" if trailing_newline else b"")
    _require(payload == expected, "CAS document is not canonical JSON")
    _require(
        ref.schema_version
        == value.model_dump(mode="json").get("schema_version", ref.schema_version),
        "CAS document schema identity changed",
    )
    return value


def _publish_model(store: LocalArtifactStore, model: BaseModel, name: str) -> ArtifactRef:
    with tempfile.TemporaryDirectory(prefix="revised-compile-projection-") as directory:
        path = Path(directory) / name
        path.write_bytes(_canonical(model) + b"\n")
        return store.put_file(
            path,
            name=name,
            media_type="application/json",
            schema_version=_model_schema(model),
        )


def _model_ref(model: BaseModel, name: str) -> ArtifactRef:
    return _payload_ref(
        _canonical(model) + b"\n",
        name=name,
        media_type="application/json",
        schema_version=_model_schema(model),
    )


def _model_schema(model: BaseModel) -> str:
    if type(model) is SkillQualification:
        return "harness.skill_qualification.v1"
    if type(model) is SkillDescriptor:
        return "harness.skill_descriptor.v1"
    schema_version = model.model_dump(mode="json").get("schema_version")
    _require(type(schema_version) is str, "projection document lacks a public schema identity")
    return schema_version


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
        sha256=digest,
        bytes=len(payload),
        media_type=media_type,
        schema_version=schema_version,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RevisedCompileRegistryProjectionError(message)


__all__ = [
    "RevisedCompileRegistryProjection",
    "RevisedCompileRegistryProjectionError",
    "issue_revised_compile_registry_projection",
    "load_revised_compile_registry_projection",
]
