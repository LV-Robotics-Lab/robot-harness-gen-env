"""Recompute snapshot validation for one locally reconciled replay result.

This local seam starts from a successful acquisition and replays the committed
snapshot validator against the acquisition CAS.  It is not a deployed-replay
receipt, portable provenance closure, complete Validate-v2 result, publication
decision, or System 2 state transition.  Compile/replay portable receipts,
provenance closure, a standalone media receipt, and a RuntimeConfig receipt are
outside this result.  Legacy source locators embedded in reconstructed metadata
may still be inspected; this module makes no zero-lookup claim.

The replay factory's checked-in fixed qualification case authorizes its wiring,
not this dynamic acquisition, input, report, or physical outcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.replay_dependencies import REPLAY_DEPENDENCY_NAMES
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    Invocation,
    RunState,
    RunStatus,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
    ValidationStatus,
)
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.validate_v2_snapshot import (
    SnapshotValidationError,
    SnapshotValidationRequest,
    SnapshotValidationResult,
    ValidateV2SnapshotAdapter,
)

_REPLAY_SKILL_ID = "text2env.replay"
_REPLAY_SKILL_VERSION = "1.0.0"
_RUNTIME_EVIDENCE_NAME = "runtime_evidence"
_RUNTIME_EVIDENCE_SCHEMA = "robotwin.scene_runtime_evidence.v2"
_RUNTIME_ASSET_SNAPSHOT_NAME = "runtime_asset_snapshot"
_RUNTIME_ASSET_SNAPSHOT_SCHEMA = "harness.runtime_asset_snapshot.v1"
_VALIDATION_REPORT_NAME = "snapshot_validation_report"
_VALIDATION_REPORT_SCHEMA = "robotwin.scene_validation.v1"


@dataclass(frozen=True, slots=True)
class System2ReplaySnapshotValidationResult:
    """The replay identity and narrow snapshot-recomputation result."""

    replay_run_id: UUID
    replay_invocation_digest: str
    runtime_evidence: ArtifactRef
    runtime_asset_snapshot_manifest: ArtifactRef
    snapshot_validation: SnapshotValidationResult


class System2ReplaySnapshotValidationError(RuntimeError):
    """Replay reconciliation or snapshot recomputation could not complete."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"System 2 replay snapshot validation stopped: {reason}")


class System2ReplaySnapshotValidator:
    """Join a successful replay snapshot to deterministic recomputation."""

    def __init__(self, *, scratch_parent: Path) -> None:
        if not isinstance(scratch_parent, Path):
            raise TypeError("scratch_parent must be Path")
        self._scratch_parent = _canonical_scratch(scratch_parent)

    def recompute(
        self,
        *,
        acquisition: System2ReplayEvidenceAcquisitionResult,
    ) -> System2ReplaySnapshotValidationResult:
        """Recompute a report for one successful, internally bound replay snapshot."""

        if type(acquisition) is not System2ReplayEvidenceAcquisitionResult:
            raise System2ReplaySnapshotValidationError(reason="acquisition_invalid")
        state = acquisition.run_state
        if type(state) is not RunState:
            raise System2ReplaySnapshotValidationError(reason="acquisition_invalid")
        if state.status is not RunStatus.SUCCEEDED:
            raise System2ReplaySnapshotValidationError(reason="replay_not_succeeded")
        invocation, replay_input, output = _bound_replay(acquisition, state)
        artifact_store = _artifact_store(acquisition.artifact_root)
        if _paths_overlap(self._scratch_parent, artifact_store.root):
            raise System2ReplaySnapshotValidationError(reason="scratch_invalid")
        runtime_evidence, snapshot = _bound_runtime_refs(
            output=output,
            state=state,
            invocation=invocation,
            artifact_store=artifact_store,
        )
        request = SnapshotValidationRequest(
            environment_package=replay_input.environment_package,
            runtime_evidence=runtime_evidence,
            runtime_asset_snapshot_manifest=snapshot,
        )
        try:
            recomputed = ValidateV2SnapshotAdapter(
                artifact_store=artifact_store,
                scratch_parent=self._scratch_parent,
            ).recompute(request)
        except SnapshotValidationError as error:
            raise System2ReplaySnapshotValidationError(
                reason="snapshot_recompute_failed"
            ) from error
        verified = _verified_snapshot_result(recomputed, artifact_store)
        return System2ReplaySnapshotValidationResult(
            replay_run_id=state.run_id,
            replay_invocation_digest=invocation.invocation_digest,
            runtime_evidence=runtime_evidence,
            runtime_asset_snapshot_manifest=snapshot,
            snapshot_validation=verified,
        )


