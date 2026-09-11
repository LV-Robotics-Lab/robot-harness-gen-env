"""Durable SQLite adapter for Golden Workflow aggregate snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from .schemas.base import CanonicalSkillRef, Sha256
from .schemas.golden_control import ControlCommitSnapshot
from .schemas.golden_control_v2 import ControlCommitSnapshotV2
from .schemas.resource_access import ResourceAccessReceipt, resource_access_request_sha256
from .schemas.workflow import IdempotencyKey, OperationSnapshot, PrincipalId, RunSnapshot

_CURRENT_COLUMNS = (
    (0, "workflow_run_id", "TEXT", 1, None, 1, 0),
    (1, "principal_id", "TEXT", 1, None, 0, 0),
    (2, "idempotency_key", "TEXT", 1, None, 0, 0),
    (3, "request_sha256", "TEXT", 1, None, 0, 0),
    (4, "start_request_sha256", "TEXT", 1, None, 0, 0),
    (5, "snapshot_json", "BLOB", 1, None, 0, 0),
    (6, "snapshot_sha256", "TEXT", 1, None, 0, 0),
)
_LEGACY_COLUMNS = (
    (0, "principal_id", "TEXT", 1, None, 1, 0),
    (1, "idempotency_key", "TEXT", 1, None, 2, 0),
    (2, "request_sha256", "TEXT", 1, None, 0, 0),
    (3, "snapshot_json", "BLOB", 1, None, 0, 0),
)
_CURRENT_INDEXES = (
    (
        "pk",
        1,
        0,
        (
            (0, 0, "workflow_run_id", 0, "BINARY", 1),
            (1, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "principal_id", 0, "BINARY", 1),
            (1, 2, "idempotency_key", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 4, "start_request_sha256", 0, "BINARY", 1),
            (1, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_LEGACY_INDEXES = (
    (
        "pk",
        1,
        0,
        (
            (0, 0, "principal_id", 0, "BINARY", 1),
            (1, 1, "idempotency_key", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_OPERATION_COLUMNS = (
    (0, "operation_id", "TEXT", 1, None, 1, 0),
    (1, "workflow_run_id", "TEXT", 1, None, 0, 0),
    (2, "principal_id", "TEXT", 1, None, 0, 0),
    (3, "skill_ref", "TEXT", 1, None, 0, 0),
    (4, "idempotency_key", "TEXT", 1, None, 0, 0),
    (5, "command_sha256", "TEXT", 1, None, 0, 0),
    (6, "base_revision", "INTEGER", 1, None, 0, 0),
    (7, "operation_json", "BLOB", 1, None, 0, 0),
    (8, "operation_sha256", "TEXT", 1, None, 0, 0),
)
_OPERATION_INDEXES = (
    (
        "pk",
        1,
        0,
        ((0, 0, "operation_id", 0, "BINARY", 1), (1, -1, None, 0, "BINARY", 0)),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 3, "skill_ref", 0, "BINARY", 1),
            (2, 4, "idempotency_key", 0, "BINARY", 1),
            (3, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 6, "base_revision", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_TURN_COLUMNS = (
    (0, "entry_id", "TEXT", 1, None, 1, 0),
    (1, "workflow_run_id", "TEXT", 1, None, 0, 0),
    (2, "entry_kind", "TEXT", 1, None, 0, 0),
    (3, "action_ref", "TEXT", 1, None, 0, 0),
    (4, "idempotency_key", "TEXT", 1, None, 0, 0),
    (5, "request_sha256", "TEXT", 1, None, 0, 0),
    (6, "base_revision", "INTEGER", 1, None, 0, 0),
)
_TURN_INDEXES = (
    (
        "pk",
        1,
        0,
        ((0, 0, "entry_id", 0, "BINARY", 1), (1, -1, None, 0, "BINARY", 0)),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 2, "entry_kind", 0, "BINARY", 1),
            (2, 3, "action_ref", 0, "BINARY", 1),
            (3, 4, "idempotency_key", 0, "BINARY", 1),
            (4, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 6, "base_revision", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_CONTROL_COLUMNS = (
    (0, "control_id", "TEXT", 1, None, 1, 0),
    (1, "workflow_run_id", "TEXT", 1, None, 0, 0),
    (2, "principal_id", "TEXT", 1, None, 0, 0),
    (3, "control_kind", "TEXT", 1, None, 0, 0),
    (4, "idempotency_key", "TEXT", 1, None, 0, 0),
    (5, "command_sha256", "TEXT", 1, None, 0, 0),
    (6, "base_revision", "INTEGER", 1, None, 0, 0),
    (7, "control_json", "BLOB", 1, None, 0, 0),
    (8, "control_sha256", "TEXT", 1, None, 0, 0),
)
_CONTROL_INDEXES = (
    (
        "pk",
        1,
        0,
        ((0, 0, "control_id", 0, "BINARY", 1), (1, -1, None, 0, "BINARY", 0)),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 3, "control_kind", 0, "BINARY", 1),
            (2, 4, "idempotency_key", 0, "BINARY", 1),
            (3, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 6, "base_revision", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_RESOURCE_ACCESS_COLUMNS = (
    (0, "access_id", "TEXT", 1, None, 1, 0),
    (1, "workflow_run_id", "TEXT", 1, None, 0, 0),
    (2, "principal_id", "TEXT", 1, None, 0, 0),
    (3, "access_identity_sha256", "TEXT", 1, None, 0, 0),
    (4, "observed_snapshot_sha256", "TEXT", 1, None, 0, 0),
    (5, "resource_sha256", "TEXT", 1, None, 0, 0),
    (6, "receipt_json", "BLOB", 1, None, 0, 0),
    (7, "receipt_payload_sha256", "TEXT", 1, None, 0, 0),
)
_RESOURCE_ACCESS_INDEXES = (
    (
        "pk",
        1,
        0,
        ((0, 0, "access_id", 0, "BINARY", 1), (1, -1, None, 0, "BINARY", 0)),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 3, "access_identity_sha256", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
    (
        "u",
        1,
        0,
        (
            (0, 1, "workflow_run_id", 0, "BINARY", 1),
            (1, 7, "receipt_payload_sha256", 0, "BINARY", 1),
            (2, -1, None, 0, "BINARY", 0),
        ),
    ),
)
_SQL_QUOTED_OR_COMMENT = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]|"
    r"--[^\r\n]*|/\*.*?\*/",
    re.DOTALL,
)
_TABLE_CONFLICT_POLICY = re.compile(r"\bON\s+CONFLICT\b", re.IGNORECASE)


class _LegacyWorkflowStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    principal_id: PrincipalId
    idempotency_key: IdempotencyKey
    request_sha256: Sha256
    snapshot_json: bytes


class _CurrentWorkflowStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_run_id: str
    principal_id: PrincipalId
    idempotency_key: IdempotencyKey
    request_sha256: Sha256
    start_request_sha256: Sha256
    snapshot_json: object
    snapshot_sha256: object


class _CurrentOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str
    workflow_run_id: str
    principal_id: PrincipalId
    skill_ref: CanonicalSkillRef
    idempotency_key: IdempotencyKey
    command_sha256: Sha256
    base_revision: int
    operation_json: object
    operation_sha256: object


class _CurrentTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entry_id: str
    workflow_run_id: str
    entry_kind: Literal["skill_operation", "control_record"]
    action_ref: str
    idempotency_key: IdempotencyKey
    request_sha256: Sha256
    base_revision: int


class _CurrentControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    control_id: str
    workflow_run_id: str
    principal_id: PrincipalId
    control_kind: Literal["advisory", "prompt_revision"]
    idempotency_key: IdempotencyKey
    command_sha256: Sha256
    base_revision: int
    control_json: object
    control_sha256: object


class _CurrentResourceAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    access_id: str
    workflow_run_id: str
    principal_id: PrincipalId
    access_identity_sha256: Sha256
    observed_snapshot_sha256: Sha256
    resource_sha256: Sha256
    receipt_json: object
    receipt_payload_sha256: object


@dataclass(frozen=True, slots=True)
class _StoredStart:
    snapshot: RunSnapshot
    principal_id: str
    idempotency_key: str
    request_sha256: str
    start_request_sha256: str


@dataclass(frozen=True, slots=True)
class _StoredOperation:
    operation: OperationSnapshot
    command_sha256: str


@dataclass(frozen=True, slots=True)
class StoredControlRecord:
    """One terminal workflow control record recovered from durable authority."""

    control: ControlCommitSnapshot
    command_sha256: str


@dataclass(frozen=True, slots=True)
class StoredWorkflowEntry:
    """One ordered Skill or control entry in the shared workflow turn journal."""

    entry_id: UUID
    workflow_run_id: UUID
    entry_kind: Literal["skill_operation", "control_record"]
    action_ref: str
    idempotency_key: str
    request_sha256: str
    base_revision: int
    operation: OperationSnapshot | None
    control: ControlCommitSnapshot | None


@dataclass(frozen=True, slots=True)
class StoredResourceAccess:
    """One append-only resource delivery receipt outside workflow turns."""

    receipt: ResourceAccessReceipt
    access_identity_sha256: str
    receipt_payload_sha256: str


class GoldenRunConflictError(RuntimeError):
    """A caller reused an immutable workflow identity for different input."""

    code = "HARN_IDEMPOTENCY_CONFLICT"


class GoldenRunCorruptionError(RuntimeError):
    """Persisted workflow authority or its immutable CAS closure is corrupt."""

    code = "HARN_PERSISTENCE_CONFLICT"


class GoldenRunHeadConflictError(GoldenRunConflictError):
    """The caller's observed workflow head is no longer current."""

    code = "HARN_WORKFLOW_HEAD_CONFLICT"


