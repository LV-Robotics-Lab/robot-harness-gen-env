"""Public store-seam tests for one shared Golden workflow turn journal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.golden_store import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHeadConflictError,
    SQLiteGoldenRunStore,
)
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.golden_control import ControlCommitSnapshot
from self_improving.harness.schemas.workflow import RunSnapshot
from tests.self_improving.harness import test_golden_submit as submit_base


def _ref(digit: str, *, name: str, schema_version: str) -> ArtifactRef:
    digest = digit * 64
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=1,
        schema_version=schema_version,
    )


def _advisory_control(initial: RunSnapshot) -> tuple[ControlCommitSnapshot, RunSnapshot]:
    receipt = _ref(
        "d",
        name="advisory.json",
        schema_version="harness.external_agent_advisory_receipt.v1",
    )
    committed_at = initial.updated_at + timedelta(seconds=1)
    control = ControlCommitSnapshot(
        schema_version="harness.control_commit_snapshot.v1",
        command_id=UUID("41000000-0000-4000-8000-000000000001"),
        workflow_run_id=initial.workflow_run_id,
        principal_id=initial.principal_id,
        command=_ref(
            "c",
            name="advisory-command.json",
            schema_version="harness.advisory_record_command.v1",
        ),
        predecessor_receipt=initial.receipt_head,
        base_revision=initial.revision,
        committed_revision=initial.revision + 1,
        base_turn_seq=initial.turn_seq,
        committed_turn_seq=initial.turn_seq + 1,
        base_state_sha256=initial.state_sha256,
        base_state=initial.state_ref,
        resulting_state_sha256=initial.state_sha256,
        resulting_state=initial.state_ref,
        committed_at=committed_at,
        control_kind="advisory",
        idempotency_key="advisory-001",
        payload=receipt,
        receipt=receipt,
        receipt_head=receipt,
    )
    snapshot = RunSnapshot.model_validate(
        initial.model_copy(
            update={
                "revision": control.committed_revision,
                "turn_seq": control.committed_turn_seq,
                "receipt_head": control.receipt_head,
                "updated_at": control.committed_at,
            }
        ).model_dump(mode="python")
    )
    return control, snapshot


def test_skill_reservation_owns_one_shared_workflow_turn(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "turns.sqlite3",
    )

    assert (
        store.read_entries(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
        )
        == ()
    )
    reserved = submit_base._reserve_transition(store, initial, running)

    assert reserved is not None
    entries = store.read_entries(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_kind == "skill_operation"
    assert entry.entry_id == running.operation_id
    assert entry.workflow_run_id == running.workflow_run_id
    assert entry.action_ref == running.skill_ref
    assert entry.idempotency_key == running.idempotency_key
    assert entry.request_sha256 == running.command.sha256
    assert entry.base_revision == running.base_revision
    assert entry.operation == running
    assert entry.control is None


def test_old_operation_database_is_transactionally_backfilled(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "old")
    with sqlite3.connect(case.workflow_store.path) as connection:
        connection.execute("DROP TABLE golden_workflow_turns")
        connection.execute("DROP TABLE golden_workflow_control_records")

    reopened = SQLiteGoldenRunStore(case.workflow_store.path)
    entries = reopened.read_entries(
        principal_id=case.command.principal_id,
        workflow_run_id=case.command.workflow_run_id,
    )

    assert len(entries) == 1
    assert entries[0].entry_kind == "skill_operation"
    assert entries[0].operation == case.operation


def test_old_database_backfills_an_active_skill_reservation(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "old-active-source")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "old-active.sqlite3",
    )
    submit_base._reserve_transition(store, initial, running)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE golden_workflow_turns")
        connection.execute("DROP TABLE golden_workflow_control_records")

    reopened = SQLiteGoldenRunStore(store.path)

    entries = reopened.read_entries(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert len(entries) == 1 and entries[0].operation == running
    persisted = reopened.read_snapshot(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert persisted is not None
    assert persisted.snapshot.active_operation == running.operation_id


def test_control_commit_is_atomic_idempotent_and_uses_the_shared_turn(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-control")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "control.sqlite3",
    )
    control, snapshot = _advisory_control(initial)

    committed = store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )

    assert committed is not None
    assert committed.control == control
    assert committed.command_sha256 == control.command.sha256
    assert (
        store.commit_control_record(
            principal_id=initial.principal_id,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control,
            snapshot=snapshot,
        )
        == committed
    )
    assert (
        store.read_control_record(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
            control_kind=control.control_kind,
            idempotency_key=control.idempotency_key,
        )
        == committed
    )
    entries = store.read_entries(
        principal_id=initial.principal_id,
        workflow_run_id=initial.workflow_run_id,
    )
    assert len(entries) == 1
    assert entries[0].entry_kind == "control_record"
    assert entries[0].control == control
    assert entries[0].operation is None
    persisted = store.read_snapshot(
        principal_id=initial.principal_id,
        workflow_run_id=initial.workflow_run_id,
    )
    assert persisted is not None and persisted.snapshot == snapshot


def test_skill_commit_updates_detail_without_allocating_another_turn(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-skill-commit")
    store, initial, running, final = submit_base._store_transition_fixture(
        case,
        tmp_path / "skill-commit.sqlite3",
    )
    submit_base._reserve_transition(store, initial, running)

    committed = store.commit_operation(
        principal_id=running.principal_id,
        running=running,
        terminal=case.operation,
        snapshot=final,
    )

    assert committed is not None and committed.operation == case.operation
    entries = store.read_entries(
        principal_id=running.principal_id,
        workflow_run_id=running.workflow_run_id,
    )
    assert len(entries) == 1
    assert entries[0].entry_id == running.operation_id
    assert entries[0].operation == case.operation
    assert (
        store.read_operations(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
        )[0].operation
        == case.operation
    )


def test_control_reads_hide_foreign_workflows_and_missing_slots(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-private")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "private.sqlite3",
    )
    control, snapshot = _advisory_control(initial)

    assert (
        store.read_control_record(
            principal_id="tests/foreign",
            workflow_run_id=initial.workflow_run_id,
            control_kind="advisory",
            idempotency_key=control.idempotency_key,
        )
        is None
    )
    assert (
        store.read_control_record(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
            control_kind="advisory",
            idempotency_key="missing",
        )
        is None
    )
    assert (
        store.commit_control_record(
            principal_id="tests/foreign",
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control,
            snapshot=snapshot,
        )
        is None
    )
    assert (
        store.read_entries(
            principal_id="tests/foreign",
            workflow_run_id=initial.workflow_run_id,
        )
        == ()
    )


def test_control_retry_rejects_changed_command_or_record(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-retry")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "retry.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    values = {
        "principal_id": initial.principal_id,
        "expected_workflow_revision": initial.revision,
        "expected_state_sha256": initial.state_sha256,
        "expected_receipt_head_sha256": initial.receipt_head.sha256,
        "snapshot": snapshot,
    }
    store.commit_control_record(control=control, **values)

    with pytest.raises(GoldenRunConflictError, match="different command"):
        store.commit_control_record(
            control=control.model_copy(
                update={
                    "command": _ref(
                        "e",
                        name="changed-command.json",
                        schema_version="harness.advisory_record_command.v1",
                    )
                }
            ),
            **values,
        )
    with pytest.raises(GoldenRunCorruptionError, match="retry differs"):
        store.commit_control_record(
            control=control.model_copy(
                update={"committed_at": control.committed_at + timedelta(seconds=1)}
            ),
            **values,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_workflow_revision", 99),
        ("expected_state_sha256", "f" * 64),
        ("expected_receipt_head_sha256", "f" * 64),
    ],
)
def test_control_commit_rejects_a_stale_expected_head(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    case = submit_base._build_case(tmp_path / f"source-{field}")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / f"{field}.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    values = {
        "principal_id": initial.principal_id,
        "expected_workflow_revision": initial.revision,
        "expected_state_sha256": initial.state_sha256,
        "expected_receipt_head_sha256": initial.receipt_head.sha256,
        "control": control,
        "snapshot": snapshot,
    }
    values[field] = value

    with pytest.raises(GoldenRunHeadConflictError):
        store.commit_control_record(**values)


def test_running_skill_blocks_a_control_at_the_same_base_revision(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-active")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "active.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    submit_base._reserve_transition(store, initial, running)

    with pytest.raises(GoldenRunHeadConflictError):
        store.commit_control_record(
            principal_id=initial.principal_id,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control,
            snapshot=snapshot,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"principal_id": "tests/foreign"},
        {"base_revision": 9},
        {"committed_revision": 9},
        {"base_turn_seq": 9},
        {"committed_turn_seq": 9},
        {"predecessor_receipt": None},
        {"base_state_sha256": "f" * 64},
        {"base_state": None},
        {"resulting_state_sha256": "f" * 64},
        {"resulting_state": None},
        {"committed_at": None},
    ],
)
def test_control_commit_rejects_authority_drift(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    case = submit_base._build_case(tmp_path / "source-authority")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "authority.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    if change == {"predecessor_receipt": None}:
        change = {"predecessor_receipt": control.command}
    elif change == {"base_state": None}:
        change = {"base_state": control.command}
    elif change == {"resulting_state": None}:
        change = {"resulting_state": control.command}
    elif change == {"committed_at": None}:
        change = {"committed_at": initial.updated_at - timedelta(seconds=1)}

    with pytest.raises(GoldenRunCorruptionError, match="authority metadata"):
        store.commit_control_record(
            principal_id=initial.principal_id,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control.model_copy(update=change),
            snapshot=snapshot,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"workspace": "production"},
        {"revision": 9},
        {"turn_seq": 9},
        {"receipt_head": None},
        {"updated_at": None},
    ],
)
def test_control_commit_rejects_a_nonexact_resulting_head(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    case = submit_base._build_case(tmp_path / "source-result")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "result.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    if change == {"receipt_head": None}:
        change = {"receipt_head": initial.receipt_head}
    elif change == {"updated_at": None}:
        change = {"updated_at": snapshot.updated_at + timedelta(seconds=1)}

    with pytest.raises(GoldenRunCorruptionError, match="exact control transition"):
        store.commit_control_record(
            principal_id=initial.principal_id,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control,
            snapshot=snapshot.model_copy(update=change),
        )


def test_control_and_skill_compete_for_one_base_revision(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-shared-base")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "shared-base.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )

    with pytest.raises(GoldenRunHeadConflictError):
        submit_base._reserve_transition(store, initial, running)


def test_shared_base_constraint_rolls_back_control_detail(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-rollback")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "rollback.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO golden_workflow_turns VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(UUID("42000000-0000-4000-8000-000000000001")),
                str(initial.workflow_run_id),
                "skill_operation",
                "tests.occupied@1.0.0",
                "occupied",
                "a" * 64,
                initial.revision,
            ),
        )

    with pytest.raises(GoldenRunConflictError, match="another workflow transition"):
        store.commit_control_record(
            principal_id=initial.principal_id,
            expected_workflow_revision=initial.revision,
            expected_state_sha256=initial.state_sha256,
            expected_receipt_head_sha256=initial.receipt_head.sha256,
            control=control,
            snapshot=snapshot,
        )
    assert (
        store.read_control_record(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
            control_kind="advisory",
            idempotency_key=control.idempotency_key,
        )
        is None
    )
    persisted = store.read_snapshot(
        principal_id=initial.principal_id,
        workflow_run_id=initial.workflow_run_id,
    )
    assert persisted is not None and persisted.snapshot == initial


def test_shared_base_constraint_rolls_back_skill_detail(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-skill-rollback")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "skill-rollback.sqlite3",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO golden_workflow_turns VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(UUID("42000000-0000-4000-8000-000000000002")),
                str(initial.workflow_run_id),
                "control_record",
                "advisory",
                "occupied",
                "b" * 64,
                initial.revision,
            ),
        )

    with pytest.raises(GoldenRunConflictError, match="operation identity"):
        submit_base._reserve_transition(store, initial, running)
    assert (
        store.read_operation(
            principal_id=running.principal_id,
            workflow_run_id=running.workflow_run_id,
            skill_ref=running.skill_ref,
            idempotency_key=running.idempotency_key,
        )
        is None
    )
    persisted = store.read_snapshot(
        principal_id=initial.principal_id,
        workflow_run_id=initial.workflow_run_id,
    )
    assert persisted is not None and persisted.snapshot == initial


def test_partial_shared_turn_layout_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "partial.sqlite3"
    SQLiteGoldenRunStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE golden_workflow_control_records")

    with pytest.raises(GoldenRunCorruptionError, match="shared turn layout"):
        SQLiteGoldenRunStore(database)


def test_old_database_backfill_rejects_an_operation_outside_a_workflow(
    tmp_path: Path,
) -> None:
    case = submit_base._build_case(tmp_path / "source-orphan")
    foreign_id = UUID("43000000-0000-4000-8000-000000000001")
    foreign = case.operation.model_copy(update={"workflow_run_id": foreign_id})
    payload = submit_base.base._canonical_bytes(foreign)
    with sqlite3.connect(case.workflow_store.path) as connection:
        connection.execute("DROP TABLE golden_workflow_turns")
        connection.execute("DROP TABLE golden_workflow_control_records")
        connection.execute(
            """
            UPDATE golden_workflow_operations
            SET workflow_run_id = ?, operation_json = ?, operation_sha256 = ?
            """,
            (str(foreign_id), payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="outside its workflow authority"):
        SQLiteGoldenRunStore(case.workflow_store.path)
    with sqlite3.connect(case.workflow_store.path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        }
    assert "golden_workflow_turns" not in names
    assert "golden_workflow_control_records" not in names


def test_control_read_rejects_a_missing_shared_turn(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-missing-turn")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "missing-turn.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM golden_workflow_turns")

    with pytest.raises(GoldenRunCorruptionError, match="missing its shared turn"):
        store.read_control_record(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
            control_kind="advisory",
            idempotency_key=control.idempotency_key,
        )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE golden_workflow_turns SET entry_kind = 'unknown'",
            "invalid authority metadata",
        ),
        (
            "UPDATE golden_workflow_turns SET action_ref = 'prompt_revision'",
            "differs from its control record",
        ),
        (
            "UPDATE golden_workflow_turns SET entry_id = '44000000-0000-4000-8000-000000000001'",
            "missing its control record",
        ),
        (
            "DELETE FROM golden_workflow_control_records",
            "detail records and shared turn journal disagree",
        ),
    ],
)
def test_shared_entry_read_rejects_control_journal_corruption(
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    case = submit_base._build_case(tmp_path / "source-entry-corrupt")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "entry-corrupt.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(statement)

    with pytest.raises(GoldenRunCorruptionError, match=message):
        store.read_entries(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
        )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE golden_workflow_control_records SET principal_id = ''",
            "invalid authority metadata",
        ),
        (
            "UPDATE golden_workflow_control_records SET control_sha256 = 'bad'",
            "invalid payload checksum",
        ),
        (
            "UPDATE golden_workflow_control_records SET control_sha256 = "
            "'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'",
            "failed its payload checksum",
        ),
        (
            "UPDATE golden_workflow_control_records SET control_kind = 'prompt_revision'",
            "inconsistent authority metadata",
        ),
    ],
)
def test_control_read_rejects_corrupt_detail_authority(
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    case = submit_base._build_case(tmp_path / "source-detail-corrupt")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "detail-corrupt.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(statement)

    with pytest.raises(GoldenRunCorruptionError, match=message):
        store.read_entries(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
        )


@pytest.mark.parametrize("payload", [b"{", b'{"schema_version": "wrong"}'])
def test_control_read_rejects_an_invalid_canonical_payload(
    tmp_path: Path,
    payload: bytes,
) -> None:
    case = submit_base._build_case(tmp_path / "source-invalid-payload")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "invalid-payload.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE golden_workflow_control_records
            SET control_json = ?, control_sha256 = ?
            """,
            (payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="cannot reconstruct stored control"):
        store.read_entries(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
        )