def _bound_replay(
    acquisition: System2ReplayEvidenceAcquisitionResult,
    state: RunState,
) -> tuple[Invocation, Text2EnvReplayInput, Text2EnvReplayOutput]:
    invocation = acquisition.invocation
    output = acquisition.typed_output
    if type(invocation) is not Invocation or type(output) is not Text2EnvReplayOutput:
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid")
    try:
        replay_input = Text2EnvReplayInput.model_validate(
            invocation.effective_parameters,
            strict=True,
        )
        serialized_output = output.model_dump(mode="json", warnings="error")
    except (
        AttributeError,
        PydanticSerializationError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid") from error
    if (
        invocation.run_id != state.run_id
        or invocation.invocation_digest != state.invocation_digest
        or invocation.skill_id != _REPLAY_SKILL_ID
        or invocation.skill_version != _REPLAY_SKILL_VERSION
        or state.skill_id != invocation.skill_id
        or state.skill_version != invocation.skill_version
        or invocation.max_attempts != state.max_attempts
        or invocation.max_attempts != 2
        or state.max_attempts != 2
        or state.attempt < 1
        or state.output != serialized_output
        or invocation.effective_parameters != replay_input.model_dump(mode="json")
        or type(invocation.dependencies) is not tuple
        or any(
            type(dependency) is not DependencyRef or dependency.version != "1"
            for dependency in invocation.dependencies
        )
        or tuple(dependency.name for dependency in invocation.dependencies)
        != tuple(sorted(REPLAY_DEPENDENCY_NAMES))
    ):
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid")
    try:
        tuple(
            DependencyRef.model_validate(
                dependency.model_dump(mode="python"),
                strict=True,
            )
            for dependency in invocation.dependencies
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid") from error
    return invocation, replay_input, output


def _artifact_store(value: object) -> LocalArtifactStore:
    if not isinstance(value, Path):
        raise System2ReplaySnapshotValidationError(reason="artifact_root_invalid")
    try:
        absolute = value.expanduser().absolute()
        resolved = absolute.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise System2ReplaySnapshotValidationError(reason="artifact_root_invalid") from error
    if absolute != resolved or value.is_symlink() or not resolved.is_dir():
        raise System2ReplaySnapshotValidationError(reason="artifact_root_invalid")
    return LocalArtifactStore(resolved)


def _canonical_scratch(value: Path) -> Path:
    try:
        absolute = value.expanduser().absolute()
        resolved = absolute.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise System2ReplaySnapshotValidationError(reason="scratch_invalid") from error
    if absolute != resolved or value.is_symlink() or not resolved.is_dir():
        raise System2ReplaySnapshotValidationError(reason="scratch_invalid")
    return resolved


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _bound_runtime_refs(
    *,
    output: Text2EnvReplayOutput,
    state: RunState,
    invocation: Invocation,
    artifact_store: LocalArtifactStore,
) -> tuple[ArtifactRef, ArtifactRef]:
    if any(type(ref) is not ArtifactRef for ref in output.replay_artifacts):
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid")
    runtime = output.runtime_evidence
    matches = tuple(
        ref
        for ref in output.replay_artifacts
        if type(ref) is ArtifactRef and ref.name == _RUNTIME_ASSET_SNAPSHOT_NAME
    )
    if len(matches) != 1:
        raise System2ReplaySnapshotValidationError(reason="runtime_asset_snapshot_invalid")
    snapshot = matches[0]
    _require_ref(
        runtime,
        name=_RUNTIME_EVIDENCE_NAME,
        schema_version=_RUNTIME_EVIDENCE_SCHEMA,
        reason="runtime_evidence_invalid",
    )
    _require_ref(
        snapshot,
        name=_RUNTIME_ASSET_SNAPSHOT_NAME,
        schema_version=_RUNTIME_ASSET_SNAPSHOT_SCHEMA,
        reason="runtime_asset_snapshot_invalid",
    )
    runtime_asset_dependency = next(
        dependency
        for dependency in invocation.dependencies
        if dependency.name == "text2env.replay.runtime_assets"
    )
    if runtime_asset_dependency.sha256 != snapshot.sha256:
        raise System2ReplaySnapshotValidationError(reason="runtime_asset_snapshot_invalid")
    if runtime not in state.artifacts:
        raise System2ReplaySnapshotValidationError(reason="runtime_evidence_invalid")
    if snapshot not in state.artifacts:
        raise System2ReplaySnapshotValidationError(reason="runtime_asset_snapshot_invalid")
    if any(type(ref) is not ArtifactRef for ref in state.artifacts):
        raise System2ReplaySnapshotValidationError(reason="replay_binding_invalid")
    try:
        artifact_store.resolve(runtime)
        artifact_store.resolve(snapshot)
    except (ArtifactResolutionError, OSError, ValueError) as error:
        raise System2ReplaySnapshotValidationError(reason="replay_artifact_invalid") from error
    return runtime, snapshot


def _require_ref(
    value: object,
    *,
    name: str,
    schema_version: str,
    reason: str,
) -> None:
    if (
        type(value) is not ArtifactRef
        or value.name != name
        or value.media_type != "application/json"
        or value.schema_version != schema_version
        or value.uri != f"artifact://sha256/{value.sha256}"
    ):
        raise System2ReplaySnapshotValidationError(reason=reason)


def _verified_snapshot_result(
    value: object,
    artifact_store: LocalArtifactStore,
) -> SnapshotValidationResult:
    if type(value) is not SnapshotValidationResult:
        raise System2ReplaySnapshotValidationError(reason="snapshot_result_invalid")
    report_ref = value.validation_report
    _require_ref(
        report_ref,
        name=_VALIDATION_REPORT_NAME,
        schema_version=_VALIDATION_REPORT_SCHEMA,
        reason="snapshot_result_invalid",
    )
    if (
        type(value.validation_status) is not ValidationStatus
        or type(value.fail_count) is not int
        or value.fail_count < 0
        or type(value.not_run_count) is not int
        or value.not_run_count < 0
    ):
        raise System2ReplaySnapshotValidationError(reason="snapshot_result_invalid")
    try:
        payload = artifact_store.resolve(report_ref).path.read_bytes()
        report = json.loads(payload)
    except (
        ArtifactResolutionError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise System2ReplaySnapshotValidationError(reason="snapshot_result_invalid") from error
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != _VALIDATION_REPORT_SCHEMA
        or report.get("status") != value.validation_status.value
        or report.get("fail_count") != value.fail_count
        or report.get("not_run_count") != value.not_run_count
    ):
        raise System2ReplaySnapshotValidationError(reason="snapshot_result_invalid")
    return value


__all__ = [
    "System2ReplaySnapshotValidationError",
    "System2ReplaySnapshotValidationResult",
    "System2ReplaySnapshotValidator",
]