class SQLiteGoldenRunStore:
    """Persist revision-zero Golden Workflow starts in one selected database."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize()

    def get_or_create_start(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        request_sha256: str,
        start_request_sha256: str,
        factory: Callable[[], RunSnapshot],
    ) -> _StoredStart:
        """Atomically recover an idempotent start or persist a new aggregate head."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT workflow_run_id, principal_id, idempotency_key,
                       request_sha256, start_request_sha256, snapshot_json,
                       snapshot_sha256
                FROM golden_workflow_starts
                WHERE principal_id = ? AND idempotency_key = ?
                """,
                (principal_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                stored = _decode_start(existing)
                if stored.start_request_sha256 != start_request_sha256:
                    raise GoldenRunConflictError(
                        "idempotency key is already bound to a different request"
                    )
                if stored.request_sha256 != request_sha256:
                    raise GoldenRunCorruptionError(
                        "stored workflow has inconsistent logical request identity"
                    )
                return stored

            displaced = connection.execute(
                """
                SELECT workflow_run_id, principal_id, idempotency_key,
                       request_sha256, start_request_sha256, snapshot_json,
                       snapshot_sha256
                FROM golden_workflow_starts
                WHERE start_request_sha256 = ?
                """,
                (start_request_sha256,),
            ).fetchone()
            if displaced is not None:
                _decode_start(displaced)
                raise GoldenRunCorruptionError(
                    "stored workflow has inconsistent idempotency metadata"
                )

            snapshot = factory()
            if (
                snapshot.principal_id != principal_id
                or snapshot.start_request_ref.sha256 != start_request_sha256
            ):
                raise GoldenRunCorruptionError(
                    "new workflow start has inconsistent authority metadata"
                )
            payload = _canonical_model_bytes(snapshot)
            try:
                connection.execute(
                    """
                    INSERT OR ABORT INTO golden_workflow_starts (
                        workflow_run_id, principal_id, idempotency_key,
                        request_sha256, start_request_sha256, snapshot_json,
                        snapshot_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot.workflow_run_id),
                        principal_id,
                        idempotency_key,
                        request_sha256,
                        start_request_sha256,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise GoldenRunConflictError(
                    "workflow identity is already bound to another aggregate"
                ) from error
            persisted = connection.execute(
                """
                SELECT workflow_run_id, principal_id, idempotency_key,
                       request_sha256, start_request_sha256, snapshot_json,
                       snapshot_sha256
                FROM golden_workflow_starts
                WHERE workflow_run_id = ?
                """,
                (str(snapshot.workflow_run_id),),
            ).fetchone()
            if persisted is None:
                raise GoldenRunCorruptionError(
                    "new workflow start disappeared from durable authority"
                )
            stored = _decode_start(persisted)
            if stored.request_sha256 != request_sha256:
                raise GoldenRunCorruptionError(
                    "new workflow start has inconsistent logical request identity"
                )
            if (
                stored.snapshot != snapshot
                or stored.principal_id != principal_id
                or stored.idempotency_key != idempotency_key
                or stored.start_request_sha256 != start_request_sha256
            ):
                raise GoldenRunCorruptionError(
                    "new workflow start has inconsistent authority metadata"
                )
            return stored

    def read_snapshot(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
    ) -> _StoredStart | None:
        """Read one revision-zero start, hiding existence from other principals."""

        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT workflow_run_id, principal_id, idempotency_key,
                       request_sha256, start_request_sha256, snapshot_json,
                       snapshot_sha256
                FROM golden_workflow_starts
                WHERE principal_id = ? AND workflow_run_id = ?
                """,
                (principal_id, str(workflow_run_id)),
            ).fetchone()
        return None if row is None else _decode_start(row)

    def read_operation(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
        skill_ref: str,
        idempotency_key: str,
    ) -> _StoredOperation | None:
        """Read one operation idempotency slot without disclosing another principal."""

        with self._connection() as connection:
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            row = connection.execute(
                """
                SELECT operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
                       command_sha256, base_revision, operation_json, operation_sha256
                FROM golden_workflow_operations
                WHERE workflow_run_id = ? AND skill_ref = ? AND idempotency_key = ?
                """,
                (str(workflow_run_id), skill_ref, idempotency_key),
            ).fetchone()
            if row is None:
                return None
            stored = _decode_operation(row)
            if stored.operation.principal_id != principal_id:
                raise GoldenRunCorruptionError("Skill operation is outside its workflow authority")
            _select_and_decode_entry(connection, stored.operation.operation_id)
            return stored

    def reserve_operation(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
        skill_ref: str,
        idempotency_key: str,
        command_sha256: str,
        expected_workflow_revision: int,
        expected_state_sha256: str,
        expected_receipt_head_sha256: str,
        operation: OperationSnapshot,
    ) -> _StoredOperation | None:
        """Atomically reserve one running operation against the exact observed head."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            current = start.snapshot
            row = connection.execute(
                """
                SELECT operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
                       command_sha256, base_revision, operation_json, operation_sha256
                FROM golden_workflow_operations
                WHERE workflow_run_id = ? AND skill_ref = ? AND idempotency_key = ?
                """,
                (str(workflow_run_id), skill_ref, idempotency_key),
            ).fetchone()
            if row is not None:
                existing = _decode_operation(row)
                if existing.operation.principal_id != principal_id:
                    raise GoldenRunCorruptionError(
                        "Skill operation is outside its workflow authority"
                    )
                _select_and_decode_entry(connection, existing.operation.operation_id)
                if existing.command_sha256 != command_sha256:
                    raise GoldenRunConflictError(
                        "operation idempotency key is already bound to a different command"
                    )
                return existing
            if (
                current.revision != expected_workflow_revision
                or current.state_sha256 != expected_state_sha256
                or current.receipt_head.sha256 != expected_receipt_head_sha256
                or current.active_operation is not None
            ):
                raise GoldenRunHeadConflictError(
                    "workflow head changed before the operation could be reserved"
                )
            if (
                operation.workflow_run_id != workflow_run_id
                or operation.principal_id != principal_id
                or operation.skill_ref != skill_ref
                or operation.idempotency_key != idempotency_key
                or operation.command.sha256 != command_sha256
                or operation.base_revision != expected_workflow_revision
                or operation.status.value != "running"
                or operation.started_at < current.updated_at
            ):
                raise GoldenRunCorruptionError("new operation has inconsistent authority metadata")
            payload = _canonical_model_bytes(operation)
            active = RunSnapshot.model_validate(
                current.model_copy(
                    update={
                        "active_operation": operation.operation_id,
                        "updated_at": operation.started_at,
                    }
                ).model_dump(mode="python")
            )
            try:
                connection.execute(
                    """
                    INSERT OR ABORT INTO golden_workflow_operations (
                        operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
                        command_sha256, base_revision, operation_json, operation_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(operation.operation_id),
                        str(workflow_run_id),
                        principal_id,
                        skill_ref,
                        idempotency_key,
                        command_sha256,
                        operation.base_revision,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                    ),
                )
                _insert_turn(
                    connection,
                    entry_id=operation.operation_id,
                    workflow_run_id=workflow_run_id,
                    entry_kind="skill_operation",
                    action_ref=skill_ref,
                    idempotency_key=idempotency_key,
                    request_sha256=command_sha256,
                    base_revision=operation.base_revision,
                )
            except sqlite3.IntegrityError as error:
                raise GoldenRunConflictError(
                    "operation identity is already bound to another command"
                ) from error
            _update_snapshot(connection, active)
            stored = _select_operation(connection, operation.operation_id)
            persisted_start = _select_start(connection, workflow_run_id)
            if stored is None or persisted_start is None:
                raise GoldenRunCorruptionError("new operation disappeared from durable authority")
            decoded = _decode_operation(stored)
            if decoded.operation != operation or persisted_start.snapshot != active:
                raise GoldenRunCorruptionError(
                    "new operation has inconsistent durable authority metadata"
                )
            return decoded

    def commit_operation(
        self,
        *,
        principal_id: str,
        running: OperationSnapshot,
        terminal: OperationSnapshot,
        snapshot: RunSnapshot,
    ) -> _StoredOperation | None:
        """Atomically commit terminal operation metadata and the resulting workflow head."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_all_current_layouts(connection)
            start = _select_start(connection, running.workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            row = _select_operation(connection, running.operation_id)
            if row is None:
                raise GoldenRunCorruptionError("reserved operation is absent")
            persisted = _decode_operation(row)
            _select_and_decode_entry(connection, running.operation_id)
            if persisted.operation.status.value != "running":
                if persisted.operation != terminal or start.snapshot != snapshot:
                    raise GoldenRunCorruptionError(
                        "terminal operation does not equal the committed workflow head"
                    )
                return persisted
            if persisted.operation != running:
                raise GoldenRunCorruptionError("workflow operation changed before terminal commit")
            current = start.snapshot
            if (
                current.active_operation != running.operation_id
                or current.revision != running.base_revision
                or current.updated_at != running.started_at
            ):
                raise GoldenRunHeadConflictError(
                    "workflow head changed before the operation could commit"
                )
            immutable = (
                "operation_id",
                "workflow_run_id",
                "principal_id",
                "idempotency_key",
                "command",
                "qualified_execution",
                "skill_ref",
                "base_revision",
                "child_run_id",
                "started_at",
            )
            if any(getattr(running, field) != getattr(terminal, field) for field in immutable):
                raise GoldenRunCorruptionError(
                    "terminal operation changed immutable authority metadata"
                )
            if (
                terminal.status.value == "running"
                or terminal.committed_revision != running.base_revision + 1
                or terminal.tool_result is None
                or terminal.ended_at is None
                or snapshot.workflow_run_id != running.workflow_run_id
                or snapshot.principal_id != principal_id
                or snapshot.revision != terminal.committed_revision
                or snapshot.turn_seq != current.turn_seq + 1
                or snapshot.active_operation is not None
                or snapshot.child_runs != (*current.child_runs, running.child_run_id)
                or snapshot.updated_at != terminal.ended_at
            ):
                raise GoldenRunCorruptionError(
                    "terminal workflow head is not the exact operation transition"
                )
            immutable_snapshot = (
                "workflow_run_id",
                "principal_id",
                "workspace",
                "requested_profile",
                "status",
                "start_request_ref",
                "registry_snapshot",
                "started_at",
            )
            if any(
                getattr(current, field) != getattr(snapshot, field) for field in immutable_snapshot
            ):
                raise GoldenRunCorruptionError(
                    "terminal workflow head changed immutable aggregate metadata"
                )
            if terminal.status.value != "succeeded" and (
                snapshot.state_sha256 != current.state_sha256
                or snapshot.state_ref != current.state_ref
            ):
                raise GoldenRunCorruptionError(
                    "blocked or failed operation cannot change trusted state"
                )
            operation_payload = _canonical_model_bytes(terminal)
            connection.execute(
                """
                UPDATE OR ABORT golden_workflow_operations
                SET operation_json = ?, operation_sha256 = ?
                WHERE operation_id = ?
                """,
                (
                    operation_payload,
                    hashlib.sha256(operation_payload).hexdigest(),
                    str(terminal.operation_id),
                ),
            )
            _update_snapshot(connection, snapshot)
            stored = _select_operation(connection, terminal.operation_id)
            persisted_start = _select_start(connection, terminal.workflow_run_id)
            if stored is None or persisted_start is None:
                raise GoldenRunCorruptionError(
                    "terminal operation disappeared from durable authority"
                )
            decoded = _decode_operation(stored)
            if decoded.operation != terminal or persisted_start.snapshot != snapshot:
                raise GoldenRunCorruptionError("terminal workflow head was not committed exactly")
            return decoded

    def read_operations(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
    ) -> tuple[_StoredOperation, ...]:
        """Read the ordered operation journal visible to one principal."""

        with self._connection() as connection:
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return ()
            rows = connection.execute(
                """
                SELECT operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
                       command_sha256, base_revision, operation_json, operation_sha256
                FROM golden_workflow_operations
                WHERE workflow_run_id = ?
                ORDER BY base_revision, operation_id
                """,
                (str(workflow_run_id),),
            ).fetchall()
            stored = tuple(_decode_operation(row) for row in rows)
            for operation in stored:
                _select_and_decode_entry(connection, operation.operation.operation_id)
            return stored

    def read_control_record(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
        control_kind: Literal["advisory", "prompt_revision"],
        idempotency_key: str,
    ) -> StoredControlRecord | None:
        """Read one terminal control idempotency slot for its owning principal."""

        with self._connection() as connection:
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            row = _select_control_by_identity(
                connection,
                workflow_run_id=workflow_run_id,
                control_kind=control_kind,
                idempotency_key=idempotency_key,
            )
            if row is None:
                return None
            stored = _decode_control(row)
            if stored.control.principal_id != principal_id:
                raise GoldenRunCorruptionError("control record is outside its workflow authority")
            _select_and_decode_entry(connection, stored.control.command_id)
            return stored

    def commit_control_record(
        self,
        *,
        principal_id: str,
        expected_workflow_revision: int,
        expected_state_sha256: str,
        expected_receipt_head_sha256: str,
        control: ControlCommitSnapshot,
        snapshot: RunSnapshot,
    ) -> StoredControlRecord | None:
        """Atomically append one terminal-only control and advance the shared head."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_all_current_layouts(connection)
            start = _select_start(connection, control.workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            existing_row = _select_control_by_identity(
                connection,
                workflow_run_id=control.workflow_run_id,
                control_kind=control.control_kind,
                idempotency_key=control.idempotency_key,
            )
            if existing_row is not None:
                existing = _decode_control(existing_row)
                if existing.command_sha256 != control.command.sha256:
                    raise GoldenRunConflictError(
                        "control idempotency key is already bound to a different command"
                    )
                if existing.control != control:
                    raise GoldenRunCorruptionError(
                        "control retry differs from its committed durable record"
                    )
                return existing

            current = start.snapshot
            if (
                current.revision != expected_workflow_revision
                or current.state_sha256 != expected_state_sha256
                or current.receipt_head.sha256 != expected_receipt_head_sha256
                or current.active_operation is not None
            ):
                raise GoldenRunHeadConflictError(
                    "workflow head changed before the control could commit"
                )
            if (
                control.principal_id != principal_id
                or control.base_revision != current.revision
                or control.committed_revision != current.revision + 1
                or control.base_turn_seq != current.turn_seq
                or control.committed_turn_seq != current.turn_seq + 1
                or control.predecessor_receipt != current.receipt_head
                or control.base_state_sha256 != current.state_sha256
                or control.base_state != current.state_ref
                or control.resulting_state_sha256 != current.state_sha256
                or control.resulting_state != current.state_ref
                or control.committed_at < current.updated_at
            ):
                raise GoldenRunCorruptionError(
                    "new control has inconsistent workflow authority metadata"
                )
            immutable_snapshot = (
                "workflow_run_id",
                "principal_id",
                "workspace",
                "requested_profile",
                "status",
                "state_sha256",
                "state_ref",
                "start_request_ref",
                "registry_snapshot",
                "active_operation",
                "child_runs",
                "started_at",
            )
            if (
                any(
                    getattr(current, field) != getattr(snapshot, field)
                    for field in immutable_snapshot
                )
                or snapshot.revision != control.committed_revision
                or snapshot.turn_seq != control.committed_turn_seq
                or snapshot.receipt_head != control.receipt_head
                or snapshot.updated_at != control.committed_at
            ):
                raise GoldenRunCorruptionError(
                    "terminal workflow head is not the exact control transition"
                )

            payload = _canonical_model_bytes(control)
            try:
                connection.execute(
                    """
                    INSERT OR ABORT INTO golden_workflow_control_records (
                        control_id, workflow_run_id, principal_id, control_kind,
                        idempotency_key, command_sha256, base_revision,
                        control_json, control_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(control.command_id),
                        str(control.workflow_run_id),
                        principal_id,
                        control.control_kind,
                        control.idempotency_key,
                        control.command.sha256,
                        control.base_revision,
                        payload,
                        hashlib.sha256(payload).hexdigest(),
                    ),
                )
                _insert_turn(
                    connection,
                    entry_id=control.command_id,
                    workflow_run_id=control.workflow_run_id,
                    entry_kind="control_record",
                    action_ref=control.control_kind,
                    idempotency_key=control.idempotency_key,
                    request_sha256=control.command.sha256,
                    base_revision=control.base_revision,
                )
            except sqlite3.IntegrityError as error:
                raise GoldenRunConflictError(
                    "control identity is already bound to another workflow transition"
                ) from error
            _update_snapshot(connection, snapshot)
            return StoredControlRecord(
                control=control,
                command_sha256=control.command.sha256,
            )

    def read_entries(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
    ) -> tuple[StoredWorkflowEntry, ...]:
        """Read the single ordered journal shared by Skill and control transitions."""

        with self._connection() as connection:
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return ()
            rows = connection.execute(
                """
                SELECT entry_id, workflow_run_id, entry_kind, action_ref,
                       idempotency_key, request_sha256, base_revision
                FROM golden_workflow_turns
                WHERE workflow_run_id = ?
                ORDER BY base_revision, entry_id
                """,
                (str(workflow_run_id),),
            ).fetchall()
            operation_count = connection.execute(
                "SELECT COUNT(*) FROM golden_workflow_operations WHERE workflow_run_id = ?",
                (str(workflow_run_id),),
            ).fetchone()[0]
            control_count = connection.execute(
                "SELECT COUNT(*) FROM golden_workflow_control_records WHERE workflow_run_id = ?",
                (str(workflow_run_id),),
            ).fetchone()[0]
            if len(rows) != operation_count + control_count:
                raise GoldenRunCorruptionError(
                    "workflow detail records and shared turn journal disagree"
                )
            entries = tuple(_decode_entry(connection, row) for row in rows)
            if any(
                (
                    entry.operation.principal_id
                    if entry.operation is not None
                    else entry.control.principal_id
                )
                != principal_id
                for entry in entries
            ):
                raise GoldenRunCorruptionError(
                    "workflow turn detail is outside its workflow authority"
                )
            return entries

    def read_resource_access(
        self,
        *,
        principal_id: str,
        workflow_run_id: UUID,
        access_identity_sha256: str | None = None,
        receipt_payload_sha256: str | None = None,
    ) -> StoredResourceAccess | None:
        """Read one delivery receipt by its stable request or receipt identity."""

        if (access_identity_sha256 is None) == (receipt_payload_sha256 is None):
            raise ValueError("exactly one resource access identity must be supplied")
        with self._connection() as connection:
            _require_all_current_layouts(connection)
            start = _select_start(connection, workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            row = _select_resource_access(
                connection,
                workflow_run_id=workflow_run_id,
                access_identity_sha256=access_identity_sha256,
                receipt_payload_sha256=receipt_payload_sha256,
            )
            if row is None:
                return None
            stored = _decode_resource_access(row)
            if stored.receipt.principal_id != principal_id:
                raise GoldenRunCorruptionError(
                    "resource access receipt is outside its workflow authority"
                )
            return stored

    def commit_resource_access(
        self,
        *,
        principal_id: str,
        access_identity_sha256: str,
        expected_snapshot_sha256: str,
        receipt: ResourceAccessReceipt,
    ) -> StoredResourceAccess | None:
        """Append one delivery receipt without advancing the workflow turn or head."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_all_current_layouts(connection)
            start = _select_start(connection, receipt.workflow_run_id)
            if start is None or start.snapshot.principal_id != principal_id:
                return None
            existing_row = _select_resource_access(
                connection,
                workflow_run_id=receipt.workflow_run_id,
                access_identity_sha256=access_identity_sha256,
                receipt_payload_sha256=None,
            )
            if existing_row is not None:
                return _decode_resource_access(existing_row)
            current_payload = _canonical_model_bytes(start.snapshot)
            if hashlib.sha256(current_payload).hexdigest() != expected_snapshot_sha256:
                raise GoldenRunHeadConflictError(
                    "workflow snapshot changed before resource delivery could be recorded"
                )
            if (
                receipt.principal_id != principal_id
                or receipt.observed_snapshot != start.snapshot
                or receipt.observed_snapshot_sha256 != expected_snapshot_sha256
            ):
                raise GoldenRunCorruptionError(
                    "resource access receipt differs from current workflow authority"
                )
            payload = _canonical_model_bytes(receipt)
            payload_sha256 = hashlib.sha256(payload).hexdigest()
            try:
                connection.execute(
                    """
                    INSERT OR ABORT INTO golden_workflow_resource_accesses (
                        access_id, workflow_run_id, principal_id,
                        access_identity_sha256, observed_snapshot_sha256,
                        resource_sha256, receipt_json, receipt_payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(receipt.access_id),
                        str(receipt.workflow_run_id),
                        principal_id,
                        access_identity_sha256,
                        expected_snapshot_sha256,
                        receipt.resource.sha256,
                        payload,
                        payload_sha256,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise GoldenRunConflictError(
                    "resource access identity is already bound to another delivery"
                ) from error
            return StoredResourceAccess(
                receipt=receipt,
                access_identity_sha256=access_identity_sha256,
                receipt_payload_sha256=payload_sha256,
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self._busy_timeout_ms / 1_000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        except sqlite3.DatabaseError as error:
            raise GoldenRunCorruptionError(
                f"golden workflow database is unavailable or corrupt: {error}"
            ) from error
        finally:
            if "connection" in locals():
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            columns = _read_column_layout(connection)
            if not columns:
                _create_current_table(connection)
                _require_table_layout(
                    connection,
                    columns=_CURRENT_COLUMNS,
                    indexes=_CURRENT_INDEXES,
                )
            elif frozenset(row[1] for row in columns) == frozenset(
                row[1] for row in _LEGACY_COLUMNS
            ):
                _require_table_layout(
                    connection,
                    columns=_LEGACY_COLUMNS,
                    indexes=_LEGACY_INDEXES,
                )
                _migrate_legacy_table(connection)
                _require_table_layout(
                    connection,
                    columns=_CURRENT_COLUMNS,
                    indexes=_CURRENT_INDEXES,
                )
            elif frozenset(row[1] for row in columns) == frozenset(
                row[1] for row in _CURRENT_COLUMNS
            ):
                _require_table_layout(
                    connection,
                    columns=_CURRENT_COLUMNS,
                    indexes=_CURRENT_INDEXES,
                )
            else:
                raise GoldenRunCorruptionError(
                    "golden workflow database has an unsupported table layout"
                )
            _initialize_operation_table(connection)
            _initialize_turn_tables(connection)
            _initialize_resource_access_table(connection)


def _decode_start(row: sqlite3.Row) -> _StoredStart:
    try:
        authority = _CurrentWorkflowStart.model_validate(dict(row))
    except ValidationError as error:
        raise GoldenRunCorruptionError(
            f"stored workflow has invalid authority metadata: {error}"
        ) from error
    workflow_run_id = authority.workflow_run_id
    payload = _snapshot_bytes(authority.snapshot_json, label=workflow_run_id)
    if type(authority.snapshot_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", authority.snapshot_sha256
    ):
        raise GoldenRunCorruptionError(
            f"stored workflow {workflow_run_id} has an invalid payload checksum"
        )
    if hashlib.sha256(payload).hexdigest() != authority.snapshot_sha256:
        raise GoldenRunCorruptionError(
            f"stored workflow {workflow_run_id} failed its payload checksum"
        )
    snapshot = _parse_canonical_snapshot(payload, label=str(workflow_run_id))

    if (
        str(snapshot.workflow_run_id) != workflow_run_id
        or snapshot.principal_id != authority.principal_id
    ):
        raise GoldenRunCorruptionError(
            f"stored workflow {workflow_run_id} has inconsistent identity metadata"
        )
    if authority.start_request_sha256 != snapshot.start_request_ref.sha256:
        raise GoldenRunCorruptionError(
            f"stored workflow {workflow_run_id} has inconsistent start request identity"
        )
    return _StoredStart(
        snapshot=snapshot,
        principal_id=authority.principal_id,
        idempotency_key=authority.idempotency_key,
        request_sha256=authority.request_sha256,
        start_request_sha256=authority.start_request_sha256,
    )


def _decode_operation(row: sqlite3.Row) -> _StoredOperation:
    try:
        authority = _CurrentOperation.model_validate(dict(row))
    except ValidationError as error:
        raise GoldenRunCorruptionError(
            f"stored operation has invalid authority metadata: {error}"
        ) from error
    label = authority.operation_id
    payload = _snapshot_bytes(authority.operation_json, label=label)
    if type(authority.operation_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", authority.operation_sha256
    ):
        raise GoldenRunCorruptionError(f"stored operation {label} has an invalid payload checksum")
    if hashlib.sha256(payload).hexdigest() != authority.operation_sha256:
        raise GoldenRunCorruptionError(f"stored operation {label} failed its payload checksum")
    try:
        operation = OperationSnapshot.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise GoldenRunCorruptionError(
            f"cannot reconstruct stored operation {label}: {error}"
        ) from error
    if payload != _canonical_model_bytes(operation):
        raise GoldenRunCorruptionError(f"stored operation {label} is not canonical JSON bytes")
    if (
        str(operation.operation_id) != authority.operation_id
        or str(operation.workflow_run_id) != authority.workflow_run_id
        or operation.principal_id != authority.principal_id
        or operation.skill_ref != authority.skill_ref
        or operation.idempotency_key != authority.idempotency_key
        or operation.command.sha256 != authority.command_sha256
        or operation.base_revision != authority.base_revision
    ):
        raise GoldenRunCorruptionError(
            f"stored operation {label} has inconsistent authority metadata"
        )
    return _StoredOperation(
        operation=operation,
        command_sha256=authority.command_sha256,
    )


def _decode_control(row: sqlite3.Row) -> StoredControlRecord:
    try:
        authority = _CurrentControl.model_validate(dict(row))
    except ValidationError as error:
        raise GoldenRunCorruptionError(
            f"stored control has invalid authority metadata: {error}"
        ) from error
    label = authority.control_id
    payload = _snapshot_bytes(authority.control_json, label=label)
    if type(authority.control_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", authority.control_sha256
    ):
        raise GoldenRunCorruptionError(f"stored control {label} has an invalid payload checksum")
    if hashlib.sha256(payload).hexdigest() != authority.control_sha256:
        raise GoldenRunCorruptionError(f"stored control {label} failed its payload checksum")
    try:
        model = (
            ControlCommitSnapshotV2
            if json.loads(payload).get("schema_version") == "harness.control_commit_snapshot.v2"
            else ControlCommitSnapshot
        )
        control = model.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise GoldenRunCorruptionError(
            f"cannot reconstruct stored control {label}: {error}"
        ) from error
    if payload != _canonical_model_bytes(control):
        raise GoldenRunCorruptionError(f"stored control {label} is not canonical JSON bytes")
    if (
        str(control.command_id) != authority.control_id
        or str(control.workflow_run_id) != authority.workflow_run_id
        or control.principal_id != authority.principal_id
        or control.control_kind != authority.control_kind
        or control.idempotency_key != authority.idempotency_key
        or control.command.sha256 != authority.command_sha256
        or control.base_revision != authority.base_revision
    ):
        raise GoldenRunCorruptionError(
            f"stored control {label} has inconsistent authority metadata"
        )
    return StoredControlRecord(control=control, command_sha256=authority.command_sha256)


def _decode_resource_access(row: sqlite3.Row) -> StoredResourceAccess:
    try:
        authority = _CurrentResourceAccess.model_validate(dict(row))
    except ValidationError as error:
        raise GoldenRunCorruptionError(
            f"stored resource access has invalid authority metadata: {error}"
        ) from error
    payload = _snapshot_bytes(authority.receipt_json, label=authority.access_id)
    if type(authority.receipt_payload_sha256) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", authority.receipt_payload_sha256
    ):
        raise GoldenRunCorruptionError("stored resource access has an invalid payload checksum")
    if hashlib.sha256(payload).hexdigest() != authority.receipt_payload_sha256:
        raise GoldenRunCorruptionError("stored resource access failed its payload checksum")
    try:
        receipt = ResourceAccessReceipt.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise GoldenRunCorruptionError(
            f"cannot reconstruct stored resource access {authority.access_id}: {error}"
        ) from error
    if payload != _canonical_model_bytes(receipt):
        raise GoldenRunCorruptionError("stored resource access is not canonical JSON bytes")
    if (
        str(receipt.access_id) != authority.access_id
        or str(receipt.workflow_run_id) != authority.workflow_run_id
        or receipt.principal_id != authority.principal_id
        or receipt.observed_snapshot_sha256 != authority.observed_snapshot_sha256
        or receipt.resource.sha256 != authority.resource_sha256
        or resource_access_request_sha256(
            principal_id=receipt.principal_id,
            workflow_run_id=receipt.workflow_run_id,
            resource_uri=receipt.resource_uri,
            resource=receipt.resource,
            observed_snapshot_sha256=receipt.observed_snapshot_sha256,
        )
        != authority.access_identity_sha256
    ):
        raise GoldenRunCorruptionError("stored resource access has inconsistent authority metadata")
    return StoredResourceAccess(
        receipt=receipt,
        access_identity_sha256=authority.access_identity_sha256,
        receipt_payload_sha256=authority.receipt_payload_sha256,
    )


def _decode_entry(connection: sqlite3.Connection, row: sqlite3.Row) -> StoredWorkflowEntry:
    try:
        authority = _CurrentTurn.model_validate(dict(row))
        entry_id = UUID(authority.entry_id)
        workflow_run_id = UUID(authority.workflow_run_id)
    except (ValidationError, TypeError, ValueError) as error:
        raise GoldenRunCorruptionError(
            f"stored workflow turn has invalid authority metadata: {error}"
        ) from error
    if authority.entry_kind == "skill_operation":
        operation_row = _select_operation(connection, entry_id)
        if operation_row is None:
            raise GoldenRunCorruptionError("workflow turn is missing its Skill operation")
        stored = _decode_operation(operation_row)
        operation = stored.operation
        if (
            operation.workflow_run_id != workflow_run_id
            or operation.skill_ref != authority.action_ref
            or operation.idempotency_key != authority.idempotency_key
            or stored.command_sha256 != authority.request_sha256
            or operation.base_revision != authority.base_revision
        ):
            raise GoldenRunCorruptionError("workflow turn differs from its Skill operation")
        return StoredWorkflowEntry(
            entry_id=entry_id,
            workflow_run_id=workflow_run_id,
            entry_kind=authority.entry_kind,
            action_ref=authority.action_ref,
            idempotency_key=authority.idempotency_key,
            request_sha256=authority.request_sha256,
            base_revision=authority.base_revision,
            operation=operation,
            control=None,
        )
    control_row = _select_control(connection, entry_id)
    if control_row is None:
        raise GoldenRunCorruptionError("workflow turn is missing its control record")
    stored_control = _decode_control(control_row)
    control = stored_control.control
    if (
        control.workflow_run_id != workflow_run_id
        or control.control_kind != authority.action_ref
        or control.idempotency_key != authority.idempotency_key
        or stored_control.command_sha256 != authority.request_sha256
        or control.base_revision != authority.base_revision
    ):
        raise GoldenRunCorruptionError("workflow turn differs from its control record")
    return StoredWorkflowEntry(
        entry_id=entry_id,
        workflow_run_id=workflow_run_id,
        entry_kind=authority.entry_kind,
        action_ref=authority.action_ref,
        idempotency_key=authority.idempotency_key,
        request_sha256=authority.request_sha256,
        base_revision=authority.base_revision,
        operation=None,
        control=control,
    )


def _snapshot_bytes(payload: object, *, label: str) -> bytes:
    if type(payload) is not bytes:
        raise GoldenRunCorruptionError(f"stored workflow {label} is not canonical JSON bytes")
    return payload


def _parse_canonical_snapshot(payload: bytes, *, label: str) -> RunSnapshot:
    try:
        snapshot = RunSnapshot.model_validate_json(payload)
    except (ValidationError, TypeError, ValueError) as error:
        raise GoldenRunCorruptionError(
            f"cannot reconstruct stored workflow {label}: {error}"
        ) from error
    if payload != _canonical_model_bytes(snapshot):
        raise GoldenRunCorruptionError(f"stored workflow {label} is not canonical JSON bytes")
    return snapshot


def _create_current_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_workflow_starts (
            workflow_run_id TEXT NOT NULL PRIMARY KEY,
            principal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            start_request_sha256 TEXT NOT NULL,
            snapshot_json BLOB NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            UNIQUE (principal_id, idempotency_key),
            UNIQUE (start_request_sha256)
        )
        """
    )


def _create_operation_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_workflow_operations (
            operation_id TEXT NOT NULL PRIMARY KEY,
            workflow_run_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            skill_ref TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            command_sha256 TEXT NOT NULL,
            base_revision INTEGER NOT NULL,
            operation_json BLOB NOT NULL,
            operation_sha256 TEXT NOT NULL,
            UNIQUE (workflow_run_id, skill_ref, idempotency_key),
            UNIQUE (workflow_run_id, base_revision)
        )
        """
    )


def _create_turn_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_workflow_turns (
            entry_id TEXT NOT NULL PRIMARY KEY,
            workflow_run_id TEXT NOT NULL,
            entry_kind TEXT NOT NULL,
            action_ref TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            base_revision INTEGER NOT NULL,
            UNIQUE (workflow_run_id, entry_kind, action_ref, idempotency_key),
            UNIQUE (workflow_run_id, base_revision)
        )
        """
    )


def _create_control_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_workflow_control_records (
            control_id TEXT NOT NULL PRIMARY KEY,
            workflow_run_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            control_kind TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            command_sha256 TEXT NOT NULL,
            base_revision INTEGER NOT NULL,
            control_json BLOB NOT NULL,
            control_sha256 TEXT NOT NULL,
            UNIQUE (workflow_run_id, control_kind, idempotency_key),
            UNIQUE (workflow_run_id, base_revision)
        )
        """
    )


def _create_resource_access_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE golden_workflow_resource_accesses (
            access_id TEXT NOT NULL PRIMARY KEY,
            workflow_run_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            access_identity_sha256 TEXT NOT NULL,
            observed_snapshot_sha256 TEXT NOT NULL,
            resource_sha256 TEXT NOT NULL,
            receipt_json BLOB NOT NULL,
            receipt_payload_sha256 TEXT NOT NULL,
            UNIQUE (workflow_run_id, access_identity_sha256),
            UNIQUE (workflow_run_id, receipt_payload_sha256)
        )
        """
    )


def _initialize_operation_table(connection: sqlite3.Connection) -> None:
    columns = _read_named_column_layout(connection, "golden_workflow_operations")
    if not columns:
        _create_operation_table(connection)
    _require_operation_table_layout(connection)


def _initialize_turn_tables(connection: sqlite3.Connection) -> None:
    turn_columns = _read_named_column_layout(connection, "golden_workflow_turns")
    control_columns = _read_named_column_layout(connection, "golden_workflow_control_records")
    if not turn_columns and not control_columns:
        _create_turn_table(connection)
        _create_control_table(connection)
        _backfill_operation_turns(connection)
    elif not turn_columns or not control_columns:
        raise GoldenRunCorruptionError(
            "golden workflow database has an unsupported shared turn layout"
        )
    _require_turn_table_layout(connection)
    _require_control_table_layout(connection)


def _initialize_resource_access_table(connection: sqlite3.Connection) -> None:
    columns = _read_named_column_layout(connection, "golden_workflow_resource_accesses")
    if not columns:
        _create_resource_access_table(connection)
    _require_resource_access_table_layout(connection)


def _backfill_operation_turns(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
               command_sha256, base_revision, operation_json, operation_sha256
        FROM golden_workflow_operations
        ORDER BY workflow_run_id, base_revision, operation_id
        """
    ).fetchall()
    for row in rows:
        stored = _decode_operation(row)
        operation = stored.operation
        start = _select_start(connection, operation.workflow_run_id)
        if start is None or start.snapshot.principal_id != operation.principal_id:
            raise GoldenRunCorruptionError(
                "cannot backfill an operation outside its workflow authority"
            )
        _insert_turn(
            connection,
            entry_id=operation.operation_id,
            workflow_run_id=operation.workflow_run_id,
            entry_kind="skill_operation",
            action_ref=operation.skill_ref,
            idempotency_key=operation.idempotency_key,
            request_sha256=stored.command_sha256,
            base_revision=operation.base_revision,
        )