def test_control_read_rejects_noncanonical_json_bytes(tmp_path: Path) -> None:
    case = submit_base._build_case(tmp_path / "source-noncanonical")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "noncanonical.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    canonical = submit_base.base._canonical_bytes(control)
    payload = json.dumps(json.loads(canonical), indent=2).encode()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE golden_workflow_control_records
            SET control_json = ?, control_sha256 = ?
            """,
            (payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="not canonical JSON bytes"):
        store.read_entries(
            principal_id=initial.principal_id,
            workflow_run_id=initial.workflow_run_id,
        )


@pytest.mark.parametrize("read_all", [False, True])
def test_control_read_rejects_self_consistent_foreign_principal_detail(
    tmp_path: Path,
    read_all: bool,
) -> None:
    case = submit_base._build_case(tmp_path / "source-foreign-detail")
    store, initial, _, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "foreign-detail.sqlite3",
    )
    control, snapshot = _advisory_control(initial)
    store.commit_control_record(
        principal_id=initial.principal_id,
        expected_workflow_revision=initial.revision,
        expected_state_sha256=initial.state_sha256,
        expected_receipt_head_sha256=initial.receipt_head.sha256,
        control=control,
        snapshot=snapshot,
    )
    foreign = control.model_copy(update={"principal_id": "tests/foreign"})
    payload = submit_base.base._canonical_bytes(foreign)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE golden_workflow_control_records
            SET principal_id = ?, control_json = ?, control_sha256 = ?
            """,
            ("tests/foreign", payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="outside its workflow authority"):
        if read_all:
            store.read_entries(
                principal_id=initial.principal_id,
                workflow_run_id=initial.workflow_run_id,
            )
        else:
            store.read_control_record(
                principal_id=initial.principal_id,
                workflow_run_id=initial.workflow_run_id,
                control_kind="advisory",
                idempotency_key=control.idempotency_key,
            )


