"""Public S1 tracer tests for the Golden Workflow aggregate."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunNotFoundError,
    GoldenRunRegistryError,
)
from self_improving.harness.golden_store import SQLiteGoldenRunStore
from self_improving.harness.handlers.text2env_compile import text2env_compile_descriptor
from self_improving.harness.qualification import ImplementationManifestV1
from self_improving.harness.schemas.common import SkillDescriptor
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.workflow import (
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
    WorkflowStartReceipt,
)

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
WORKFLOW_ID = UUID("10000000-0000-4000-8000-000000000001")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OBJECTIVE_EVIDENCE = (
    b'{"key":"request.objective","observed_at":"2026-09-09T00:00:00Z",'
    b'"schema_version":"harness.world_fact_evidence.v1",'
    b'"source_kind":"user_input","valid_until":null,'
    b'"value":"Place a can on a plate."}\n'
)
ALTERNATE_OBJECTIVE_EVIDENCE = OBJECTIVE_EVIDENCE.replace(
    b"Place a can on a plate.",
    b"Place a bottle on a plate.",
)


def _put(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    schema_version: str,
):
    source = root / name
    source.write_bytes(payload)
    return store.put_file(
        source,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def _request(objective, registry, **changes) -> RunStartRequest:
    values = {
        "schema_version": "harness.run_start_request.v1",
        "principal_id": "tests/golden-e2e",
        "idempotency_key": "start-text-001",
        "workspace": "ephemeral",
        "requested_profile": "genesis.robot_policy@1",
        "initial_fact_evidence": (objective,),
        "registry_snapshot": registry,
    }
    values.update(changes)
    return RunStartRequest.model_validate(values)


def _canonical_bytes(model) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _qualified_registry(store: LocalArtifactStore, root: Path):
    bundle = (
        PROJECT_ROOT
        / "self_improving"
        / "harness"
        / "qualified_skills"
        / "text2env.compile"
        / "1.0.0"
    )
    qualification_ref = _put(
        store,
        root,
        name="compile-qualification.json",
        payload=(bundle / "qualification.json").read_bytes(),
        schema_version="harness.skill_qualification.v1",
    )
    report_ref = _put(
        store,
        root,
        name="compile-qualification-report.json",
        payload=(bundle / "report.json").read_bytes(),
        schema_version="harness.skill_qualification_report.v1",
    )
    manifest_payload = (bundle / "manifest.json").read_bytes()
    manifest = ImplementationManifestV1.model_validate_json(manifest_payload)
    descriptor = text2env_compile_descriptor(
        qualification_artifact=qualification_ref,
        implementation_sha256=manifest.bundle_sha256,
    )
    descriptor_ref = _put(
        store,
        root,
        name="compile-descriptor.json",
        payload=_canonical_bytes(descriptor),
        schema_version="harness.skill_descriptor.v1",
    )
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=(
            QualifiedSkillSnapshot(
                skill_ref="text2env.compile@1.0.0",
                mcp_tool_name="text2env_compile_v1_0_0",
                descriptor_ref=descriptor_ref,
                qualification_ref=qualification_ref,
                qualification_report_ref=report_ref,
            ),
        ),
    )
    return _put(
        store,
        root,
        name="registry.json",
        payload=_canonical_bytes(snapshot),
        schema_version="harness.registry_snapshot.v1",
    )


def _load_registry(store: LocalArtifactStore, registry_ref) -> RegistrySnapshot:
    return RegistrySnapshot.model_validate_json(store.resolve(registry_ref).path.read_bytes())


def _publish_registry_entry(
    store: LocalArtifactStore,
    root: Path,
    entry: QualifiedSkillSnapshot,
    *,
    name: str,
):
    snapshot = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=(entry,),
    )
    return _put(
        store,
        root,
        name=name,
        payload=_canonical_bytes(snapshot),
        schema_version="harness.registry_snapshot.v1",
    )


def _replace_persisted_snapshot(store: LocalArtifactStore, payload: bytes) -> None:
    database = store.root / "golden-workflows.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            UPDATE golden_workflow_starts
            SET snapshot_json = ?, snapshot_sha256 = ?
            """,
            (payload, sha256(payload).hexdigest()),
        )


def _replace_with_legacy_start_table(
    database: Path,
    *,
    request_sha256: str,
) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute(
            """
            SELECT principal_id, idempotency_key, snapshot_json
            FROM golden_workflow_starts
            """
        ).fetchone()
        connection.execute("DROP TABLE golden_workflow_starts")
        connection.execute(
            """
            CREATE TABLE golden_workflow_starts (
                principal_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                snapshot_json BLOB NOT NULL,
                PRIMARY KEY (principal_id, idempotency_key)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO golden_workflow_starts (
                principal_id, idempotency_key, request_sha256, snapshot_json
            ) VALUES (?, ?, ?, ?)
            """,
            (row[0], row[1], request_sha256, row[2]),
        )