def _insert_turn(
    connection: sqlite3.Connection,
    *,
    entry_id: UUID,
    workflow_run_id: UUID,
    entry_kind: Literal["skill_operation", "control_record"],
    action_ref: str,
    idempotency_key: str,
    request_sha256: str,
    base_revision: int,
) -> None:
    connection.execute(
        """
        INSERT OR ABORT INTO golden_workflow_turns (
            entry_id, workflow_run_id, entry_kind, action_ref,
            idempotency_key, request_sha256, base_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(entry_id),
            str(workflow_run_id),
            entry_kind,
            action_ref,
            idempotency_key,
            request_sha256,
            base_revision,
        ),
    )


def _select_start(
    connection: sqlite3.Connection,
    workflow_run_id: UUID,
) -> _StoredStart | None:
    row = connection.execute(
        """
        SELECT workflow_run_id, principal_id, idempotency_key,
               request_sha256, start_request_sha256, snapshot_json,
               snapshot_sha256
        FROM golden_workflow_starts
        WHERE workflow_run_id = ?
        """,
        (str(workflow_run_id),),
    ).fetchone()
    return None if row is None else _decode_start(row)


def _select_operation(
    connection: sqlite3.Connection,
    operation_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT operation_id, workflow_run_id, principal_id, skill_ref, idempotency_key,
               command_sha256, base_revision, operation_json, operation_sha256
        FROM golden_workflow_operations
        WHERE operation_id = ?
        """,
        (str(operation_id),),
    ).fetchone()


