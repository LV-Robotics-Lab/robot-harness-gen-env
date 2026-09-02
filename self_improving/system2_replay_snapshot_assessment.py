"""Record one local-CAS snapshot assessment without advancing System 2 state.

The assessment is a deterministic record of recomputation over the supplied
local typed acquisition.  It is not a ``fresh_observation`` or
``WorldFactEvidence``, a complete Validate-v2 result, portable authority,
decision, publishable result, receipt, world-state transition, or claim of
physical success.  The replay factory's checked-in fixed qualification case
qualifies only that fixed wiring; it does not authorize this dynamic input,
acquisition, report, or outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import UUID4, AwareDatetime, NonNegativeInt, ValidationError

from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    Invocation,
    Text2EnvReplayInput,
    ValidationStatus,
)
from self_improving.harness.schemas.base import HarnessModel, Sha256
from self_improving.harness.system2.planner import LocalPlannerArtifactPublisher
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.system2_replay_snapshot_validation import (
    System2ReplaySnapshotValidationError,
    System2ReplaySnapshotValidationResult,
    System2ReplaySnapshotValidator,
)

_ASSESSMENT_SCHEMA = "harness.system2_replay_snapshot_assessment.v1"
_ASSESSMENT_SCOPE = "local_cas_snapshot_recomputation"
_ASSESSMENT_NAME = "system2_replay_snapshot_assessment"
_ASSESSMENT_MEDIA_TYPE = "application/json"

_AssessmentFailureReason = Literal[
    "snapshot_validation_failed",
    "assessment_publish_failed",
    "assessment_verification_failed",
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


class _ReplaySnapshotAssessment(HarnessModel):
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


@dataclass(frozen=True, slots=True)
class System2ReplaySnapshotAssessmentResult:
    """The canonical assessment ref and the snapshot result it records."""

    assessment: ArtifactRef
    snapshot_validation: System2ReplaySnapshotValidationResult


class System2ReplaySnapshotAssessmentError(RuntimeError):
    """Snapshot recomputation or assessment recording could not complete."""

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
        assessment = _ReplaySnapshotAssessment(
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
    if (
        type(value) is not ArtifactRef
        or value.name != _ASSESSMENT_NAME
        or value.media_type != _ASSESSMENT_MEDIA_TYPE
        or value.schema_version != _ASSESSMENT_SCHEMA
        or value.uri != f"artifact://sha256/{value.sha256}"
    ):
        raise ValueError("assessment ref is invalid")
    published = store.resolve(value).path.read_bytes()
    if published != payload:
        raise ValueError("published assessment changed bytes")
    _ReplaySnapshotAssessment.model_validate_json(published, strict=True)
    return value


__all__ = [
    "System2ReplaySnapshotAssessmentError",
    "System2ReplaySnapshotAssessmentRecorder",
    "System2ReplaySnapshotAssessmentResult",
]