def _current_store_layout_ddl(
    *,
    workflow_run_id: str = "TEXT NOT NULL PRIMARY KEY",
    principal_id: str = "TEXT NOT NULL",
    request_sha256: str = "TEXT NOT NULL",
    start_request_sha256: str = "TEXT NOT NULL",
    snapshot_json: str = "BLOB NOT NULL",
    snapshot_sha256: str = "TEXT NOT NULL",
    principal_lookup: bool = True,
    start_request_lookup: bool = True,
    extra_definitions: tuple[str, ...] = (),
    table_suffix: str = "",
) -> str:
    definitions = [
        f"workflow_run_id {workflow_run_id}",
        f"principal_id {principal_id}",
        "idempotency_key TEXT NOT NULL",
        f"request_sha256 {request_sha256}",
        f"start_request_sha256 {start_request_sha256}",
        f"snapshot_json {snapshot_json}",
        f"snapshot_sha256 {snapshot_sha256}",
        *extra_definitions,
    ]
    if principal_lookup:
        definitions.append("UNIQUE (principal_id, idempotency_key)")
    if start_request_lookup:
        definitions.append("UNIQUE (start_request_sha256)")
    body = ",\n                ".join(definitions)
    return (
        "CREATE TABLE golden_workflow_starts "
        f"(\n                {body}\n            ){table_suffix}"
    )


class _ArtifactStoreLookalike:
    def __init__(self, root: Path) -> None:
        self.root = root


def _golden_harness(
    *,
    artifact_store: LocalArtifactStore,
    workflow_store: SQLiteGoldenRunStore | None = None,
    **kwargs: object,
) -> GoldenRunHarness:
    selected_store = workflow_store or SQLiteGoldenRunStore(
        artifact_store.root / "golden-workflows.sqlite3"
    )
    return GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=selected_store,
        **kwargs,  # type: ignore[arg-type]
    )


def test_start_builds_trusted_state_and_receipt_chain_from_cas_inputs(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    harness = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    request = _request(objective, registry)

    snapshot = harness.start(request)

    assert set(snapshot.model_dump(mode="json")) == {
        "schema_version",
        "workflow_run_id",
        "principal_id",
        "workspace",
        "requested_profile",
        "revision",
        "status",
        "turn_seq",
        "state_sha256",
        "state_ref",
        "start_request_ref",
        "registry_snapshot",
        "receipt_head",
        "active_operation",
        "child_runs",
        "started_at",
        "updated_at",
    }
    assert snapshot.workflow_run_id == WORKFLOW_ID
    assert snapshot.principal_id == "tests/golden-e2e"
    assert snapshot.workspace == "ephemeral"
    assert snapshot.requested_profile == "genesis.robot_policy@1"
    assert snapshot.revision == 0
    assert snapshot.status == "active"
    assert snapshot.turn_seq == 0
    assert snapshot.state_sha256 == (
        "5875f1c2a692652d6b4be968e8cd1b1d75d014c7cc196c78073228cb1d0e108e"
    )
    assert snapshot.state_ref.sha256 == (
        "25e338adaaa0609e47512ebd833a933a9f048fb486c1974a2ad9567f0a0fc52a"
    )
    assert snapshot.state_ref.schema_version == "harness.trusted_world_state.v1"
    assert snapshot.start_request_ref.sha256 == (
        "2d464fc29e42dfd14d38104fca9811270c875351e2a42dfeb79b1963dd1f232e"
    )
    assert snapshot.start_request_ref.schema_version == "harness.run_start_request.v1"
    assert store.resolve(snapshot.start_request_ref).path.read_bytes() == _canonical_bytes(request)
    assert snapshot.registry_snapshot == registry
    start_receipt = WorkflowStartReceipt.model_validate_json(
        store.resolve(snapshot.receipt_head).path.read_bytes()
    )
    assert start_receipt.request_ref == snapshot.start_request_ref
    assert snapshot.receipt_head.sha256 == (
        "4979e9073562e71b76276264a024114ed0d7f57bf7a1c62dab61d118fb7ea18d"
    )
    assert snapshot.receipt_head.schema_version == "harness.workflow_start_receipt.v1"
    assert snapshot.active_operation is None
    assert snapshot.child_runs == ()
    assert snapshot.started_at == NOW
    assert snapshot.updated_at == NOW


def test_harness_requires_the_exact_local_cas_authority(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="artifact_store must be the exact LocalArtifactStore"):
        _golden_harness(artifact_store=_ArtifactStoreLookalike(tmp_path))  # type: ignore[arg-type]


def test_harness_requires_an_explicit_durable_workflow_authority(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="workflow_store"):
        GoldenRunHarness(  # type: ignore[call-arg]
            artifact_store=LocalArtifactStore(tmp_path / "cas")
        )


def test_harness_rejects_a_workflow_store_lookalike(tmp_path: Path) -> None:
    with pytest.raises(
        TypeError,
        match="workflow_store must be the exact SQLiteGoldenRunStore",
    ):
        GoldenRunHarness(
            artifact_store=LocalArtifactStore(tmp_path / "cas"),
            workflow_store=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"principal_id": ""}, "at least 1 character"),
        (
            {"workflow_run_id": UUID("10000000-0000-1000-8000-000000000001")},
            "UUID version 4",
        ),
        ({"unexpected": True}, "Extra inputs are not permitted"),
    ],
)
def test_run_read_request_rejects_ambiguous_identity(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "schema_version": "harness.run_read_request.v1",
        "principal_id": "tests/golden-e2e",
        "workflow_run_id": WORKFLOW_ID,
    }
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        RunReadRequest.model_validate(values)