def _select_control(
    connection: sqlite3.Connection,
    control_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT control_id, workflow_run_id, principal_id, control_kind,
               idempotency_key, command_sha256, base_revision,
               control_json, control_sha256
        FROM golden_workflow_control_records
        WHERE control_id = ?
        """,
        (str(control_id),),
    ).fetchone()


def _select_control_by_identity(
    connection: sqlite3.Connection,
    *,
    workflow_run_id: UUID,
    control_kind: Literal["advisory", "prompt_revision"],
    idempotency_key: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT control_id, workflow_run_id, principal_id, control_kind,
               idempotency_key, command_sha256, base_revision,
               control_json, control_sha256
        FROM golden_workflow_control_records
        WHERE workflow_run_id = ? AND control_kind = ? AND idempotency_key = ?
        """,
        (str(workflow_run_id), control_kind, idempotency_key),
    ).fetchone()


def _select_resource_access(
    connection: sqlite3.Connection,
    *,
    workflow_run_id: UUID,
    access_identity_sha256: str | None,
    receipt_payload_sha256: str | None,
) -> sqlite3.Row | None:
    if access_identity_sha256 is not None:
        column = "access_identity_sha256"
        identity = access_identity_sha256
    else:
        assert receipt_payload_sha256 is not None
        column = "receipt_payload_sha256"
        identity = receipt_payload_sha256
    return connection.execute(
        f"""
        SELECT access_id, workflow_run_id, principal_id,
               access_identity_sha256, observed_snapshot_sha256,
               resource_sha256, receipt_json, receipt_payload_sha256
        FROM golden_workflow_resource_accesses
        WHERE workflow_run_id = ? AND {column} = ?
        """,
        (str(workflow_run_id), identity),
    ).fetchone()


