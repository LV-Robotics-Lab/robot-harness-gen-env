"""Acquire one durable replay result for a promoted System 2 package input.

The production replay factory remains the only assembly seam.  Its fixed
qualification case authorizes the live wiring and descriptor, not this dynamic
input or result.  This module binds that application to the promotion
destination, invokes one dynamic replay, and reconciles the returned terminal
state with the durable run interfaces and local destination CAS.  The result is
not a portable or offline authority and does not claim validation, publication,
or System 2 state advancement.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from self_improving.harness.artifacts import ArtifactResolutionError, LocalArtifactStore
from self_improving.harness.event_journal import EventPage, StoredRunEvent
from self_improving.harness.events import RunEvent
from self_improving.harness.registry import RunPersistenceError
from self_improving.harness.replay_application import (
    ReplayApplicationSettings,
    create_replay_application,
)
from self_improving.harness.replay_dependencies import REPLAY_DEPENDENCY_NAMES
from self_improving.harness.schemas import (
    ArtifactRef,
    DependencyRef,
    EnvironmentPackage,
    ExecutionReproducibility,
    Invocation,
    RunState,
    RunStatus,
    SkillDescriptorV2,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
)
from self_improving.system2_replay_input_promotion import PromotedSystem2ReplayInput

_SKILL_ID = "text2env.replay"
_SKILL_VERSION = "1.0.0"
_ENVIRONMENT_PACKAGE_SCHEMA = "harness.environment_package.v1"


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


@dataclass(frozen=True, slots=True)
class System2ReplayEvidenceAcquisitionResult:
    """One local reconciliation result for a factory-qualified dynamic replay."""

    run_state: RunState
    invocation: Invocation | None
    event_page: EventPage
    typed_output: Text2EnvReplayOutput | None
    artifact_root: Path
    journal_path: Path


class System2ReplayEvidenceAcquisitionError(RuntimeError):
    """Replay setup or durable evidence reconciliation failed."""

    def __init__(self, *, reason: str, run_state: RunState | None = None) -> None:
        self.reason = reason
        self.run_state = run_state
        super().__init__(f"System 2 replay evidence acquisition stopped: {reason}")


class System2ReplayEvidenceAcquirer:
    """Deep module joining promotion, qualified replay, durability, and CAS checks."""

    def __init__(self, *, application_settings: ReplayApplicationSettings) -> None:
        if type(application_settings) is not ReplayApplicationSettings:
            raise TypeError("application_settings must be exact ReplayApplicationSettings")
        self._settings = application_settings

    def acquire(
        self,
        *,
        promoted: PromotedSystem2ReplayInput,
    ) -> System2ReplayEvidenceAcquisitionResult:
        """Invoke one dynamic replay and return its reconciled terminal record."""

        if type(promoted) is not PromotedSystem2ReplayInput:
            raise TypeError("promoted must be exact PromotedSystem2ReplayInput")
        artifact_root = self._preflight(promoted)
        artifact_store = LocalArtifactStore(artifact_root)
        self._verify_promoted_package(promoted, artifact_store)
        try:
            application = create_replay_application(self._settings)
        except Exception as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="application_factory_failed"
            ) from error
        self._verify_application(application, artifact_root)
        try:
            returned = application.replay(promoted.replay_input)
        except RunPersistenceError as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="run_persistence_failed",
                run_state=_diagnostic_state(error.state, expected_run_id=error.run_id),
            ) from error
        except Exception as error:
            raise System2ReplayEvidenceAcquisitionError(reason="replay_failed") from error
        if type(returned) is not RunState or returned.status is RunStatus.RUNNING:
            raise System2ReplayEvidenceAcquisitionError(
                reason="returned_state_invalid",
                run_state=returned if type(returned) is RunState else None,
            )

        try:
            persisted = application.run_state(returned.run_id)
            invocation = application.invocation(returned.run_id)
            page = application.events(
                after_event_id=0,
                run_id=returned.run_id,
                limit=200,
            )
            persisted_after_events = application.run_state(returned.run_id)
            invocation_after_events = application.invocation(returned.run_id)
        except (
            AttributeError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="durable_read_failed",
                run_state=returned,
            ) from error

        self._verify_durable_state(
            returned=returned,
            persisted=persisted,
            invocation=invocation,
            page=page,
            persisted_after_events=persisted_after_events,
            invocation_after_events=invocation_after_events,
            replay_input=promoted.replay_input,
        )
        typed_output = self._verify_artifacts(
            state=returned,
            artifact_store=artifact_store,
        )
        _, journal_path = self._verify_application(
            application,
            artifact_root,
            run_state=returned,
        )
        return System2ReplayEvidenceAcquisitionResult(
            run_state=returned,
            invocation=invocation,
            event_page=page,
            typed_output=typed_output,
            artifact_root=artifact_root,
            journal_path=journal_path,
        )

    def _preflight(self, promoted: PromotedSystem2ReplayInput) -> Path:
        state_root = self._settings.state_root
        if not isinstance(state_root, Path):
            raise System2ReplayEvidenceAcquisitionError(reason="state_root_invalid")
        canonical_state = state_root.expanduser().absolute()
        if (
            canonical_state.exists()
            or canonical_state.is_symlink()
            or canonical_state != canonical_state.resolve(strict=False)
        ):
            raise System2ReplayEvidenceAcquisitionError(reason="state_root_not_fresh")
        try:
            settings_root = _canonical_directory(self._settings.evidence_artifact_root)
            promoted_root = _canonical_directory(promoted.destination_artifact_root)
        except (OSError, TypeError, ValueError) as error:
            raise System2ReplayEvidenceAcquisitionError(reason="artifact_root_invalid") from error
        if settings_root != promoted_root:
            raise System2ReplayEvidenceAcquisitionError(reason="artifact_root_mismatch")
        if type(promoted.replay_input) is not Text2EnvReplayInput:
            raise System2ReplayEvidenceAcquisitionError(reason="promoted_input_invalid")
        if type(promoted.environment_package_ref) is not ArtifactRef:
            raise System2ReplayEvidenceAcquisitionError(reason="promoted_input_invalid")
        return settings_root

    @staticmethod
    def _verify_promoted_package(
        promoted: PromotedSystem2ReplayInput,
        artifact_store: LocalArtifactStore,
    ) -> None:
        package_ref = promoted.environment_package_ref
        package = promoted.replay_input.environment_package
        if (
            type(package) is not EnvironmentPackage
            or package_ref.uri != _artifact_uri(package_ref)
            or package_ref.media_type != "application/json"
            or package_ref.schema_version != _ENVIRONMENT_PACKAGE_SCHEMA
            or type(package.asset_catalog) is not ArtifactRef
            or type(package.package_manifest) is not ArtifactRef
            or package.asset_catalog.media_type != "application/json"
            or package.package_manifest.media_type != "application/json"
            or package.asset_catalog.uri != _artifact_uri(package.asset_catalog)
            or package.package_manifest.uri != _artifact_uri(package.package_manifest)
        ):
            raise System2ReplayEvidenceAcquisitionError(reason="promoted_package_invalid")
        try:
            package_bytes = artifact_store.resolve(package_ref).path.read_bytes()
            artifact_store.resolve(package.asset_catalog)
            artifact_store.resolve(package.package_manifest)
            reloaded = EnvironmentPackage.model_validate_json(package_bytes)
        except (ArtifactResolutionError, OSError, ValidationError, ValueError) as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="promoted_package_invalid"
            ) from error
        if reloaded != package or package_bytes != _canonical_json_bytes(
            package.model_dump(mode="json")
        ):
            raise System2ReplayEvidenceAcquisitionError(reason="promoted_package_invalid")

    def _verify_application(
        self,
        application: object,
        artifact_root: Path,
        *,
        run_state: RunState | None = None,
    ) -> tuple[Path, Path]:
        try:
            skills = application.skills
            application_root = _canonical_directory(application.artifact_root)
            state_root = _canonical_directory(self._settings.state_root)
            journal_path = _canonical_file(application.journal_path)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="application_invalid",
                run_state=run_state,
            ) from error
        if (
            type(skills) is not tuple
            or len(skills) != 1
            or not _is_replay_descriptor(skills[0])
            or application_root != artifact_root
            or journal_path != state_root / "harness.sqlite3"
        ):
            raise System2ReplayEvidenceAcquisitionError(
                reason="application_invalid",
                run_state=run_state,
            )
        return application_root, journal_path

    @staticmethod
    def _verify_durable_state(
        *,
        returned: RunState,
        persisted: object,
        invocation: object,
        page: object,
        persisted_after_events: object,
        invocation_after_events: object,
        replay_input: Text2EnvReplayInput,
    ) -> None:
        if (
            type(persisted) is not RunState
            or persisted != returned
            or type(persisted_after_events) is not RunState
            or persisted_after_events != returned
            or returned.skill_id != _SKILL_ID
            or returned.skill_version != _SKILL_VERSION
            or type(page) is not EventPage
            or type(page.events) is not tuple
            or type(page.has_more) is not bool
            or page.has_more
            or len(page.events) != len(returned.events)
        ):
            raise System2ReplayEvidenceAcquisitionError(
                reason="durable_state_mismatch",
                run_state=returned,
            )
        if any(type(item) is not StoredRunEvent for item in page.events):
            raise System2ReplayEvidenceAcquisitionError(
                reason="durable_events_mismatch",
                run_state=returned,
            )
        event_ids = tuple(item.event_id for item in page.events)
        if (
            type(page.last_event_id) is not int
            or page.last_event_id < 1
            or any(type(event_id) is not int or event_id < 1 for event_id in event_ids)
            or event_ids != tuple(sorted(set(event_ids)))
            or page.last_event_id != event_ids[-1]
            or any(
                type(item.envelope) is not RunEvent
                or item.envelope.run_id != returned.run_id
                or item.envelope.skill_id != _SKILL_ID
                or item.envelope.skill_version != _SKILL_VERSION
                or item.envelope.event != returned.events[index]
                for index, item in enumerate(page.events)
            )
        ):
            raise System2ReplayEvidenceAcquisitionError(
                reason="durable_events_mismatch",
                run_state=returned,
            )
        if returned.attempt == 0:
            if (
                returned.status not in {RunStatus.BLOCKED, RunStatus.FAILED}
                or returned.max_attempts != 0
                or returned.invocation_digest is not None
                or invocation is not None
                or invocation_after_events is not None
            ):
                raise System2ReplayEvidenceAcquisitionError(
                    reason="durable_invocation_mismatch",
                    run_state=returned,
                )
            return
        if (
            returned.max_attempts != 2
            or type(invocation) is not Invocation
            or type(invocation_after_events) is not Invocation
            or invocation_after_events != invocation
            or invocation.run_id != returned.run_id
            or invocation.skill_id != _SKILL_ID
            or invocation.skill_version != _SKILL_VERSION
            or invocation.max_attempts != 2
            or invocation.invocation_digest != returned.invocation_digest
            or invocation.effective_parameters != replay_input.model_dump(mode="json")
            or type(invocation.dependencies) is not tuple
            or any(type(item) is not DependencyRef for item in invocation.dependencies)
            or tuple(item.name for item in invocation.dependencies)
            != tuple(sorted(REPLAY_DEPENDENCY_NAMES))
        ):
            raise System2ReplayEvidenceAcquisitionError(
                reason="durable_invocation_mismatch",
                run_state=returned,
            )

    @staticmethod
    def _verify_artifacts(
        *,
        state: RunState,
        artifact_store: LocalArtifactStore,
    ) -> Text2EnvReplayOutput | None:
        event_refs = tuple(ref for event in state.events for ref in event.artifact_refs)
        if state.attempt == 0:
            if (
                state.blocker is None
                or state.blocker.artifact_refs != state.artifacts
                or not _canonical_refs((*state.artifacts, *event_refs))
            ):
                raise System2ReplayEvidenceAcquisitionError(
                    reason="run_artifact_invalid",
                    run_state=state,
                )
        if state.attempt > 0:
            try:
                if state.blocker is not None and any(
                    ref not in state.artifacts for ref in state.blocker.artifact_refs
                ):
                    raise ValueError("blocker artifact is absent from terminal artifacts")
                unique_refs: list[ArtifactRef] = []
                for ref in (*state.artifacts, *event_refs):
                    if type(ref) is not ArtifactRef or ref.uri != _artifact_uri(ref):
                        raise ValueError("run artifact is not a canonical CAS reference")
                    if ref not in unique_refs:
                        unique_refs.append(ref)
                for ref in unique_refs:
                    artifact_store.resolve(ref)
            except (ArtifactResolutionError, OSError, ValueError) as error:
                raise System2ReplayEvidenceAcquisitionError(
                    reason="run_artifact_invalid",
                    run_state=state,
                ) from error
        if state.status is not RunStatus.SUCCEEDED:
            if state.output is not None:
                raise System2ReplayEvidenceAcquisitionError(
                    reason="typed_output_invalid",
                    run_state=state,
                )
            return None
        try:
            typed = Text2EnvReplayOutput.model_validate(state.output)
        except ValidationError as error:
            raise System2ReplayEvidenceAcquisitionError(
                reason="typed_output_invalid",
                run_state=state,
            ) from error
        output_refs = (typed.runtime_evidence, *typed.replay_artifacts)
        if (
            typed.model_dump(mode="json") != state.output
            or any(ref not in state.artifacts for ref in output_refs)
            or any(ref.uri != _artifact_uri(ref) for ref in output_refs)
        ):
            raise System2ReplayEvidenceAcquisitionError(
                reason="typed_output_invalid",
                run_state=state,
            )
        return typed


def _canonical_directory(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError("artifact root must be a Path")
    absolute = value.expanduser().absolute()
    resolved = absolute.resolve(strict=True)
    if absolute != resolved or value.is_symlink() or not resolved.is_dir():
        raise ValueError("artifact root must be one canonical directory")
    return resolved


def _canonical_file(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError("journal path must be a Path")
    absolute = value.expanduser().absolute()
    resolved = absolute.resolve(strict=True)
    if absolute != resolved or value.is_symlink() or not resolved.is_file():
        raise ValueError("journal path must be one canonical file")
    return resolved


def _artifact_uri(ref: ArtifactRef) -> str:
    return f"artifact://sha256/{ref.sha256}"


def _canonical_refs(refs: tuple[ArtifactRef, ...]) -> bool:
    return all(type(ref) is ArtifactRef and ref.uri == _artifact_uri(ref) for ref in refs)


def _is_replay_descriptor(value: object) -> bool:
    return (
        type(value) is SkillDescriptorV2
        and value.skill_id == _SKILL_ID
        and value.version == _SKILL_VERSION
        and value.input_schema == "harness.text2env_replay_input.v1"
        and value.output_schema == "harness.text2env_replay_output.v1"
        and value.reproducibility is ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
        and value.max_attempts == 2
    )


def _diagnostic_state(value: object, *, expected_run_id: object) -> RunState | None:
    if (
        type(value) is not RunState
        or value.run_id != expected_run_id
        or value.status is RunStatus.RUNNING
    ):
        return None
    refs = [*value.artifacts]
    for event in value.events:
        refs.extend(event.artifact_refs)
    if value.blocker is not None:
        refs.extend(value.blocker.artifact_refs)
    if any(type(ref) is not ArtifactRef or ref.uri != _artifact_uri(ref) for ref in refs):
        return None
    return value


__all__ = [
    "System2ReplayEvidenceAcquirer",
    "System2ReplayEvidenceAcquisitionError",
    "System2ReplayEvidenceAcquisitionResult",
]