def test_start_is_durably_idempotent_across_harness_restart(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)
    first = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)

    def unexpected_clock() -> datetime:
        pytest.fail("an idempotent retry must not read the execution clock")

    def unexpected_workflow_id() -> UUID:
        pytest.fail("an idempotent retry must not allocate another workflow identity")

    retry = _golden_harness(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        clock=unexpected_clock,
        workflow_id_factory=unexpected_workflow_id,
    ).start(request)

    assert retry == first


def test_start_retry_rejects_tampered_idempotency_lookup_metadata(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    request = _request(objective, registry)
    _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE golden_workflow_starts SET idempotency_key = 'tampered-key'")

    with pytest.raises(
        GoldenRunCorruptionError,
        match="idempotency metadata",
    ):
        _golden_harness(
            artifact_store=artifact_store,
            workflow_store=SQLiteGoldenRunStore(database),
        ).read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=request.principal_id,
                workflow_run_id=WORKFLOW_ID,
            )
        )

    with pytest.raises(
        GoldenRunCorruptionError,
        match="idempotency metadata",
    ) as raised:
        _golden_harness(
            artifact_store=artifact_store,
            workflow_store=SQLiteGoldenRunStore(database),
            clock=lambda: NOW,
            workflow_id_factory=lambda: UUID("20000000-0000-4000-8000-000000000002"),
        ).start(request)
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


def test_legacy_private_start_store_is_migrated_for_retry_and_read(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "legacy-workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    request = _request(objective, registry)
    snapshot = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    receipt = WorkflowStartReceipt.model_validate_json(
        artifact_store.resolve(snapshot.receipt_head).path.read_bytes()
    )
    _replace_with_legacy_start_table(
        database,
        request_sha256=receipt.request_sha256,
    )

    def unexpected_clock() -> datetime:
        pytest.fail("legacy idempotency recovery must not execute the start again")

    def unexpected_workflow_id() -> UUID:
        pytest.fail("legacy idempotency recovery must retain the workflow identity")

    migrated = _golden_harness(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        workflow_store=SQLiteGoldenRunStore(database),
        clock=unexpected_clock,
        workflow_id_factory=unexpected_workflow_id,
    )

    assert migrated.start(request) == snapshot
    assert (
        migrated.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=WORKFLOW_ID,
            )
        )
        == snapshot
    )


def test_legacy_migration_preserves_a_corrupt_logical_request_identity(
    tmp_path: Path,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "legacy-workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    request = _request(objective, registry)
    snapshot = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    _replace_with_legacy_start_table(database, request_sha256="f" * 64)

    migrated = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
    )

    with pytest.raises(
        GoldenRunCorruptionError,
        match="logical request identity",
    ):
        migrated.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=snapshot.workflow_run_id,
            )
        )
    with pytest.raises(
        GoldenRunCorruptionError,
        match="logical request identity",
    ):
        migrated.start(request)


def test_read_recovers_the_workflow_head_across_harness_restart(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))

    recovered = _golden_harness(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        workflow_store=SQLiteGoldenRunStore(database),
    ).read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id="tests/golden-e2e",
            workflow_run_id=WORKFLOW_ID,
        )
    )

    assert recovered == snapshot
    assert not (store.root / "golden-workflows.sqlite3").exists()

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from pathlib import Path
from uuid import UUID

from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import RunReadRequest
from self_improving.harness.golden_store import SQLiteGoldenRunStore