def _select_and_decode_entry(
    connection: sqlite3.Connection,
    entry_id: UUID,
) -> StoredWorkflowEntry:
    row = connection.execute(
        """
        SELECT entry_id, workflow_run_id, entry_kind, action_ref,
               idempotency_key, request_sha256, base_revision
        FROM golden_workflow_turns
        WHERE entry_id = ?
        """,
        (str(entry_id),),
    ).fetchone()
    if row is None:
        raise GoldenRunCorruptionError("workflow detail record is missing its shared turn")
    return _decode_entry(connection, row)


def _update_snapshot(connection: sqlite3.Connection, snapshot: RunSnapshot) -> None:
    payload = _canonical_model_bytes(snapshot)
    cursor = connection.execute(
        """
        UPDATE OR ABORT golden_workflow_starts
        SET snapshot_json = ?, snapshot_sha256 = ?
        WHERE workflow_run_id = ? AND principal_id = ?
        """,
        (
            payload,
            hashlib.sha256(payload).hexdigest(),
            str(snapshot.workflow_run_id),
            snapshot.principal_id,
        ),
    )
    if cursor.rowcount != 1:
        raise GoldenRunCorruptionError("workflow head disappeared during atomic update")


def _read_column_layout(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return _read_named_column_layout(connection, "golden_workflow_starts")


def _read_named_column_layout(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row[key] for key in ("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"))
        for row in connection.execute("SELECT * FROM pragma_table_xinfo(?)", (table_name,))
    )


