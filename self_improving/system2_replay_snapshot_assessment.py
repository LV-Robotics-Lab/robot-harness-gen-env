"""Record or artifact-verify a local-CAS assessment without advancing System 2 state.

The assessment is a deterministic record of recomputation over the supplied
local typed acquisition.  Artifact-only verification later proves only that
the canonical record's package/runtime/snapshot closure yields the exact
recorded report, status, and counts in that same local CAS.  Its replay run id,
invocation digest, timestamps, and non-runtime dependency identities remain
typed metadata copied by the recorder, not independently authenticated Registry
or deployed-run provenance.  It is not a ``fresh_observation`` or
``WorldFactEvidence``, a complete Validate-v2 result, portable authority,
decision, publishable result, receipt, world-state transition, or claim of
physical success.  The replay factory's checked-in fixed qualification case
qualifies only that fixed wiring; it does not authorize this dynamic input,
acquisition, report, or outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from pydantic import UUID4, AwareDatetime, ValidationError, model_validator

from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.replay_dependencies import (
    REPLAY_DEPENDENCY_NAMES,
    REPLAY_RUNTIME_ASSET_DEPENDENCY,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    Invocation,
    Text2EnvReplayInput,
    ValidationStatus,
)
from self_improving.harness.schemas.base import HarnessModel, NonNegativeInt, Sha256
from self_improving.harness.system2.planner import LocalPlannerArtifactPublisher
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.system2_replay_snapshot_validation import (
    System2ReplaySnapshotValidationError,
    System2ReplaySnapshotValidationResult,
    System2ReplaySnapshotValidator,
)
from self_improving.validate_v2_snapshot import (
    SnapshotValidationError,
    SnapshotValidationRequest,
    SnapshotValidationResult,
    ValidateV2SnapshotAdapter,
)

_ASSESSMENT_SCHEMA = "harness.system2_replay_snapshot_assessment.v1"
_ASSESSMENT_SCOPE = "local_cas_snapshot_recomputation"
_ASSESSMENT_NAME = "system2_replay_snapshot_assessment"
_ASSESSMENT_MEDIA_TYPE = "application/json"
_ASSET_CATALOG_SCHEMA = "robotwin.asset_catalog.v1"
_PACKAGE_MANIFEST_SCHEMA = "robotwin.generated_scene_package.v1"
_RUNTIME_EVIDENCE_NAME = "runtime_evidence"
_RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
_RUNTIME_ASSET_SNAPSHOT_NAME = "runtime_asset_snapshot"
_RUNTIME_ASSET_SNAPSHOT_SCHEMA = "harness.runtime_asset_snapshot.v1"
_VALIDATION_REPORT_NAME = "snapshot_validation_report"
_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"

_AssessmentFailureReason = Literal[
    "snapshot_validation_failed",
    "assessment_publish_failed",
    "assessment_verification_failed",
    "assessment_invalid",
    "assessment_mismatch",
    "assessment_recompute_failed",
]


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _is_exact_json_ref(
    value: object,
    *,
    name: str | None,
    schema_version: str,
) -> bool:
    return (
        type(value) is ArtifactRef
        and (name is None or value.name == name)
        and value.media_type == "application/json"
        and value.schema_version == schema_version
        and value.uri == f"artifact://sha256/{value.sha256}"
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


class System2ReplaySnapshotAssessment(HarnessModel):
    """Canonical local assessment record, not an authenticated run receipt.

    Structural validation keeps times, dependency shape, and artifact bindings
    self-consistent.  It does not authenticate the recorded run identity,
    timestamps, or non-runtime dependency provenance.
    """

    schema_version: Literal["harness.system2_replay_snapshot_assessment.v1"] = _ASSESSMENT_SCHEMA
    scope: Literal["local_cas_snapshot_recomputation"] = _ASSESSMENT_SCOPE
    replay_run_id: UUID4
    replay_invocation_digest: Sha256
    replay_skill_ref: Literal["text2env.replay@1.0.0"]
    max_attempts: Literal[2]
    replay_started_at: AwareDatetime
    replay_ended_at: AwareDatetime
    replay_input: Text2EnvReplayInput
    dependencies: tuple[DependencyRef, ...]
    runtime_evidence: ArtifactRef
    runtime_asset_snapshot_manifest: ArtifactRef
    validation_report: ArtifactRef
    validation_status: ValidationStatus
    fail_count: NonNegativeInt
    not_run_count: NonNegativeInt

    @model_validator(mode="after")
    def record_is_self_consistent(self) -> System2ReplaySnapshotAssessment:
        if (
            self.replay_started_at.utcoffset() != timedelta(0)
            or self.replay_ended_at.utcoffset() != timedelta(0)
            or self.replay_started_at > self.replay_ended_at
        ):
            raise ValueError("assessment replay times are invalid")
        if (
            type(self.dependencies) is not tuple
            or any(type(dependency) is not DependencyRef for dependency in self.dependencies)
            or tuple(dependency.name for dependency in self.dependencies)
            != tuple(sorted(REPLAY_DEPENDENCY_NAMES))
            or any(dependency.version != "1" for dependency in self.dependencies)
        ):
            raise ValueError("assessment dependency closure is invalid")
        runtime_assets = next(
            dependency
            for dependency in self.dependencies
            if dependency.name == REPLAY_RUNTIME_ASSET_DEPENDENCY
        )
        if runtime_assets.sha256 != self.runtime_asset_snapshot_manifest.sha256:
            raise ValueError("assessment runtime asset binding is invalid")
        package = self.replay_input.environment_package
        expected_refs = (
            (package.asset_catalog, None, _ASSET_CATALOG_SCHEMA),
            (package.package_manifest, None, _PACKAGE_MANIFEST_SCHEMA),
            (self.runtime_evidence, _RUNTIME_EVIDENCE_NAME, _RUNTIME_EVIDENCE_SCHEMA),
            (
                self.runtime_asset_snapshot_manifest,
                _RUNTIME_ASSET_SNAPSHOT_NAME,
                _RUNTIME_ASSET_SNAPSHOT_SCHEMA,
            ),
            (self.validation_report, _VALIDATION_REPORT_NAME, _VALIDATION_REPORT_SCHEMA),
        )
        if any(
            not _is_exact_json_ref(ref, name=name, schema_version=schema_version)
            for ref, name, schema_version in expected_refs
        ):
            raise ValueError("assessment artifact reference is invalid")
        return self


@dataclass(frozen=True, slots=True)
class System2ReplaySnapshotAssessmentResult:
    """The canonical assessment ref and the snapshot result it records."""

    assessment: ArtifactRef
    snapshot_validation: System2ReplaySnapshotValidationResult


@dataclass(frozen=True, slots=True)
class VerifiedSystem2ReplaySnapshotAssessment:
    """A canonical local record matching one newly executed snapshot recomputation.

    Verification covers the same-CAS package/runtime/snapshot closure and exact
    report/status/count match only.  It does not establish deployed-run origin,
    dynamic qualification, physical success, portable authority, or state-change
    authority.
    """

    assessment: ArtifactRef
    record: System2ReplaySnapshotAssessment
    snapshot_validation: SnapshotValidationResult


class System2ReplaySnapshotAssessmentError(RuntimeError):
    """Assessment recording or artifact-only verification could not complete."""

    def __init__(self, *, reason: _AssessmentFailureReason) -> None:
        self.reason = reason
        super().__init__(f"System 2 replay snapshot assessment stopped: {reason}")


class System2ReplaySnapshotAssessmentRecorder:
    """Record one replay snapshot assessment in its local CAS."""

    def __init__(self, *, scratch_parent: Path) -> None:
        self._scratch_parent = scratch_parent

    def record(
        self,
        *,
        acquisition: System2ReplayEvidenceAcquisitionResult,
    ) -> System2ReplaySnapshotAssessmentResult:
        """Recompute and record one local replay snapshot assessment."""

        try:
            snapshot_validation = System2ReplaySnapshotValidator(
                scratch_parent=self._scratch_parent,
            ).recompute(acquisition=acquisition)
        except (System2ReplaySnapshotValidationError, TypeError) as error:
            raise System2ReplaySnapshotAssessmentError(
                reason="snapshot_validation_failed"
            ) from error
        invocation = cast(Invocation, acquisition.invocation)
        state = acquisition.run_state
        replay_input = Text2EnvReplayInput.model_validate(
            invocation.effective_parameters,
            strict=True,
        )
        assessment = System2ReplaySnapshotAssessment(
            replay_run_id=snapshot_validation.replay_run_id,
            replay_invocation_digest=snapshot_validation.replay_invocation_digest,
            replay_skill_ref=f"{invocation.skill_id}@{invocation.skill_version}",
            max_attempts=invocation.max_attempts,
            replay_started_at=state.started_at,
            replay_ended_at=cast(datetime, state.ended_at),
            replay_input=replay_input,
            dependencies=tuple(
                sorted(invocation.dependencies, key=lambda dependency: dependency.name)
            ),
            runtime_evidence=snapshot_validation.runtime_evidence,
            runtime_asset_snapshot_manifest=(snapshot_validation.runtime_asset_snapshot_manifest),
            validation_report=snapshot_validation.snapshot_validation.validation_report,
            validation_status=snapshot_validation.snapshot_validation.validation_status,
            fail_count=snapshot_validation.snapshot_validation.fail_count,
            not_run_count=snapshot_validation.snapshot_validation.not_run_count,
        )
        payload = _canonical_json_bytes(assessment.model_dump(mode="json", warnings="error"))
        store = LocalArtifactStore(acquisition.artifact_root)
        try:
            assessment_ref = LocalPlannerArtifactPublisher(
                artifact_store=store,
                scratch_root=self._scratch_parent,
            ).publish(
                payload,
                name=_ASSESSMENT_NAME,
                media_type=_ASSESSMENT_MEDIA_TYPE,
                schema_version=_ASSESSMENT_SCHEMA,
            )
        except (ArtifactResolutionError, OSError, TypeError, ValueError) as error:
            raise System2ReplaySnapshotAssessmentError(
                reason="assessment_publish_failed"
            ) from error
        try:
            assessment_ref = _verified_assessment_ref(
                assessment_ref,
                payload=payload,
                store=store,
            )
        except (
            ArtifactResolutionError,
            OSError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            raise System2ReplaySnapshotAssessmentError(
                reason="assessment_verification_failed"
            ) from error
        return System2ReplaySnapshotAssessmentResult(
            assessment=assessment_ref,
            snapshot_validation=snapshot_validation,
        )


def _verified_assessment_ref(
    value: object,
    *,
    payload: bytes,
    store: LocalArtifactStore,
) -> ArtifactRef:
    value = _require_assessment_ref(value)
    published = store.resolve(value).path.read_bytes()
    if published != payload:
        raise ValueError("published assessment changed bytes")
    System2ReplaySnapshotAssessment.model_validate_json(published, strict=True)
    return value


def _require_assessment_ref(value: object) -> ArtifactRef:
    if (
        type(value) is not ArtifactRef
        or value.name != _ASSESSMENT_NAME
        or value.media_type != _ASSESSMENT_MEDIA_TYPE
        or value.schema_version != _ASSESSMENT_SCHEMA
        or value.uri != f"artifact://sha256/{value.sha256}"
    ):
        raise ValueError("assessment ref is invalid")
    return value


class System2ReplaySnapshotAssessmentVerifier:
    """Verify the narrow local snapshot claim using only an assessment artifact.

    The interface deliberately accepts neither an acquisition nor injected
    validation adapters.  Replay identity, timestamps, and non-runtime dependency
    identities are retained metadata rather than independently verified origin.
    It emits no fresh observation, receipt, decision, publishability result, or
    state transition; fixed replay qualification does not authorize this input.
    """

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        scratch_parent: Path,
    ) -> None:
        if type(artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be LocalArtifactStore")
        if not isinstance(scratch_parent, Path):
            raise TypeError("scratch_parent must be Path")
        self._artifact_store = artifact_store
        self._scratch_parent = scratch_parent

    def verify(
        self,
        *,
        assessment: ArtifactRef,
    ) -> VerifiedSystem2ReplaySnapshotAssessment:
        """Match a canonical assessment to real same-CAS snapshot recomputation."""

        try:
            assessment = _require_assessment_ref(assessment)
            payload = self._artifact_store.resolve(assessment).path.read_bytes()
            record = System2ReplaySnapshotAssessment.model_validate_json(payload, strict=True)
            canonical = _canonical_json_bytes(record.model_dump(mode="json", warnings="error"))
            if payload != canonical:
                raise ValueError("assessment payload is not canonical")
        except (
            ArtifactResolutionError,
            OSError,
            TypeError,
            UnicodeError,
            ValidationError,
            ValueError,
        ) as error:
            raise System2ReplaySnapshotAssessmentError(reason="assessment_invalid") from error
        try:
            scratch = self._scratch_parent.expanduser().absolute().resolve(strict=True)
            artifact_root = self._artifact_store.root.expanduser().absolute().resolve(strict=True)
            if _paths_overlap(scratch, artifact_root):
                raise ValueError("assessment scratch and artifact roots overlap")
            self._artifact_store.resolve(record.validation_report)
            recomputed = ValidateV2SnapshotAdapter(
                artifact_store=self._artifact_store,
                scratch_parent=self._scratch_parent,
            ).recompute(
                SnapshotValidationRequest(
                    environment_package=record.replay_input.environment_package,
                    runtime_evidence=record.runtime_evidence,
                    runtime_asset_snapshot_manifest=record.runtime_asset_snapshot_manifest,
                )
            )
        except (
            ArtifactResolutionError,
            OSError,
            RuntimeError,
            SnapshotValidationError,
            TypeError,
            ValueError,
        ) as error:
            raise System2ReplaySnapshotAssessmentError(
                reason="assessment_recompute_failed"
            ) from error
        if (
            record.validation_report != recomputed.validation_report
            or record.validation_status is not recomputed.validation_status
            or record.fail_count != recomputed.fail_count
            or record.not_run_count != recomputed.not_run_count
        ):
            raise System2ReplaySnapshotAssessmentError(reason="assessment_mismatch")
        return VerifiedSystem2ReplaySnapshotAssessment(
            assessment=assessment,
            record=record,
            snapshot_validation=recomputed,
        )


__all__ = [
    "System2ReplaySnapshotAssessment",
    "System2ReplaySnapshotAssessmentError",
    "System2ReplaySnapshotAssessmentRecorder",
    "System2ReplaySnapshotAssessmentResult",
    "System2ReplaySnapshotAssessmentVerifier",
    "VerifiedSystem2ReplaySnapshotAssessment",
]