snapshot = GoldenRunHarness(
    artifact_store=LocalArtifactStore(Path(sys.argv[1])),
    workflow_store=SQLiteGoldenRunStore(Path(sys.argv[2])),
).read(
    RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=sys.argv[3],
        workflow_run_id=UUID(sys.argv[4]),
    )
)
print(json.dumps(snapshot.model_dump(mode="json"), sort_keys=True))
""",
            str(store.root),
            str(database),
            "tests/golden-e2e",
            str(WORKFLOW_ID),
        ],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == snapshot.model_dump(mode="json")


def test_read_hides_missing_and_other_principal_workflows(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    workflow_store = SQLiteGoldenRunStore(tmp_path / "workflow-authority.sqlite3")
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    harness = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(_request(objective, registry))

    requests = (
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id="tests/another-principal",
            workflow_run_id=WORKFLOW_ID,
        ),
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id="tests/golden-e2e",
            workflow_run_id=UUID("20000000-0000-4000-8000-000000000002"),
        ),
    )
    for request in requests:
        with pytest.raises(
            GoldenRunNotFoundError,
            match="golden workflow was not found for this principal",
        ) as raised:
            harness.read(request)
        assert raised.value.code == "HARN_WORKFLOW_NOT_FOUND"


def test_read_rejects_a_tampered_start_request_database_identity(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    harness = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(_request(objective, registry))
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE golden_workflow_starts SET start_request_sha256 = ?",
            ("f" * 64,),
        )

    with pytest.raises(
        GoldenRunCorruptionError,
        match="start request identity",
    ) as raised:
        harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=WORKFLOW_ID,
            )
        )
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


def test_read_rejects_a_missing_start_request_cas_object(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    harness = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    snapshot = harness.start(_request(objective, registry))
    artifact_store.resolve(snapshot.start_request_ref).path.unlink()

    with pytest.raises(GoldenRunCorruptionError, match="CAS closure") as raised:
        harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=WORKFLOW_ID,
            )
        )
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("text_payload", "canonical JSON bytes"),
        ("invalid_checksum", "invalid payload checksum"),
        ("mismatched_checksum", "failed its payload checksum"),
        ("invalid_authority_digest", "invalid authority metadata"),
    ],
)
def test_read_rejects_corrupt_sqlite_snapshot_storage(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    harness = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(_request(objective, registry))
    statements = {
        "text_payload": "UPDATE golden_workflow_starts "
        "SET snapshot_json = CAST(snapshot_json AS TEXT)",
        "invalid_checksum": "UPDATE golden_workflow_starts SET snapshot_sha256 = 'broken'",
        "mismatched_checksum": "UPDATE golden_workflow_starts SET snapshot_sha256 = '"
        + "f" * 64
        + "'",
        "invalid_authority_digest": ("UPDATE golden_workflow_starts SET request_sha256 = 'broken'"),
    }
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(statements[attack])

    with pytest.raises(GoldenRunCorruptionError, match=message) as raised:
        harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=WORKFLOW_ID,
            )
        )
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


def test_sqlite_golden_run_store_fails_closed_for_invalid_authority(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        SQLiteGoldenRunStore(tmp_path / "invalid-timeout.sqlite3", busy_timeout_ms=0)
    with pytest.raises(GoldenRunCorruptionError, match="unavailable or corrupt"):
        SQLiteGoldenRunStore(tmp_path)

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(GoldenRunCorruptionError, match="unavailable or corrupt"):
        SQLiteGoldenRunStore(corrupt)


def test_sqlite_golden_run_store_rejects_an_unknown_table_layout(tmp_path: Path) -> None:
    database = tmp_path / "unknown-layout.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE golden_workflow_starts (unknown TEXT)")

    with pytest.raises(GoldenRunCorruptionError, match="unsupported table layout"):
        SQLiteGoldenRunStore(database)


@pytest.mark.parametrize(
    "ddl",
    [
        _current_store_layout_ddl(workflow_run_id="TEXT NOT NULL"),
        _current_store_layout_ddl(principal_lookup=False),
        _current_store_layout_ddl(start_request_lookup=False),
        _current_store_layout_ddl(request_sha256="TEXT"),
        _current_store_layout_ddl(request_sha256="VARCHAR NOT NULL"),
        _current_store_layout_ddl(snapshot_json="TEXT NOT NULL"),
        _current_store_layout_ddl(snapshot_sha256="TEXT NOT NULL DEFAULT ''"),
        _current_store_layout_ddl(
            extra_definitions=("hidden_copy TEXT GENERATED ALWAYS AS (principal_id) VIRTUAL",)
        ),
    ],
    ids=[
        "missing-workflow-primary-key",
        "missing-principal-idempotency-unique",
        "missing-start-request-unique",
        "nullable-column",
        "wrong-declared-type-same-affinity",
        "wrong-type-affinity",
        "column-default",
        "hidden-generated-column",
    ],
)
def test_sqlite_golden_run_store_rejects_forged_current_column_constraints(
    tmp_path: Path,
    ddl: str,
) -> None:
    database = tmp_path / "forged-current-layout.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(ddl)

    with pytest.raises(GoldenRunCorruptionError, match="unsupported table layout") as raised:
        SQLiteGoldenRunStore(database)
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


@pytest.mark.parametrize(
    "ddl",
    [
        _current_store_layout_ddl()
        + "; CREATE INDEX extra_lookup ON golden_workflow_starts (principal_id)",
        _current_store_layout_ddl(start_request_lookup=False)
        + "; CREATE UNIQUE INDEX duplicate_lookup "
        "ON golden_workflow_starts (principal_id, idempotency_key)",
        _current_store_layout_ddl(start_request_lookup=False)
        + "; CREATE UNIQUE INDEX partial_lookup "
        "ON golden_workflow_starts (start_request_sha256) "
        "WHERE principal_id = 'tests/golden-e2e'",
        _current_store_layout_ddl(start_request_lookup=False)
        + "; CREATE UNIQUE INDEX expression_lookup "
        "ON golden_workflow_starts (lower(start_request_sha256))",
        _current_store_layout_ddl(start_request_sha256="TEXT NOT NULL COLLATE NOCASE"),
    ],
    ids=["extra", "duplicate", "partial", "expression", "collation"],
)
def test_sqlite_golden_run_store_rejects_forged_current_indexes(
    tmp_path: Path,
    ddl: str,
) -> None:
    database = tmp_path / "forged-current-indexes.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(ddl)

    with pytest.raises(GoldenRunCorruptionError, match="unsupported table layout") as raised:
        SQLiteGoldenRunStore(database)
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


def test_sqlite_golden_run_store_rejects_a_forged_legacy_layout(tmp_path: Path) -> None:
    database = tmp_path / "forged-legacy-layout.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE golden_workflow_starts (
                principal_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                snapshot_json BLOB NOT NULL
            )
            """
        )

    with pytest.raises(GoldenRunCorruptionError, match="unsupported table layout") as raised:
        SQLiteGoldenRunStore(database)
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