@pytest.mark.parametrize("read_all", [False, True])
def test_skill_read_rejects_self_consistent_foreign_principal_detail(
    tmp_path: Path,
    read_all: bool,
) -> None:
    case = submit_base._build_case(tmp_path / "source-foreign-skill")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "foreign-skill.sqlite3",
    )
    submit_base._reserve_transition(store, initial, running)
    foreign = running.model_copy(update={"principal_id": "tests/foreign"})
    payload = submit_base.base._canonical_bytes(foreign)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE golden_workflow_operations
            SET principal_id = ?, operation_json = ?, operation_sha256 = ?
            """,
            ("tests/foreign", payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="outside its workflow authority"):
        if read_all:
            store.read_entries(
                principal_id=initial.principal_id,
                workflow_run_id=initial.workflow_run_id,
            )
        else:
            store.read_operation(
                principal_id=initial.principal_id,
                workflow_run_id=initial.workflow_run_id,
                skill_ref=running.skill_ref,
                idempotency_key=running.idempotency_key,
            )


def test_skill_retry_rejects_self_consistent_foreign_principal_detail(
    tmp_path: Path,
) -> None:
    case = submit_base._build_case(tmp_path / "source-foreign-skill-retry")
    store, initial, running, _ = submit_base._store_transition_fixture(
        case,
        tmp_path / "foreign-skill-retry.sqlite3",
    )
    submit_base._reserve_transition(store, initial, running)
    foreign = running.model_copy(update={"principal_id": "tests/foreign"})
    payload = submit_base.base._canonical_bytes(foreign)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE golden_workflow_operations
            SET principal_id = ?, operation_json = ?, operation_sha256 = ?
            """,
            ("tests/foreign", payload, hashlib.sha256(payload).hexdigest()),
        )

    with pytest.raises(GoldenRunCorruptionError, match="outside its workflow authority"):
        submit_base._reserve_transition(store, initial, running)
