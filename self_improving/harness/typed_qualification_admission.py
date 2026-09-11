"""Freeze and relocate native typed Skill qualifications without changing their meaning."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .run_store import RunStore
from .schema_catalog import schema_model
from .schemas.common import (
    ArtifactRef,
    RegisteredSkillDescriptor,
    RunStatus,
    SkillDescriptor,
    SkillDescriptorV2,
    SkillQualification,
)
from .schemas.genesis_fresh_capture import GenesisFreshCaptureQualificationV1
from .schemas.genesis_scene_application_v2 import GenesisSceneSkillQualificationReportV2
from .schemas.genesis_scene_assessment import GenesisSceneValidationQualificationV1
from .schemas.image2env_genesis_compile_application import (
    Image2EnvGenesisSkillQualificationReportV1,
)
from .schemas.typed_qualification_admission import (
    TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID,
    QualificationCandidateIntentV1,
    QualificationImplementationMemberV1,
    TypedQualificationAdmissionV1,
)

_REPORT_TYPES = {
    "harness.image2env_genesis_skill_qualification_report.v1": (
        Image2EnvGenesisSkillQualificationReportV1
    ),
    "harness.genesis_scene_skill_qualification_report.v2": (GenesisSceneSkillQualificationReportV2),
    "harness.genesis_fresh_capture_qualification.v1": GenesisFreshCaptureQualificationV1,
    "harness.genesis_scene_validation_qualification.v1": (GenesisSceneValidationQualificationV1),
}
_VALIDATION_IMPLEMENTATION_PATHS = (
    "genesis_scene_assessment.py",
    "genesis_scene_validation_application.py",
    "schemas/genesis_scene_assessment.py",
    "registry.py",
    "run_store.py",
    "event_journal.py",
    "artifacts.py",
)
_CAPABILITY_TOKEN = object()


class TypedQualificationAdmissionError(ValueError):
    """Native qualification evidence cannot be admitted without changing its meaning."""


class VerifiedTypedQualificationAdmission:
    """Opaque result issued only after complete admission verification."""

    __slots__ = ("_admission", "_admission_ref")

    def __init__(
        self,
        admission: TypedQualificationAdmissionV1,
        admission_ref: ArtifactRef,
        *,
        _token: object,
    ) -> None:
        if _token is not _CAPABILITY_TOKEN:
            raise TypeError("verified admission capabilities are issued only by the verifier")
        self._admission = admission
        self._admission_ref = admission_ref

    @property
    def admission(self) -> TypedQualificationAdmissionV1:
        return self._admission

    @property
    def admission_ref(self) -> ArtifactRef:
        return self._admission_ref


def freeze_typed_qualification_admission(
    *,
    artifact_store: LocalArtifactStore,
    child_authority: RunStore,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
    implementation_members: tuple[QualificationImplementationMemberV1, ...],
) -> VerifiedTypedQualificationAdmission:
    """Freeze native report evidence and its durable qualification candidate intents."""

    try:
        descriptor, report, bindings = _verify_authority_documents(
            artifact_store=artifact_store,
            descriptor_ref=descriptor_ref,
            qualification_ref=qualification_ref,
            qualification_report_ref=qualification_report_ref,
            implementation_members=implementation_members,
        )
        intents = tuple(
            _freeze_candidate(
                artifact_store=artifact_store,
                child_authority=child_authority,
                descriptor=descriptor,
                report=report,
                binding=binding,
            )
            for binding in bindings
        )
        draft = TypedQualificationAdmissionV1(
            schema_version=TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID,
            skill_ref=_skill_ref(descriptor),
            mcp_tool_name=descriptor.mcp_tool_name,
            descriptor=descriptor_ref,
            qualification=qualification_ref,
            qualification_report=qualification_report_ref,
            implementation_sha256=descriptor.implementation_sha256,
            implementation_members=implementation_members,
            candidate_intents=intents,
            admission_sha256="0" * 64,
        )
        admission = draft.model_copy(update={"admission_sha256": _admission_sha256(draft)})
        _publish_admission(artifact_store, admission)
        return verify_typed_qualification_admission(
            artifact_store=artifact_store,
            admission=admission,
        )
    except TypedQualificationAdmissionError:
        raise
    except (ArtifactResolutionError, ValidationError, RuntimeError, ValueError, TypeError) as error:
        raise TypedQualificationAdmissionError(
            f"typed qualification admission failed: {error}"
        ) from error


def verify_typed_qualification_admission(
    *,
    artifact_store: LocalArtifactStore,
    admission: TypedQualificationAdmissionV1,
) -> VerifiedTypedQualificationAdmission:
    """Recompute a frozen admission from CAS only, including candidate intent bindings."""

    try:
        checked = TypedQualificationAdmissionV1.model_validate(admission.model_dump(mode="json"))
        descriptor, report, bindings = _verify_authority_documents(
            artifact_store=artifact_store,
            descriptor_ref=checked.descriptor,
            qualification_ref=checked.qualification,
            qualification_report_ref=checked.qualification_report,
            implementation_members=checked.implementation_members,
        )
        _require(checked.skill_ref == _skill_ref(descriptor), "admission Skill identity changed")
        _require(
            checked.mcp_tool_name == descriptor.mcp_tool_name,
            "admission MCP tool identity changed",
        )
        _require(
            checked.implementation_sha256 == descriptor.implementation_sha256,
            "admission implementation identity changed",
        )
        _require(
            len(checked.candidate_intents) == len(bindings),
            "admission candidate intent count changed",
        )
        for intent, binding in zip(checked.candidate_intents, bindings, strict=True):
            _verify_candidate(
                artifact_store=artifact_store,
                descriptor=descriptor,
                report=report,
                binding=binding,
                intent=intent,
            )
        _require(
            checked.admission_sha256 == _admission_sha256(checked),
            "admission identity changed",
        )
        admission_ref = _expected_admission_ref(checked)
        _require(
            artifact_store.resolve(admission_ref).path.read_bytes() == _canonical(checked),
            "published admission bytes changed",
        )
        return VerifiedTypedQualificationAdmission(
            checked,
            admission_ref,
            _token=_CAPABILITY_TOKEN,
        )
    except TypedQualificationAdmissionError:
        raise
    except (ArtifactResolutionError, ValidationError, RuntimeError, ValueError, TypeError) as error:
        raise TypedQualificationAdmissionError(
            f"typed qualification admission verification failed: {error}"
        ) from error


class _CandidateBinding(tuple):
    """Internal fixed tuple: run ID, optional report digest, and output reference."""

    __slots__ = ()

    @property
    def run_id(self):
        return self[0]

    @property
    def invocation_digest(self):
        return self[1]

    @property
    def output(self):
        return self[2]


def _verify_authority_documents(
    *,
    artifact_store: LocalArtifactStore,
    descriptor_ref: ArtifactRef,
    qualification_ref: ArtifactRef,
    qualification_report_ref: ArtifactRef,
    implementation_members: tuple[QualificationImplementationMemberV1, ...],
) -> tuple[RegisteredSkillDescriptor, BaseModel, tuple[_CandidateBinding, ...]]:
    descriptor_type = {
        "harness.skill_descriptor.v1": SkillDescriptor,
        "harness.skill_descriptor.v2": SkillDescriptorV2,
    }.get(descriptor_ref.schema_version)
    _require(descriptor_type is not None, "descriptor schema is not admitted")
    descriptor = cast(
        RegisteredSkillDescriptor,
        _read_canonical(artifact_store, descriptor_ref, descriptor_type, "descriptor"),
    )
    qualification = cast(
        SkillQualification,
        _read_canonical(
            artifact_store,
            qualification_ref,
            SkillQualification,
            "qualification",
        ),
    )
    report_type = _REPORT_TYPES.get(qualification_report_ref.schema_version)
    _require(report_type is not None, "qualification requires a native typed report")
    report = _read_canonical(
        artifact_store,
        qualification_report_ref,
        report_type,
        "typed report",
    )
    skill_ref = _skill_ref(descriptor)
    _require(
        descriptor.qualification_artifact == qualification_ref,
        "descriptor qualification changed",
    )
    _require(qualification.skill_ref == skill_ref, "qualification Skill identity changed")
    _require(
        qualification.report_sha256 == qualification_report_ref.sha256,
        "qualification typed report digest changed",
    )
    _require(getattr(report, "skill_ref") == skill_ref, "typed report Skill identity changed")
    _verify_implementation(
        artifact_store=artifact_store,
        descriptor=descriptor,
        report=report,
        members=implementation_members,
    )
    for ref in _artifact_refs(report):
        artifact_store.resolve(ref)
    return descriptor, report, _candidate_bindings(report)


def _verify_implementation(
    *,
    artifact_store: LocalArtifactStore,
    descriptor: RegisteredSkillDescriptor,
    report: BaseModel,
    members: tuple[QualificationImplementationMemberV1, ...],
) -> None:
    _require(bool(members), "implementation closure is empty")
    paths = tuple(member.logical_path for member in members)
    _require(len(paths) == len(set(paths)), "implementation logical paths are not unique")
    _require(all(_portable_path(path) for path in paths), "implementation logical path is unsafe")
    refs = tuple(member.artifact for member in members)
    for ref in refs:
        _require(
            ref.uri == f"artifact://sha256/{ref.sha256}" and ref.schema_version is None,
            "implementation member is not an exact untyped CAS object",
        )
        try:
            artifact_store.resolve(ref)
        except ArtifactResolutionError as error:
            raise TypedQualificationAdmissionError(
                f"implementation member is unavailable: {error}"
            ) from error
    if isinstance(report, Image2EnvGenesisSkillQualificationReportV1):
        _require(
            refs == report.implementation_sources,
            "implementation closure differs from report",
        )
        digest = _sha256(
            {
                "sources": [ref.model_dump(mode="json") for ref in refs],
                "compile": report.compile_implementation_sha256,
            }
        )
    elif isinstance(report, GenesisSceneSkillQualificationReportV2):
        _require(refs == report.implementation, "implementation closure differs from report")
        digest = _sha256(
            {
                "numeric_identity": {
                    "numpy_version": report.numpy_version,
                    "python_abi": report.python_abi,
                },
                "members": [ref.model_dump(mode="json") for ref in refs],
            }
        )
    elif isinstance(report, GenesisFreshCaptureQualificationV1):
        _require(refs == report.implementation, "implementation closure differs from report")
        digest = _sha256([ref.model_dump(mode="json") for ref in refs])
    else:
        _require(
            isinstance(report, GenesisSceneValidationQualificationV1),
            "typed report implementation policy is unavailable",
        )
        _require(
            paths == _VALIDATION_IMPLEMENTATION_PATHS,
            "validation implementation paths changed",
        )
        digest = _sha256(tuple((member.logical_path, member.artifact.sha256) for member in members))
    _require(digest == getattr(report, "implementation_sha256"), "implementation digest changed")
    _require(
        digest == descriptor.implementation_sha256,
        "descriptor implementation differs from typed report",
    )


def _candidate_bindings(report: BaseModel) -> tuple[_CandidateBinding, ...]:
    if isinstance(report, Image2EnvGenesisSkillQualificationReportV1):
        return tuple(
            _CandidateBinding((case.run_id, case.invocation_digest, case.compile_receipt))
            for case in report.cases
        )
    if isinstance(report, GenesisSceneSkillQualificationReportV2):
        return tuple(
            _CandidateBinding(
                (case.candidate_run_id, case.invocation_digest, case.execution_receipt)
            )
            for case in report.cases
        )
    if isinstance(report, GenesisFreshCaptureQualificationV1):
        return tuple(
            _CandidateBinding((run_id, None, receipt))
            for run_id, receipt in zip(report.candidate_runs, report.receipts, strict=True)
        )
    _require(
        isinstance(report, GenesisSceneValidationQualificationV1),
        "typed report candidate policy is unavailable",
    )
    return tuple(
        _CandidateBinding((run_id, None, validation))
        for run_id, validation in zip(report.candidate_runs, report.validations, strict=True)
    )


def _freeze_candidate(
    *,
    artifact_store: LocalArtifactStore,
    child_authority: RunStore,
    descriptor: RegisteredSkillDescriptor,
    report: BaseModel,
    binding: _CandidateBinding,
) -> QualificationCandidateIntentV1:
    lifecycle = child_authority.read_controlled_lifecycle(binding.run_id)
    _require(
        lifecycle.intent is None
        and lifecycle.invocation is not None
        and lifecycle.terminal is not None,
        "candidate intent lifecycle is incomplete",
    )
    intent = QualificationCandidateIntentV1(
        run_id=binding.run_id,
        invocation_digest=lifecycle.invocation.invocation_digest,
        invocation=lifecycle.invocation,
        run_state=lifecycle.terminal,
        output=binding.output,
    )
    _verify_candidate(
        artifact_store=artifact_store,
        descriptor=descriptor,
        report=report,
        binding=binding,
        intent=intent,
    )
    return intent


def _verify_candidate(
    *,
    artifact_store: LocalArtifactStore,
    descriptor: RegisteredSkillDescriptor,
    report: BaseModel,
    binding: _CandidateBinding,
    intent: QualificationCandidateIntentV1,
) -> None:
    invocation, state = intent.invocation, intent.run_state
    expected_skill = _skill_ref(descriptor)
    _require(
        intent.run_id == binding.run_id == invocation.run_id == state.run_id,
        "candidate intent run identity changed",
    )
    _require(
        intent.invocation_digest == invocation.invocation_digest == state.invocation_digest,
        "candidate intent digest changed",
    )
    if binding.invocation_digest is not None:
        _require(
            binding.invocation_digest == invocation.invocation_digest,
            "typed report candidate intent digest changed",
        )
    _require(
        f"{invocation.skill_id}@{invocation.skill_version}" == expected_skill
        and f"{state.skill_id}@{state.skill_version}" == expected_skill,
        "candidate intent Skill identity changed",
    )
    _require(
        invocation.max_attempts == descriptor.max_attempts == state.max_attempts,
        "candidate intent attempt policy changed",
    )
    _require(
        state.status is RunStatus.SUCCEEDED
        and state.output is not None
        and state.blocker is None
        and all(event.stage.startswith("qualification_candidate.") for event in state.events),
        "candidate intent is not a successful qualification candidate",
    )
    _require(intent.output == binding.output, "candidate intent output reference changed")
    _require(
        intent.output.uri == f"artifact://sha256/{intent.output.sha256}"
        and intent.output.media_type == "application/json"
        and intent.output.schema_version == descriptor.output_schema,
        "candidate intent output reference has the wrong type",
    )
    _require(
        sum(ref == intent.output for ref in state.artifacts) == 1,
        "candidate intent must retain exactly one typed output reference",
    )
    input_type = schema_model(descriptor.input_schema)
    output_type = schema_model(descriptor.output_schema)
    typed_input = input_type.model_validate(invocation.effective_parameters)
    typed_output = output_type.model_validate(state.output)
    _require(
        invocation.invocation_digest
        == _invocation_digest(
            descriptor=descriptor,
            typed_input=typed_input,
            dependencies=invocation.dependencies,
        ),
        "candidate intent digest does not match its effective input",
    )
    output_bytes = artifact_store.resolve(intent.output).path.read_bytes()
    _require(output_bytes == _canonical(typed_output), "candidate intent output bytes changed")
    for ref in (*_artifact_refs(typed_input), *state.artifacts):
        artifact_store.resolve(ref)
    _verify_typed_case(
        artifact_store=artifact_store,
        report=report,
        binding=binding,
        typed_input=typed_input,
        typed_output=typed_output,
    )


def _verify_typed_case(
    *,
    artifact_store: LocalArtifactStore,
    report: BaseModel,
    binding: _CandidateBinding,
    typed_input: BaseModel,
    typed_output: BaseModel,
) -> None:
    if isinstance(report, Image2EnvGenesisSkillQualificationReportV1):
        _require(
            getattr(typed_output, "implementation_sha256") == report.compile_implementation_sha256,
            "compile candidate implementation differs from typed report",
        )
        request = getattr(typed_output, "request")
        raw = artifact_store.resolve(request).path.read_bytes()
        _require(raw == _canonical(typed_input), "compile candidate request differs from intent")
    elif isinstance(report, GenesisSceneSkillQualificationReportV2):
        case = next(case for case in report.cases if case.candidate_run_id == binding.run_id)
        _require(
            case.compile_run_id == getattr(typed_input, "compile_run_id")
            and case.execution_request == getattr(typed_input, "execution_request")
            and getattr(typed_output, "request") == case.execution_request,
            "replay candidate differs from typed report intent",
        )
    elif isinstance(report, GenesisFreshCaptureQualificationV1):
        _require(
            getattr(typed_output, "replay_run_id") == getattr(typed_input, "replay_run_id")
            and getattr(typed_output, "ttl_seconds") == getattr(typed_input, "ttl_seconds"),
            "observation candidate differs from typed report intent",
        )
    else:
        _require(
            isinstance(report, GenesisSceneValidationQualificationV1)
            and getattr(typed_output, "replay_run_id") == getattr(typed_input, "replay_run_id")
            and getattr(typed_output, "candidate_only") is True,
            "validation candidate differs from typed report intent",
        )


def _read_canonical(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    model: type[BaseModel],
    label: str,
) -> BaseModel:
    _require(
        ref.uri == f"artifact://sha256/{ref.sha256}" and ref.media_type == "application/json",
        f"{label} reference is not canonical CAS JSON",
    )
    raw = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(raw)
    _require(raw == _canonical(value), f"{label} bytes are not canonical JSON")
    return value


def _skill_ref(descriptor: RegisteredSkillDescriptor) -> str:
    return f"{descriptor.skill_id}@{descriptor.version}"


def _invocation_digest(
    *,
    descriptor: RegisteredSkillDescriptor,
    typed_input: BaseModel,
    dependencies: tuple[Any, ...],
) -> str:
    return _sha256(
        {
            "skill_id": descriptor.skill_id,
            "skill_version": descriptor.version,
            "effective_parameters": _content_identity(typed_input),
            "dependencies": [item.model_dump(mode="json") for item in dependencies],
            "max_attempts": descriptor.max_attempts,
        }
    )


def _content_identity(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, ArtifactRef):
        return {
            "media_type": value.media_type,
            "schema_version": value.schema_version,
            "sha256": value.sha256,
        }
    if isinstance(value, BaseModel):
        return {
            name: _content_identity(getattr(value, name)) for name in value.__class__.model_fields
        }
    if isinstance(value, (list, tuple)):
        return [_content_identity(item) for item in value]
    return value


def _artifact_refs(value: Any) -> tuple[ArtifactRef, ...]:
    found: list[ArtifactRef] = []

    def visit(item: Any) -> None:
        if isinstance(item, ArtifactRef):
            found.append(item)
        elif isinstance(item, BaseModel):
            for name in item.__class__.model_fields:
                visit(getattr(item, name))
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(found)


def _portable_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and value == path.as_posix() and ".." not in path.parts


def _admission_sha256(admission: TypedQualificationAdmissionV1) -> str:
    return _sha256(admission.model_dump(mode="json", exclude={"admission_sha256"}))


def _expected_admission_ref(admission: TypedQualificationAdmissionV1) -> ArtifactRef:
    payload = _canonical(admission)
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactRef(
        name="typed-qualification-admission.json",
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=len(payload),
        schema_version=TYPED_QUALIFICATION_ADMISSION_SCHEMA_ID,
    )


def _publish_admission(
    store: LocalArtifactStore,
    admission: TypedQualificationAdmissionV1,
) -> ArtifactRef:
    expected = _expected_admission_ref(admission)
    with tempfile.TemporaryDirectory(prefix="typed-qualification-admission-") as directory:
        path = Path(directory) / expected.name
        path.write_bytes(_canonical(admission))
        actual = store.put_file(
            path,
            name=expected.name,
            media_type=expected.media_type,
            schema_version=expected.schema_version,
        )
    _require(actual == expected, "published admission identity changed")
    return actual


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _canonical(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TypedQualificationAdmissionError(message)