@pytest.mark.parametrize(
    "ddl",
    [
        _current_store_layout_ddl()
        + """; CREATE TRIGGER rewrite_workflow_authority
        AFTER INSERT ON golden_workflow_starts
        BEGIN
            UPDATE golden_workflow_starts
            SET request_sha256 = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff';
        END""",
        "CREATE TABLE principals (principal_id TEXT PRIMARY KEY); "
        + _current_store_layout_ddl(
            principal_id="TEXT NOT NULL REFERENCES principals(principal_id)"
        ),
        _current_store_layout_ddl(table_suffix=" STRICT"),
        _current_store_layout_ddl(table_suffix=" WITHOUT ROWID"),
        _current_store_layout_ddl(workflow_run_id="TEXT NOT NULL PRIMARY KEY ON CONFLICT REPLACE"),
        _current_store_layout_ddl().replace(
            "UNIQUE (principal_id, idempotency_key)",
            "UNIQUE (principal_id, idempotency_key) ON/**/CONFLICT REPLACE",
        ),
        _current_store_layout_ddl().replace(
            "UNIQUE (start_request_sha256)",
            "UNIQUE (start_request_sha256) ON CONFLICT REPLACE",
        ),
    ],
    ids=[
        "trigger",
        "foreign-key",
        "strict",
        "without-rowid",
        "primary-key-replace",
        "principal-idempotency-replace",
        "start-request-replace",
    ],
)
def test_sqlite_golden_run_store_rejects_forged_current_table_semantics(
    tmp_path: Path,
    ddl: str,
) -> None:
    database = tmp_path / "forged-current-table.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(ddl)

    with pytest.raises(GoldenRunCorruptionError, match="unsupported table layout") as raised:
        SQLiteGoldenRunStore(database)
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


@pytest.mark.parametrize(
    ("trigger_body", "message"),
    [
        (
            """
            UPDATE golden_workflow_starts
            SET request_sha256 =
                'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff';
            """,
            "logical request identity",
        ),
        (
            "UPDATE golden_workflow_starts SET idempotency_key = 'tampered-key';",
            "inconsistent authority metadata",
        ),
        ("DELETE FROM golden_workflow_starts;", "disappeared from durable authority"),
    ],
    ids=["rewrite-logical-digest", "rewrite-metadata", "delete"],
)
def test_start_rejects_authority_tampered_by_a_late_insert_trigger(
    tmp_path: Path,
    trigger_body: str,
    message: str,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    workflow_store = SQLiteGoldenRunStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(
            f"""
            CREATE TRIGGER rewrite_workflow_authority
            AFTER INSERT ON golden_workflow_starts
            BEGIN
                {trigger_body}
            END
            """
        )
    harness = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )

    with pytest.raises(GoldenRunCorruptionError, match=message) as raised:
        harness.start(_request(objective, registry))
    assert raised.value.code == "HARN_PERSISTENCE_CONFLICT"


