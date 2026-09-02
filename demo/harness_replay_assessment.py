"""Durable Workbench index for one verified local replay snapshot assessment.

The module joins a typed replay acquisition to the existing local assessment
recorder, the shared SQLite run authority, and artifact-only recomputation.  Its
small interface returns only an immutable display projection.  Its claim is
limited to one same-local SQLite binding plus same-CAS snapshot recomputation;
it does not independently authenticate deployed-run provenance, add a run event,
mutate a terminal RunState or Invocation, create a fresh observation, complete
Validate-v2, issue a receipt or decision, claim physical success or
publishability, qualify the dynamic input, establish portable authority, or
advance System 2 state.  The replay factory's fixed qualification case does not
authorize this dynamic run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, Field, ValidationError, model_validator

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import (
    EventJournalCorruptionError,
    EventPage,
    SQLiteEventJournal,
)
from self_improving.harness.run_store import RunStoreCorruptionError, SQLiteRunStore
from self_improving.harness.schemas import (
    ArtifactRef,
    Invocation,
    RunState,
    RunStatus,
    Text2EnvReplayOutput,
    ValidationStatus,
)
from self_improving.harness.schemas.base import (
    HarnessModel,
    PositiveInt,
    Sha256,
)
from self_improving.system2_replay_evidence_acquisition import (
    System2ReplayEvidenceAcquisitionResult,
)
from self_improving.system2_replay_snapshot_assessment import (
    System2ReplaySnapshotAssessmentError,
    System2ReplaySnapshotAssessmentRecorder,
    System2ReplaySnapshotAssessmentVerifier,
    VerifiedSystem2ReplaySnapshotAssessment,
)

_VIEW_SCHEMA = "harness.workbench_replay_snapshot_assessment.v1"
_INDEX_SCHEMA = "harness.workbench_replay_snapshot_assessment_index.v1"
_ASSESSMENT_NAME = "system2_replay_snapshot_assessment"
_ASSESSMENT_MEDIA_TYPE = "application/json"
_ASSESSMENT_SCHEMA = "harness.system2_replay_snapshot_assessment.v1"
_REPLAY_SKILL_ID = "text2env.replay"
_REPLAY_SKILL_VERSION = "1.0.0"
_RUNTIME_ASSET_SNAPSHOT_NAME = "runtime_asset_snapshot"
_MAX_EVENT_COUNT = 200
_SQLITE_MAX_INTEGER = 2**63 - 1
_BROWSER_MAX_INTEGER = 2**53 - 1
_CanonicalPositiveDecimal = Annotated[
    str,
    Field(strict=True, pattern=r"^[1-9][0-9]{0,18}$"),
]
_BrowserNonNegativeInt = Annotated[
    int,
    Field(strict=True, ge=0, le=_BROWSER_MAX_INTEGER),
]


class WorkbenchReplayAssessmentInputError(ValueError):
    """The supplied configuration or request is outside the fixed interface."""


class WorkbenchReplayAssessmentNotFoundError(LookupError):
    """No replay snapshot assessment is indexed for the requested run."""


class WorkbenchReplayAssessmentAuthorityError(RuntimeError):
    """The immutable index no longer agrees with its SQLite or CAS authority."""


class WorkbenchReplayAssessmentUnavailableError(RuntimeError):
    """The local SQLite or CAS dependency cannot complete the operation."""


class WorkbenchReplayAssessmentView(HarnessModel):
    """Path-free same-local display projection, not a Validate-v2 decision or receipt."""

    schema_version: Literal["harness.workbench_replay_snapshot_assessment.v1"] = _VIEW_SCHEMA
    run_id: UUID4
    invocation_digest: Sha256
    run_status: Literal[RunStatus.SUCCEEDED]
    terminal_event_count: PositiveInt
    terminal_last_event_id: _CanonicalPositiveDecimal
    assessment_sha256: Sha256
    assessment_bytes: _CanonicalPositiveDecimal
    validation_report_sha256: Sha256
    validation_report_bytes: _CanonicalPositiveDecimal
    validation_status: ValidationStatus
    fail_count: _BrowserNonNegativeInt
    not_run_count: _BrowserNonNegativeInt

    @model_validator(mode="after")
    def validation_status_matches_counts(self) -> WorkbenchReplayAssessmentView:
        if any(
            int(value) > _SQLITE_MAX_INTEGER
            for value in (
                self.terminal_last_event_id,
                self.assessment_bytes,
                self.validation_report_bytes,
            )
        ):
            raise ValueError("display decimal exceeds local integer range")
        if (
            self.terminal_event_count > _MAX_EVENT_COUNT
            or int(self.terminal_last_event_id) < self.terminal_event_count
        ):
            raise ValueError("terminal event identity is impossible")
        if self.fail_count > 0:
            expected = ValidationStatus.FAIL
        elif self.not_run_count > 0:
            expected = ValidationStatus.INCOMPLETE
        else:
            expected = ValidationStatus.PASS
        if self.validation_status is not expected:
            raise ValueError("validation status and counts disagree")
        return self


class _WorkbenchReplayAssessmentIndex(HarnessModel):
    """Private immutable row, including the full local authority identities."""

    schema_version: Literal["harness.workbench_replay_snapshot_assessment_index.v1"] = _INDEX_SCHEMA
    view: WorkbenchReplayAssessmentView
    invocation_sha256: Sha256
    run_state_sha256: Sha256
    event_page_sha256: Sha256


class WorkbenchReplayAssessment:
    """Bind and inspect one immutable same-local replay-assessment read model."""

    def __init__(
        self,
        *,
        journal_path: Path,
        artifact_root: Path,
        scratch_parent: Path,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if not isinstance(journal_path, Path):
            raise WorkbenchReplayAssessmentInputError("journal_path must be a Path")
        if not isinstance(artifact_root, Path):
            raise WorkbenchReplayAssessmentInputError("artifact_root must be a Path")
        if not isinstance(scratch_parent, Path):
            raise WorkbenchReplayAssessmentInputError("scratch_parent must be a Path")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 2**31 - 1:
            raise WorkbenchReplayAssessmentInputError(
                "busy_timeout_ms must fit a positive SQLite integer"
            )
        try:
            self._journal_path = journal_path.expanduser().resolve(strict=True)
            self._artifact_root = artifact_root.expanduser().resolve(strict=True)
            self._scratch_parent = scratch_parent.expanduser().resolve(strict=True)
            if not self._journal_path.is_file():
                raise OSError("journal is not a file")
            if not self._artifact_root.is_dir():
                raise OSError("artifact root is not a directory")
            if not self._scratch_parent.is_dir():
                raise OSError("scratch parent is not a directory")
        except (OSError, RuntimeError) as error:
            raise WorkbenchReplayAssessmentUnavailableError(
                "Workbench replay assessment storage is unavailable"
            ) from error
        if _paths_overlap(self._scratch_parent, self._artifact_root):
            raise WorkbenchReplayAssessmentInputError(
                "scratch_parent and artifact_root must be disjoint"
            )
        if self._journal_path.is_relative_to(self._artifact_root):
            raise WorkbenchReplayAssessmentInputError("journal_path must be outside artifact_root")
        self._busy_timeout_ms = busy_timeout_ms
        self._artifact_store = LocalArtifactStore(self._artifact_root)
        self._initialize()

    def record(
        self,
        *,
        acquisition: System2ReplayEvidenceAcquisitionResult,
    ) -> WorkbenchReplayAssessmentView:
        """Record one assessment and commit its immutable index row last."""

        if type(acquisition) is not System2ReplayEvidenceAcquisitionResult:
            raise WorkbenchReplayAssessmentInputError(
                "acquisition must be an exact System2ReplayEvidenceAcquisitionResult"
            )
        if (
            type(acquisition.run_state) is not RunState
            or type(acquisition.invocation) is not Invocation
            or type(acquisition.event_page) is not EventPage
            or type(acquisition.typed_output) is not Text2EnvReplayOutput
        ):
            raise WorkbenchReplayAssessmentInputError("acquisition fields are invalid")
        self._require_configured_acquisition(acquisition)
        persisted, invocation, history = self._read_terminal_authority(acquisition.run_state.run_id)
        _require_exact_authority(
            observed=(persisted, invocation, history),
            expected=(
                acquisition.run_state,
                acquisition.invocation,
                acquisition.event_page,
            ),
            message="Replay acquisition differs from the configured durable authority",
        )
        if _strict_terminal_output(persisted) != acquisition.typed_output:
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay acquisition output differs from terminal RunState"
            )
        try:
            recorded = System2ReplaySnapshotAssessmentRecorder(
                scratch_parent=self._scratch_parent,
            ).record(acquisition=acquisition)
        except System2ReplaySnapshotAssessmentError as error:
            if error.reason == "snapshot_validation_failed":
                raise WorkbenchReplayAssessmentAuthorityError(
                    "Replay acquisition cannot support a local snapshot assessment"
                ) from error
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay snapshot assessment could not be recorded"
            ) from error
        except ValidationError as error:
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay assessment record is invalid for the durable authority"
            ) from error

        verified = self._verify_assessment(recorded.assessment)
        confirmed_state, confirmed_invocation, confirmed_history = self._read_terminal_authority(
            acquisition.run_state.run_id
        )
        _require_exact_authority(
            observed=(confirmed_state, confirmed_invocation, confirmed_history),
            expected=(persisted, invocation, history),
            message="Replay durable authority changed while the assessment was recorded",
        )
        self._require_assessment_binding(
            verified=verified,
            persisted=confirmed_state,
            invocation=confirmed_invocation,
        )
        view = _project_view(
            verified=verified,
            persisted=confirmed_state,
            history=confirmed_history,
        )
        self._put_index(
            view=view,
            persisted=confirmed_state,
            invocation=confirmed_invocation,
            history=confirmed_history,
        )
        return view

    def inspect(self, *, run_id: UUID) -> WorkbenchReplayAssessmentView:
        """Re-read and verify an indexed assessment after any process restart."""

        if type(run_id) is not UUID:
            raise WorkbenchReplayAssessmentInputError("run_id must be a UUID")
        indexed = self._read_index(run_id)
        persisted, invocation, history = self._read_terminal_authority(run_id)
        if (
            indexed.invocation_sha256 != _model_digest(invocation)
            or indexed.run_state_sha256 != _model_digest(persisted)
            or indexed.event_page_sha256 != _event_page_digest(history)
        ):
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay authority identity differs from its immutable index"
            )
        indexed_view = indexed.view
        assessment = ArtifactRef(
            name=_ASSESSMENT_NAME,
            uri=f"artifact://sha256/{indexed_view.assessment_sha256}",
            media_type=_ASSESSMENT_MEDIA_TYPE,
            sha256=indexed_view.assessment_sha256,
            bytes=int(indexed_view.assessment_bytes),
            schema_version=_ASSESSMENT_SCHEMA,
        )
        verified = self._verify_assessment(assessment)
        self._require_assessment_binding(
            verified=verified,
            persisted=persisted,
            invocation=invocation,
        )
        current = _project_view(verified=verified, persisted=persisted, history=history)
        if current != indexed_view:
            raise WorkbenchReplayAssessmentAuthorityError(
                "Indexed replay assessment differs from its durable authority"
            )
        return current

    def _require_configured_acquisition(
        self,
        acquisition: System2ReplayEvidenceAcquisitionResult,
    ) -> None:
        try:
            journal_path = acquisition.journal_path.expanduser().resolve(strict=True)
            artifact_root = acquisition.artifact_root.expanduser().resolve(strict=True)
        except (AttributeError, OSError, RuntimeError) as error:
            raise WorkbenchReplayAssessmentInputError(
                "acquisition storage locators are invalid"
            ) from error
        if journal_path != self._journal_path or artifact_root != self._artifact_root:
            raise WorkbenchReplayAssessmentInputError(
                "acquisition belongs to a different Workbench authority"
            )

    def _verify_assessment(
        self,
        assessment: ArtifactRef,
    ) -> VerifiedSystem2ReplaySnapshotAssessment:
        try:
            return System2ReplaySnapshotAssessmentVerifier(
                artifact_store=self._artifact_store,
                scratch_parent=self._scratch_parent,
            ).verify(assessment=assessment)
        except System2ReplaySnapshotAssessmentError as error:
            if error.reason in {"assessment_invalid", "assessment_mismatch"}:
                raise WorkbenchReplayAssessmentAuthorityError(
                    "Replay snapshot assessment failed authority checks"
                ) from error
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay snapshot assessment could not be recomputed"
            ) from error

    def _read_terminal_authority(
        self,
        run_id: UUID,
    ) -> tuple[RunState, Invocation, EventPage]:
        try:
            store = SQLiteRunStore(
                self._journal_path,
                busy_timeout_ms=self._busy_timeout_ms,
            )
            invocation = store.read_invocation(run_id)
            persisted = store.read_run_state(run_id)
            history = SQLiteEventJournal(
                self._journal_path,
                busy_timeout_ms=self._busy_timeout_ms,
            ).read(
                after_event_id=0,
                run_id=run_id,
                limit=_MAX_EVENT_COUNT,
            )
        except (EventJournalCorruptionError, RunStoreCorruptionError) as error:
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay run authority failed integrity checks"
            ) from error
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as error:
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay run authority is unavailable"
            ) from error
        if persisted is None or invocation is None:
            raise WorkbenchReplayAssessmentAuthorityError(
                "Indexed replay assessment has incomplete run authority"
            )
        _require_terminal_history(persisted=persisted, invocation=invocation, history=history)
        return persisted, invocation, history

    def _require_assessment_binding(
        self,
        *,
        verified: VerifiedSystem2ReplaySnapshotAssessment,
        persisted: RunState,
        invocation: Invocation,
    ) -> None:
        record = verified.record
        if (
            record.replay_run_id != persisted.run_id
            or record.replay_invocation_digest != invocation.invocation_digest
            or record.replay_skill_ref != f"{invocation.skill_id}@{invocation.skill_version}"
            or record.max_attempts != invocation.max_attempts
            or record.replay_started_at != persisted.started_at
            or record.replay_ended_at != persisted.ended_at
            or record.replay_input.model_dump(mode="json") != invocation.effective_parameters
            or record.dependencies != invocation.dependencies
        ):
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay assessment metadata differs from the run authority"
            )
        typed_output = _strict_terminal_output(persisted)
        output_refs = (typed_output.runtime_evidence, *typed_output.replay_artifacts)
        snapshots = tuple(
            ref for ref in typed_output.replay_artifacts if ref.name == _RUNTIME_ASSET_SNAPSHOT_NAME
        )
        if (
            record.runtime_evidence != typed_output.runtime_evidence
            or len(snapshots) != 1
            or snapshots[0] != record.runtime_asset_snapshot_manifest
            or any(ref not in persisted.artifacts for ref in output_refs)
        ):
            raise WorkbenchReplayAssessmentAuthorityError(
                "Replay assessment artifacts differ from terminal replay output"
            )

    def _put_index(
        self,
        *,
        view: WorkbenchReplayAssessmentView,
        persisted: RunState,
        invocation: Invocation,
        history: EventPage,
    ) -> None:
        index = _WorkbenchReplayAssessmentIndex(
            view=view,
            invocation_sha256=_model_digest(invocation),
            run_state_sha256=_model_digest(persisted),
            event_page_sha256=_event_page_digest(history),
        )
        payload = _canonical_index_bytes(index)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT payload_json, payload_sha256
                    FROM workbench_replay_snapshot_assessments
                    WHERE run_id = ?
                    """,
                    (str(view.run_id),),
                ).fetchone()
                if row is not None:
                    existing = _decode_index_row(row, expected_run_id=view.run_id)
                    if existing != index or row["payload_json"] != payload:
                        raise WorkbenchReplayAssessmentAuthorityError(
                            "Run already has a different replay assessment index"
                        )
                    return
                connection.execute(
                    """
                    INSERT INTO workbench_replay_snapshot_assessments (
                        run_id, payload_json, payload_sha256
                    ) VALUES (?, ?, ?)
                    """,
                    (str(view.run_id), payload, digest),
                )
        except WorkbenchReplayAssessmentAuthorityError:
            raise
        except sqlite3.Error as error:
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay assessment index is unavailable"
            ) from error

    def _read_index(self, run_id: UUID) -> _WorkbenchReplayAssessmentIndex:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json, payload_sha256
                    FROM workbench_replay_snapshot_assessments
                    WHERE run_id = ?
                    """,
                    (str(run_id),),
                ).fetchone()
        except sqlite3.Error as error:
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay assessment index is unavailable"
            ) from error
        if row is None:
            raise WorkbenchReplayAssessmentNotFoundError("Replay snapshot assessment was not found")
        return _decode_index_row(row, expected_run_id=run_id)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._journal_path,
            timeout=self._busy_timeout_ms / 1_000,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workbench_replay_snapshot_assessments (
                        run_id TEXT PRIMARY KEY,
                        payload_json BLOB NOT NULL,
                        payload_sha256 TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as error:
            raise WorkbenchReplayAssessmentUnavailableError(
                "Replay assessment index is unavailable"
            ) from error


def _require_exact_authority(
    *,
    observed: tuple[RunState, Invocation, EventPage],
    expected: tuple[RunState, Invocation, EventPage],
    message: str,
) -> None:
    if observed != expected:
        raise WorkbenchReplayAssessmentAuthorityError(message)


def _require_terminal_history(
    *,
    persisted: RunState,
    invocation: Invocation,
    history: EventPage,
) -> None:
    if (
        persisted.skill_id != _REPLAY_SKILL_ID
        or persisted.skill_version != _REPLAY_SKILL_VERSION
        or persisted.status is not RunStatus.SUCCEEDED
        or persisted.attempt < 1
        or persisted.invocation_digest != invocation.invocation_digest
        or persisted.run_id != invocation.run_id
        or persisted.max_attempts != invocation.max_attempts
        or history.has_more
        or not history.events
        or len(history.events) != len(persisted.events)
        or history.last_event_id != history.events[-1].event_id
    ):
        raise WorkbenchReplayAssessmentAuthorityError(
            "Replay run authority is not one complete successful terminal history"
        )
    if any(
        stored.envelope.run_id != persisted.run_id
        or stored.envelope.skill_id != persisted.skill_id
        or stored.envelope.skill_version != persisted.skill_version
        or stored.envelope.event != expected
        for stored, expected in zip(history.events, persisted.events, strict=True)
    ):
        raise WorkbenchReplayAssessmentAuthorityError(
            "Replay event history differs from terminal RunState"
        )


def _strict_terminal_output(persisted: RunState) -> Text2EnvReplayOutput:
    try:
        output_payload = json.dumps(
            persisted.output,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return Text2EnvReplayOutput.model_validate_json(
            output_payload,
            strict=True,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise WorkbenchReplayAssessmentAuthorityError(
            "Replay terminal output is not a strict typed replay output"
        ) from error


def _project_view(
    *,
    verified: VerifiedSystem2ReplaySnapshotAssessment,
    persisted: RunState,
    history: EventPage,
) -> WorkbenchReplayAssessmentView:
    record = verified.record
    return WorkbenchReplayAssessmentView(
        run_id=persisted.run_id,
        invocation_digest=record.replay_invocation_digest,
        run_status=persisted.status,
        terminal_event_count=len(history.events),
        terminal_last_event_id=str(history.last_event_id),
        assessment_sha256=verified.assessment.sha256,
        assessment_bytes=str(verified.assessment.bytes),
        validation_report_sha256=record.validation_report.sha256,
        validation_report_bytes=str(record.validation_report.bytes),
        validation_status=record.validation_status,
        fail_count=record.fail_count,
        not_run_count=record.not_run_count,
    )


def _canonical_index_bytes(index: _WorkbenchReplayAssessmentIndex) -> bytes:
    return _canonical_json_bytes(index.model_dump(mode="json", warnings="error"))


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


def _model_digest(value: Invocation | RunState) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(value.model_dump(mode="json", warnings="error"))
    ).hexdigest()


def _event_page_digest(history: EventPage) -> str:
    projection = {
        "events": [
            {
                "event_id": stored.event_id,
                "run_id": str(stored.envelope.run_id),
                "skill_id": stored.envelope.skill_id,
                "skill_version": stored.envelope.skill_version,
                "event": stored.envelope.event.model_dump(mode="json", warnings="error"),
            }
            for stored in history.events
        ],
        "last_event_id": history.last_event_id,
        "has_more": history.has_more,
    }
    return hashlib.sha256(_canonical_json_bytes(projection)).hexdigest()


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _decode_index_row(
    row: sqlite3.Row,
    *,
    expected_run_id: UUID,
) -> _WorkbenchReplayAssessmentIndex:
    try:
        payload = row["payload_json"]
        if (
            type(payload) is not bytes
            or hashlib.sha256(payload).hexdigest() != row["payload_sha256"]
        ):
            raise ValueError("index payload digest mismatch")
        index = _WorkbenchReplayAssessmentIndex.model_validate_json(payload, strict=True)
        if index.view.run_id != expected_run_id or payload != _canonical_index_bytes(index):
            raise ValueError("index payload is not canonical for the requested run")
        return index
    except (KeyError, TypeError, UnicodeError, ValidationError, ValueError) as error:
        raise WorkbenchReplayAssessmentAuthorityError(
            "Replay assessment index failed integrity checks"
        ) from error


__all__ = [
    "WorkbenchReplayAssessment",
    "WorkbenchReplayAssessmentAuthorityError",
    "WorkbenchReplayAssessmentInputError",
    "WorkbenchReplayAssessmentNotFoundError",
    "WorkbenchReplayAssessmentUnavailableError",
    "WorkbenchReplayAssessmentView",
]