def _read_named_index_layouts(
    connection: sqlite3.Connection,
    table_name: str,
) -> tuple[tuple[object, ...], ...]:
    indexes = []
    for row in connection.execute("SELECT * FROM pragma_index_list(?)", (table_name,)):
        columns = tuple(
            tuple(column[key] for key in ("seqno", "cid", "name", "desc", "coll", "key"))
            for column in connection.execute(
                """
                SELECT seqno, cid, name, desc, coll, key
                FROM pragma_index_xinfo(?)
                ORDER BY seqno
                """,
                (row["name"],),
            ).fetchall()
        )
        indexes.append((row["origin"], row["unique"], row["partial"], columns))
    return tuple(indexes)


def _require_operation_table_layout(connection: sqlite3.Connection) -> None:
    _require_named_table_layout(
        connection,
        table_name="golden_workflow_operations",
        columns=_OPERATION_COLUMNS,
        indexes=_OPERATION_INDEXES,
    )


def _require_turn_table_layout(connection: sqlite3.Connection) -> None:
    _require_named_table_layout(
        connection,
        table_name="golden_workflow_turns",
        columns=_TURN_COLUMNS,
        indexes=_TURN_INDEXES,
    )


def _require_control_table_layout(connection: sqlite3.Connection) -> None:
    _require_named_table_layout(
        connection,
        table_name="golden_workflow_control_records",
        columns=_CONTROL_COLUMNS,
        indexes=_CONTROL_INDEXES,
    )