@pytest.mark.parametrize("attack", ["principal", "start_request"])
def test_sqlite_golden_run_store_rejects_a_factory_outside_its_reservation(
    tmp_path: Path,
    attack: str,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=artifact_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    receipt = WorkflowStartReceipt.model_validate_json(
        artifact_store.resolve(snapshot.receipt_head).path.read_bytes()
    )
    principal_id = "tests/another-principal" if attack == "principal" else snapshot.principal_id
    start_request_sha256 = snapshot.start_request_ref.sha256 if attack == "principal" else "f" * 64
    target = SQLiteGoldenRunStore(tmp_path / f"factory-{attack}.sqlite3")

    with pytest.raises(GoldenRunCorruptionError, match="authority metadata"):
        target.get_or_create_start(
            principal_id=principal_id,
            idempotency_key="factory-attack",
            request_sha256=receipt.request_sha256,
            start_request_sha256=start_request_sha256,
            factory=lambda: snapshot,
        )


@pytest.mark.parametrize(
    ("attack", "message"),
    [
        ("invalid_row", "cannot migrate legacy"),
        ("principal_mismatch", "inconsistent principal metadata"),
    ],
)
def test_sqlite_golden_run_store_rejects_corrupt_legacy_authority(
    tmp_path: Path,
    attack: str,
    message: str,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "legacy-workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(database),
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    receipt = WorkflowStartReceipt.model_validate_json(
        artifact_store.resolve(snapshot.receipt_head).path.read_bytes()
    )
    _replace_with_legacy_start_table(
        database,
        request_sha256=receipt.request_sha256,
    )
    statements = {
        "invalid_row": "UPDATE golden_workflow_starts SET request_sha256 = 'broken'",
        "principal_mismatch": (
            "UPDATE golden_workflow_starts SET principal_id = 'tests/another-principal'"
        ),
    }
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(statements[attack])

    with pytest.raises(GoldenRunCorruptionError, match=message):
        SQLiteGoldenRunStore(database)


def test_sqlite_golden_run_store_bootstraps_concurrently_without_lock_errors(
    tmp_path: Path,
) -> None:
    for attempt in range(40):
        database = tmp_path / f"concurrent-bootstrap-{attempt}.sqlite3"
        ready = Barrier(2)

        def construct_store() -> SQLiteGoldenRunStore:
            ready.wait(timeout=2)
            return SQLiteGoldenRunStore(database, busy_timeout_ms=1_000)

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = tuple(workers.submit(construct_store) for _ in range(2))
            stores = tuple(future.result(timeout=3) for future in futures)

        assert all(store.path == database for store in stores)


def test_concurrent_start_is_one_durable_idempotent_transition(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    database = tmp_path / "workflow-authority.sqlite3"
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    request = _request(objective, registry)
    ready = Barrier(2)
    harnesses = (
        _golden_harness(
            artifact_store=artifact_store,
            workflow_store=SQLiteGoldenRunStore(database),
            clock=lambda: NOW,
            workflow_id_factory=lambda: WORKFLOW_ID,
        ),
        _golden_harness(
            artifact_store=artifact_store,
            workflow_store=SQLiteGoldenRunStore(database),
            clock=lambda: NOW + timedelta(seconds=1),
            workflow_id_factory=lambda: UUID("20000000-0000-4000-8000-000000000002"),
        ),
    )

    def start(harness: GoldenRunHarness) -> RunSnapshot:
        ready.wait(timeout=2)
        return harness.start(request)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = tuple(workers.submit(start, harness) for harness in harnesses)
        snapshots = tuple(future.result(timeout=5) for future in futures)

    assert snapshots[0] == snapshots[1]
    assert snapshots[0].workflow_run_id in {
        WORKFLOW_ID,
        UUID("20000000-0000-4000-8000-000000000002"),
    }
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM golden_workflow_starts").fetchone()[0] == 1


def test_start_rejects_a_workflow_identity_collision_across_principals(
    tmp_path: Path,
) -> None:
    artifact_store = LocalArtifactStore(tmp_path / "cas")
    workflow_store = SQLiteGoldenRunStore(tmp_path / "workflow-authority.sqlite3")
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    first = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    first.start(_request(objective, registry))
    second = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )

    with pytest.raises(
        GoldenRunConflictError,
        match="workflow identity is already bound",
    ) as raised:
        second.start(
            _request(
                objective,
                registry,
                principal_id="tests/another-principal",
            )
        )
    assert raised.value.code == "HARN_IDEMPOTENCY_CONFLICT"


def test_start_rejects_same_principal_and_idempotency_key_with_changed_request(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    alternate = _put(
        store,
        tmp_path,
        name="alternate-objective.json",
        payload=ALTERNATE_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    harness = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(_request(objective, registry))

    with pytest.raises(
        GoldenRunConflictError,
        match="idempotency key is already bound to a different request",
    ) as raised:
        harness.start(_request(alternate, registry))
    assert raised.value.code == "HARN_IDEMPOTENCY_CONFLICT"


def test_start_retry_rejects_noncanonical_persisted_snapshot(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)
    harness = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(request)
    database = store.root / "golden-workflows.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        payload = connection.execute("SELECT snapshot_json FROM golden_workflow_starts").fetchone()[
            0
        ]
    _replace_persisted_snapshot(store, payload.replace(b"{", b"{ ", 1))

    with pytest.raises(GoldenRunCorruptionError, match="canonical JSON bytes"):
        _golden_harness(artifact_store=store).start(request)


def test_start_retry_rejects_snapshot_swapped_between_idempotency_records(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    alternate = _put(
        store,
        tmp_path,
        name="alternate-objective.json",
        payload=ALTERNATE_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    workflow_ids = iter(
        (
            WORKFLOW_ID,
            UUID("20000000-0000-4000-8000-000000000002"),
        )
    )
    harness = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: next(workflow_ids),
    )
    first_request = _request(objective, registry)
    harness.start(first_request)
    harness.start(
        _request(
            alternate,
            registry,
            idempotency_key="start-text-002",
        )
    )
    database = store.root / "golden-workflows.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        swapped = connection.execute(
            """
            SELECT snapshot_json, snapshot_sha256 FROM golden_workflow_starts
            WHERE idempotency_key = 'start-text-002'
            """
        ).fetchone()
        connection.execute(
            """
            UPDATE golden_workflow_starts
            SET snapshot_json = ?, snapshot_sha256 = ?
            WHERE idempotency_key = 'start-text-001'
            """,
            swapped,
        )

    with pytest.raises(GoldenRunCorruptionError, match="identity metadata"):
        _golden_harness(artifact_store=store).start(first_request)


def test_start_retry_reverifies_cas_closure(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    store.resolve(snapshot.state_ref).path.write_bytes(b'{"tampered":true}\n')

    with pytest.raises(GoldenRunCorruptionError, match="CAS closure"):
        _golden_harness(artifact_store=store).start(request)


def test_read_rejects_a_valid_receipt_with_inconsistent_snapshot_binding(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    harness = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    snapshot = harness.start(_request(objective, registry))
    receipt = WorkflowStartReceipt.model_validate_json(
        store.resolve(snapshot.receipt_head).path.read_bytes()
    ).model_copy(update={"started_at": NOW + timedelta(seconds=1)})
    changed_receipt = _put(
        store,
        tmp_path,
        name="changed-start-receipt.json",
        payload=_canonical_bytes(receipt),
        schema_version="harness.workflow_start_receipt.v1",
    )
    _replace_persisted_snapshot(
        store,
        _canonical_bytes(snapshot.model_copy(update={"receipt_head": changed_receipt})),
    )

    with pytest.raises(GoldenRunCorruptionError, match="request receipt binding"):
        harness.read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id="tests/golden-e2e",
                workflow_run_id=WORKFLOW_ID,
            )
        )


def test_start_retry_rejects_invalid_or_inconsistent_snapshot_authority(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)

    _replace_persisted_snapshot(store, b"{")
    with pytest.raises(GoldenRunCorruptionError, match="cannot reconstruct"):
        _golden_harness(artifact_store=store).start(request)

    _replace_persisted_snapshot(
        store,
        _canonical_bytes(snapshot.model_copy(update={"revision": 1})),
    )
    with pytest.raises(GoldenRunCorruptionError, match="request receipt binding"):
        _golden_harness(artifact_store=store).start(request)


def test_start_retry_rejects_noncanonical_json_inside_hash_bound_cas(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    state_payload = store.resolve(snapshot.state_ref).path.read_bytes()
    noncanonical_state = _put(
        store,
        tmp_path,
        name="noncanonical-state.json",
        payload=state_payload.replace(b"{", b"{ ", 1),
        schema_version="harness.trusted_world_state.v1",
    )
    receipt = WorkflowStartReceipt.model_validate_json(
        store.resolve(snapshot.receipt_head).path.read_bytes()
    ).model_copy(update={"state_ref": noncanonical_state})
    changed_receipt = _put(
        store,
        tmp_path,
        name="changed-receipt.json",
        payload=_canonical_bytes(receipt),
        schema_version="harness.workflow_start_receipt.v1",
    )
    changed_snapshot = snapshot.model_copy(
        update={
            "state_ref": noncanonical_state,
            "receipt_head": changed_receipt,
        }
    )
    _replace_persisted_snapshot(store, _canonical_bytes(changed_snapshot))

    with pytest.raises(GoldenRunCorruptionError, match="canonical JSON bytes"):
        _golden_harness(artifact_store=store).start(request)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (OBJECTIVE_EVIDENCE.rstrip(b"\n"), "canonical JSON"),
        (
            OBJECTIVE_EVIDENCE.replace(
                b'"source_kind":"user_input"',
                b'"source_kind":"fresh_observation"',
            ),
            "user_input provenance",
        ),
        (
            OBJECTIVE_EVIDENCE.replace(b'"key":"request.objective"', b'"key":"task.objective"'),
            "request namespace",
        ),
    ],
)
def test_start_rejects_untrusted_initial_fact_evidence(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=payload,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)

    with pytest.raises(ValueError, match=message):
        _golden_harness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, registry))


def test_start_rejects_unparseable_registry_snapshot(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _put(
        store,
        tmp_path,
        name="registry.json",
        payload=b"not json at all",
        schema_version="harness.registry_snapshot.v1",
    )

    with pytest.raises(GoldenRunRegistryError, match="registry snapshot") as raised:
        _golden_harness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, registry))
    assert raised.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_start_rejects_a_target_skill_claim_not_bound_by_qualified_descriptor(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry_ref = _qualified_registry(store, tmp_path)
    registry = RegistrySnapshot.model_validate_json(store.resolve(registry_ref).path.read_bytes())
    entry = registry.qualified_skills[0].model_copy(
        update={
            "skill_ref": "text2env.compile@2.0.0",
            "mcp_tool_name": "text2env_compile_v2_0_0",
        }
    )
    forged_registry = registry.model_copy(update={"qualified_skills": (entry,)})
    forged_ref = _put(
        store,
        tmp_path,
        name="forged-registry.json",
        payload=_canonical_bytes(forged_registry),
        schema_version="harness.registry_snapshot.v1",
    )

    with pytest.raises(GoldenRunRegistryError, match="qualification closure") as raised:
        _golden_harness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, forged_ref))
    assert raised.value.code == "HARN_SKILL_UNQUALIFIED"