def _require_resource_access_table_layout(connection: sqlite3.Connection) -> None:
    _require_named_table_layout(
        connection,
        table_name="golden_workflow_resource_accesses",
        columns=_RESOURCE_ACCESS_COLUMNS,
        indexes=_RESOURCE_ACCESS_INDEXES,
    )


def _require_all_current_layouts(connection: sqlite3.Connection) -> None:
    _require_table_layout(
        connection,
        columns=_CURRENT_COLUMNS,
        indexes=_CURRENT_INDEXES,
    )
    _require_operation_table_layout(connection)
    _require_turn_table_layout(connection)
    _require_control_table_layout(connection)
    _require_resource_access_table_layout(connection)


def _require_table_layout(
    connection: sqlite3.Connection,
    *,
    columns: tuple[tuple[object, ...], ...],
    indexes: tuple[tuple[object, ...], ...],
) -> None:
    _require_named_table_layout(
        connection,
        table_name="golden_workflow_starts",
        columns=columns,
        indexes=indexes,
    )


def _require_named_table_layout(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    columns: tuple[tuple[object, ...], ...],
    indexes: tuple[tuple[object, ...], ...],
) -> None:
    table_metadata = tuple(
        tuple(row[key] for key in ("schema", "name", "type", "ncol", "wr", "strict"))
        for row in connection.execute(
            "SELECT * FROM pragma_table_list(?)",
            (table_name,),
        )
    )
    expected_table_metadata = (("main", table_name, "table", len(columns), 0, 0),)
    if table_metadata != expected_table_metadata:
        raise GoldenRunCorruptionError("golden workflow database has an unsupported table layout")
    table_sql = connection.execute(
        """
        SELECT sql
        FROM sqlite_schema
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()["sql"]
    foreign_keys = connection.execute(
        "SELECT * FROM pragma_foreign_key_list(?)",
        (table_name,),
    ).fetchall()
    triggers = connection.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'trigger' AND tbl_name = ?
        """,
        (table_name,),
    ).fetchall()
    if (
        _read_named_column_layout(connection, table_name) != columns
        or Counter(_read_named_index_layouts(connection, table_name)) != Counter(indexes)
        or _TABLE_CONFLICT_POLICY.search(_SQL_QUOTED_OR_COMMENT.sub(" ", table_sql))
        or foreign_keys
        or triggers
    ):
        raise GoldenRunCorruptionError("golden workflow database has an unsupported table layout")