def test_start_rejects_an_unavailable_registry_or_qualification_artifact(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry_ref = _qualified_registry(store, tmp_path)
    registry_path = store.resolve(registry_ref).path
    registry_path.unlink()
    with pytest.raises(GoldenRunRegistryError) as missing_registry:
        _golden_harness(artifact_store=store).start(_request(objective, registry_ref))
    assert missing_registry.value.code == "HARN_ARTIFACT_UNAVAILABLE"

    registry_ref = _qualified_registry(store, tmp_path)
    registry = _load_registry(store, registry_ref)
    store.resolve(registry.qualified_skills[0].descriptor_ref).path.unlink()
    with pytest.raises(GoldenRunRegistryError) as missing_descriptor:
        _golden_harness(artifact_store=store).start(_request(objective, registry_ref))
    assert missing_descriptor.value.code == "HARN_ARTIFACT_UNAVAILABLE"


def test_start_rejects_invalid_nested_qualification_json(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry_ref = _qualified_registry(store, tmp_path)
    entry = _load_registry(store, registry_ref).qualified_skills[0]
    descriptor = SkillDescriptor.model_validate_json(
        store.resolve(entry.descriptor_ref).path.read_bytes()
    )
    invalid_qualification = _put(
        store,
        tmp_path,
        name="invalid-qualification.json",
        payload=b"not json",
        schema_version="harness.skill_qualification.v1",
    )
    changed_descriptor = descriptor.model_copy(
        update={"qualification_artifact": invalid_qualification}
    )
    changed_descriptor_ref = _put(
        store,
        tmp_path,
        name="changed-descriptor.json",
        payload=_canonical_bytes(changed_descriptor),
        schema_version="harness.skill_descriptor.v1",
    )
    invalid_registry = _publish_registry_entry(
        store,
        tmp_path,
        entry.model_copy(
            update={
                "descriptor_ref": changed_descriptor_ref,
                "qualification_ref": invalid_qualification,
            }
        ),
        name="invalid-nested-registry.json",
    )

    with pytest.raises(GoldenRunRegistryError) as raised:
        _golden_harness(artifact_store=store).start(_request(objective, invalid_registry))
    assert raised.value.code == "HARN_INPUT_SCHEMA_INVALID"


def test_start_rejects_a_qualified_descriptor_with_unknown_schema(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry_ref = _qualified_registry(store, tmp_path)
    entry = _load_registry(store, registry_ref).qualified_skills[0]
    descriptor = SkillDescriptor.model_validate_json(
        store.resolve(entry.descriptor_ref).path.read_bytes()
    ).model_copy(update={"input_schema": "harness.unavailable_input.v1"})
    changed_descriptor_ref = _put(
        store,
        tmp_path,
        name="unknown-schema-descriptor.json",
        payload=_canonical_bytes(descriptor),
        schema_version="harness.skill_descriptor.v1",
    )
    invalid_registry = _publish_registry_entry(
        store,
        tmp_path,
        entry.model_copy(update={"descriptor_ref": changed_descriptor_ref}),
        name="unknown-schema-registry.json",
    )

    with pytest.raises(GoldenRunRegistryError) as raised:
        _golden_harness(artifact_store=store).start(_request(objective, invalid_registry))
    assert raised.value.code == "HARN_DEPENDENCY_DRIFT"


def test_start_rejects_a_non_utc_execution_clock(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)

    with pytest.raises(ValueError, match="clock must return a UTC datetime"):
        _golden_harness(
            artifact_store=store,
            clock=lambda: NOW.astimezone(timezone(timedelta(hours=8))),
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, registry))


def test_start_requires_uuid4_identity_and_rolls_back_failed_reservation(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    request = _request(objective, registry)

    with pytest.raises(ValidationError, match="UUID version 4"):
        _golden_harness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: UUID("10000000-0000-1000-8000-000000000001"),
        ).start(request)

    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    assert snapshot.workflow_run_id == WORKFLOW_ID


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"uri": "file:///tmp/registry.json"}, "content-addressed artifact URI"),
        (
            {"uri": "artifact://sha256/" + "f" * 64},
            "content-addressed artifact URI",
        ),
        ({"media_type": "text/plain"}, "media_type='application/json'"),
        ({"schema_version": "harness.other.v1"}, "schema_version=harness.registry_snapshot.v1"),
    ],
)
def test_start_request_rejects_unbound_registry_refs(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    invalid_registry = registry.model_copy(update=changes)

    with pytest.raises(ValidationError, match=message):
        _request(objective, invalid_registry)


@pytest.mark.parametrize("attack", ["duplicate", "unsorted"])
def test_start_request_rejects_ambiguous_initial_evidence(
    tmp_path: Path,
    attack: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    first = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    second = _put(
        store,
        tmp_path,
        name="detail.json",
        payload=OBJECTIVE_EVIDENCE.replace(b"Place a can", b"Place the can"),
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    ordered = tuple(sorted((first, second), key=lambda item: item.sha256))
    evidence = (first, first) if attack == "duplicate" else tuple(reversed(ordered))

    with pytest.raises(ValidationError, match="sorted and unique"):
        _request(first, registry, initial_fact_evidence=evidence)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {
                "started_at": NOW.astimezone(timezone(timedelta(hours=8))),
                "updated_at": NOW.astimezone(timezone(timedelta(hours=8))),
            },
            "started_at must use UTC",
        ),
        ({"updated_at": NOW - timedelta(seconds=1)}, "updated_at cannot precede started_at"),
    ],
)
def test_run_snapshot_rejects_temporal_attacks(
    tmp_path: Path,
    changes: dict[str, datetime],
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    payload = snapshot.model_dump(mode="python")
    payload.update(changes)

    with pytest.raises(ValidationError, match=message):
        RunSnapshot.model_validate(payload)


def test_run_snapshot_accepts_a_versioned_workflow_operation_receipt_head(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    operation_receipt = snapshot.receipt_head.model_copy(
        update={"schema_version": "harness.workflow_operation_receipt.v1"}
    )
    payload = snapshot.model_dump(mode="python")
    payload.update({"receipt_head": operation_receipt, "revision": 1})

    advanced = RunSnapshot.model_validate(payload)

    assert advanced.receipt_head.schema_version == "harness.workflow_operation_receipt.v1"
    assert advanced.revision == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"uri": "file:///tmp/receipt.json"}, "content-addressed artifact URI"),
        ({"media_type": "text/plain"}, "media_type='application/json'"),
        (
            {"schema_version": "harness.trusted_tool_receipt.v1"},
            "versioned workflow receipt schema",
        ),
        ({"schema_version": None}, "versioned workflow receipt schema"),
    ],
)
def test_run_snapshot_rejects_unbound_receipt_heads(
    tmp_path: Path,
    changes: dict[str, str | None],
    message: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    snapshot = _golden_harness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    payload = snapshot.model_dump(mode="python")
    payload["receipt_head"] = snapshot.receipt_head.model_copy(update=changes)

    with pytest.raises(ValidationError, match=message):
        RunSnapshot.model_validate(payload)