def _migrate_legacy_table(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT principal_id, idempotency_key, request_sha256, snapshot_json
        FROM golden_workflow_starts
        ORDER BY principal_id, idempotency_key
        """
    ).fetchall()
    migrated: list[tuple[str, str, str, str, str, bytes, str]] = []
    for row in rows:
        try:
            legacy = _LegacyWorkflowStart.model_validate(dict(row))
        except ValidationError as error:
            raise GoldenRunCorruptionError(
                f"cannot migrate legacy golden workflow row: {error}"
            ) from error
        label = f"legacy {legacy.principal_id}/{legacy.idempotency_key}"
        payload = _snapshot_bytes(legacy.snapshot_json, label=label)
        snapshot = _parse_canonical_snapshot(payload, label=label)
        if snapshot.principal_id != legacy.principal_id:
            raise GoldenRunCorruptionError(
                f"stored workflow {label} has inconsistent principal metadata"
            )
        migrated.append(
            (
                str(snapshot.workflow_run_id),
                legacy.principal_id,
                legacy.idempotency_key,
                legacy.request_sha256,
                snapshot.start_request_ref.sha256,
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )

    connection.execute("ALTER TABLE golden_workflow_starts RENAME TO golden_workflow_starts_legacy")
    _create_current_table(connection)
    connection.executemany(
        """
        INSERT OR ABORT INTO golden_workflow_starts (
            workflow_run_id, principal_id, idempotency_key,
            request_sha256, start_request_sha256, snapshot_json,
            snapshot_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        migrated,
    )
    connection.execute("DROP TABLE golden_workflow_starts_legacy")


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


__all__ = [
    "GoldenRunConflictError",
    "GoldenRunCorruptionError",
    "GoldenRunHeadConflictError",
    "SQLiteGoldenRunStore",
    "StoredResourceAccess",
]
