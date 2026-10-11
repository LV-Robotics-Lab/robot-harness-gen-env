"""Public S1 tracer tests for the Golden Workflow aggregate."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.harness as public_harness
from scene_gen.catalog import AssetCatalog
from self_improving.harness.application import (
    CompileApplicationSettings,
    create_compile_application,
)
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.event_journal import SQLiteEventJournal
from self_improving.harness.events import RunEvent
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
from self_improving.harness.run_store import ControlledRunIntent, SQLiteRunStore
from self_improving.harness.schemas.common import (
    Blocker,
    Event,
    RunState,
    RunStatus,
    SkillDescriptor,
)
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    Text2EnvCompileInput,
)
from self_improving.harness.schemas.workflow import (
    OperationSnapshot,
    RunCommandRequest,
    RunReadRequest,
    RunSnapshot,
    RunStartRequest,
    WorkflowStartReceipt,
)
from self_improving.harness.schemas.workflow_operation import (
    QualifiedSkillExecutionV2,
    StateDeltaV2,
    ToolResultV2,
    TrustedToolReceiptV2,
    WorkflowOperationReceiptV2,
)
from self_improving.harness.system2.domain import TrustedWorldState
from self_improving.harness.trusted_state import build_world_state

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
GENERATED_OBJECTIVE = "Place a purple hexagonal pedestal on the table."
GENERATED_OBJECTIVE_EVIDENCE = (
    b'{"key":"request.objective","observed_at":"2026-09-09T00:00:00Z",'
    b'"schema_version":"harness.world_fact_evidence.v1",'
    b'"source_kind":"user_input","valid_until":null,'
    b'"value":"Place a purple hexagonal pedestal on the table."}\n'
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


def _compact_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


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


def _compile_settings(tmp_path: Path) -> CompileApplicationSettings:
    catalogs = tmp_path / "catalogs"
    external_assets = tmp_path / "external-assets"
    catalogs.mkdir(parents=True)
    external_assets.mkdir(parents=True)
    return CompileApplicationSettings(
        state_root=tmp_path / "compile-state",
        external_catalog_roots=(catalogs,),
        allowed_asset_roots=(external_assets,),
        admission_date=date(2026, 9, 10),
        asset_library_root=tmp_path / "asset-library",
    )


def _empty_catalog(path: Path, *, objects_root: Path) -> None:
    catalog = AssetCatalog(
        robotwin_root=str(objects_root.parent / "RoboTwin"),
        objects_root=str(objects_root),
        entries=(),
    )
    path.write_text(
        json.dumps(
            catalog.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
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


def _frozen_compile_execution(
    store: LocalArtifactStore,
    root: Path,
    registry_ref,
):
    registry = _load_registry(store, registry_ref)
    entry = registry.qualified_skills[0]
    descriptor = SkillDescriptor.model_validate_json(
        store.resolve(entry.descriptor_ref).path.read_bytes()
    )
    execution = QualifiedSkillExecutionV2(
        schema_version="harness.qualified_skill_execution.v2",
        skill_ref=entry.skill_ref,
        descriptor=entry.descriptor_ref,
        input_schema=descriptor.input_schema,
        output_schema=descriptor.output_schema,
        implementation_sha256=descriptor.implementation_sha256,
        qualification_artifact=entry.qualification_ref,
        qualification_report=entry.qualification_report_ref,
        max_attempts=descriptor.max_attempts,
    )
    return _put(
        store,
        root,
        name="qualified-skill-execution.json",
        payload=_canonical_bytes(execution),
        schema_version="harness.qualified_skill_execution.v2",
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


def _read_cas_model(store: LocalArtifactStore, ref, model):
    return model.model_validate_json(store.resolve(ref).path.read_bytes())


def _replace_terminal_operation_and_head(
    workflow_store: SQLiteGoldenRunStore,
    operation: OperationSnapshot,
    snapshot: RunSnapshot,
) -> None:
    operation_payload = _canonical_bytes(operation)
    snapshot_payload = _canonical_bytes(snapshot)
    with closing(sqlite3.connect(workflow_store.path)) as connection, connection:
        connection.execute(
            "UPDATE golden_workflow_operations "
            "SET operation_json = ?, operation_sha256 = ? WHERE operation_id = ?",
            (
                operation_payload,
                sha256(operation_payload).hexdigest(),
                str(operation.operation_id),
            ),
        )
        connection.execute(
            "UPDATE golden_workflow_starts "
            "SET snapshot_json = ?, snapshot_sha256 = ? WHERE workflow_run_id = ?",
            (
                snapshot_payload,
                sha256(snapshot_payload).hexdigest(),
                str(snapshot.workflow_run_id),
            ),
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


def _operation_layout_ddl(
    *,
    operation_id: str = "TEXT NOT NULL PRIMARY KEY",
    workflow_unique: bool = True,
    extra_definitions: tuple[str, ...] = (),
    table_suffix: str = "",
) -> str:
    definitions = [
        f"operation_id {operation_id}",
        "workflow_run_id TEXT NOT NULL",
        "principal_id TEXT NOT NULL",
        "idempotency_key TEXT NOT NULL",
        "command_sha256 TEXT NOT NULL",
        "operation_json BLOB NOT NULL",
        "operation_sha256 TEXT NOT NULL",
        *extra_definitions,
    ]
    if workflow_unique:
        definitions.append("UNIQUE (workflow_run_id, idempotency_key)")
    return (
        "CREATE TABLE golden_workflow_operations "
        f"(\n                {', '.join(definitions)}\n            ){table_suffix}"
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


def _committed_blocked_operation(
    tmp_path: Path,
) -> tuple[
    SQLiteGoldenRunStore,
    OperationSnapshot,
    OperationSnapshot,
    RunSnapshot,
]:
    """Build the smallest store-level terminal transition without invoking a Skill."""

    artifact_store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        artifact_store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    workflow_store = SQLiteGoldenRunStore(tmp_path / "workflow.sqlite3")
    started = _golden_harness(
        artifact_store=artifact_store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="blocked-001",
        expected_revision=0,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head=started.receipt_head,
        skill_ref="text2env.compile@1.0.0",
        parameters={},
    )
    command_ref = _put(
        artifact_store,
        tmp_path,
        name="command.json",
        payload=_canonical_bytes(command),
        schema_version="harness.run_command_request.v1",
    )
    qualified_execution_ref = _frozen_compile_execution(
        artifact_store,
        tmp_path,
        registry,
    )
    operation_id = UUID("20000000-0000-4000-8000-000000000002")
    child_run_id = UUID("30000000-0000-4000-8000-000000000002")
    reserved = workflow_store.reserve_operation(
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key=command.idempotency_key,
        command_sha256=command_ref.sha256,
        expected_revision=started.revision,
        expected_state_sha256=started.state_sha256,
        expected_receipt_sha256=started.receipt_head.sha256,
        factory=lambda snapshot: OperationSnapshot(
            schema_version="harness.operation_snapshot.v1",
            operation_id=operation_id,
            workflow_run_id=snapshot.workflow_run_id,
            principal_id=snapshot.principal_id,
            idempotency_key=command.idempotency_key,
            command=command_ref,
            qualified_execution=qualified_execution_ref,
            skill_ref=command.skill_ref,
            status="running",
            base_revision=snapshot.revision,
            committed_revision=None,
            child_run_id=child_run_id,
            tool_result=None,
            trusted_receipt=None,
            state_delta=None,
            resulting_state=None,
            resulting_state_sha256=None,
            receipt=None,
            started_at=NOW,
            ended_at=None,
        ),
    )
    assert reserved is not None
    running = reserved.operation
    tool_result = _put(
        artifact_store,
        tmp_path,
        name="blocked-tool-result.json",
        payload=b"{}\n",
        schema_version="harness.tool_result.v2",
    )
    trusted_receipt = _put(
        artifact_store,
        tmp_path,
        name="blocked-trusted-receipt.json",
        payload=b"{}\n",
        schema_version="harness.trusted_tool_receipt.v2",
    )
    receipt = _put(
        artifact_store,
        tmp_path,
        name="blocked-workflow-receipt.json",
        payload=b"{}\n",
        schema_version="harness.workflow_operation_receipt.v2",
    )
    ended_at = NOW + timedelta(seconds=1)
    terminal = OperationSnapshot.model_validate(
        running.model_copy(
            update={
                "status": "blocked",
                "committed_revision": 1,
                "tool_result": tool_result,
                "trusted_receipt": trusted_receipt,
                "resulting_state": started.state_ref,
                "resulting_state_sha256": started.state_sha256,
                "receipt": receipt,
                "ended_at": ended_at,
            }
        ).model_dump(mode="python")
    )
    committed_head = RunSnapshot.model_validate(
        started.model_copy(
            update={
                "revision": 1,
                "turn_seq": 1,
                "receipt_head": receipt,
                "active_operation": None,
                "child_runs": (child_run_id,),
                "updated_at": ended_at,
            }
        ).model_dump(mode="python")
    )
    committed = workflow_store.commit_operation(
        principal_id=started.principal_id,
        running=running,
        terminal=terminal,
        snapshot=committed_head,
    )
    assert committed is not None
    return workflow_store, running, terminal, committed_head


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


def _recover_offline_blocked_operation(
    tmp_path: Path,
):

    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put(
        store,
        tmp_path,
        name="objective.json",
        payload=OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    workflow_store = SQLiteGoldenRunStore(tmp_path / "workflow.sqlite3")
    started = _golden_harness(
        artifact_store=store,
        workflow_store=workflow_store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    catalog_path = tmp_path / "catalog.json"
    _empty_catalog(catalog_path, objects_root=tmp_path / "objects")
    catalog = store.put_file(
        catalog_path,
        name="catalog.json",
        media_type="application/json",
        schema_version="robotwin.asset_catalog.v1",
    )
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="offline-terminal-001",
        expected_revision=0,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head=started.receipt_head,
        skill_ref="text2env.compile@1.0.0",
        parameters=Text2EnvCompileInput(
            request="Place a can on a plate.",
            seed=7,
            asset_catalog=catalog,
            config=CompileConfig(generate_missing_assets=False),
        ).model_dump(mode="json"),
    )
    command_ref = _put(
        store,
        tmp_path,
        name="offline-command.json",
        payload=_canonical_bytes(command),
        schema_version="harness.run_command_request.v1",
    )
    qualified_execution = _frozen_compile_execution(store, tmp_path, registry)
    child_run_id = UUID("30000000-0000-4000-8000-000000000007")
    operation_id = UUID("20000000-0000-4000-8000-000000000007")
    reserved = workflow_store.reserve_operation(
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key=command.idempotency_key,
        command_sha256=command_ref.sha256,
        expected_revision=0,
        expected_state_sha256=started.state_sha256,
        expected_receipt_sha256=started.receipt_head.sha256,
        factory=lambda snapshot: OperationSnapshot(
            schema_version="harness.operation_snapshot.v1",
            operation_id=operation_id,
            workflow_run_id=snapshot.workflow_run_id,
            principal_id=snapshot.principal_id,
            idempotency_key=command.idempotency_key,
            command=command_ref,
            qualified_execution=qualified_execution,
            skill_ref=command.skill_ref,
            status="running",
            base_revision=0,
            committed_revision=None,
            child_run_id=child_run_id,
            tool_result=None,
            trusted_receipt=None,
            state_delta=None,
            resulting_state=None,
            resulting_state_sha256=None,
            receipt=None,
            started_at=NOW,
            ended_at=None,
        ),
    )
    assert reserved is not None

    registry_model = _load_registry(store, registry)
    descriptor = SkillDescriptor.model_validate_json(
        store.resolve(registry_model.qualified_skills[0].descriptor_ref).path.read_bytes()
    )
    parameter_sha256 = _compact_sha256(command.parameters)
    child_database = tmp_path / "child.sqlite3"
    child_store = SQLiteRunStore(child_database)
    assert child_store.reserve_controlled_intent(
        ControlledRunIntent(
            run_id=child_run_id,
            skill_id=descriptor.skill_id,
            skill_version=descriptor.version,
            input_schema=descriptor.input_schema,
            phase="validated_input",
            raw_parameters_sha256=parameter_sha256,
            effective_parameters_sha256=parameter_sha256,
            dependencies=None,
            descriptor_sha256=_compact_sha256(descriptor.model_dump(mode="json")),
            implementation_sha256=descriptor.implementation_sha256,
            qualification_sha256=descriptor.qualification_artifact.sha256,
            max_attempts=descriptor.max_attempts,
        )
    )
    ended_at = NOW + timedelta(seconds=1)
    blocker = Blocker(
        code="HARN_DEPENDENCY_UNAVAILABLE",
        message="catalog dependency unavailable",
        stage="preflight",
        retryable=False,
        details={},
        unknowns=(),
        artifact_refs=(),
    )
    events = (
        Event(
            seq=1,
            timestamp=NOW,
            stage="preflight",
            attempt=0,
            from_status=None,
            to_status="running",
            artifact_refs=(),
        ),
        Event(
            seq=2,
            timestamp=ended_at,
            stage="preflight",
            attempt=0,
            from_status="running",
            to_status="blocked",
            artifact_refs=(),
        ),
    )
    state = RunState(
        run_id=child_run_id,
        invocation_digest=None,
        skill_id=descriptor.skill_id,
        skill_version=descriptor.version,
        status="blocked",
        attempt=0,
        max_attempts=0,
        started_at=NOW,
        ended_at=ended_at,
        events=events,
        artifacts=(),
        output=None,
        blocker=blocker,
    )
    journal = SQLiteEventJournal(child_database)
    for event in events:
        journal.publish(
            RunEvent(
                run_id=child_run_id,
                skill_id=descriptor.skill_id,
                skill_version=descriptor.version,
                event=event,
            )
        )
    child_store.put_run_state(state)

    recovered = _golden_harness(
        artifact_store=store,
        workflow_store=workflow_store,
        child_run_store=SQLiteRunStore(child_database),
    ).submit(command)

    durable = workflow_store.read_snapshot(
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
    )
    assert durable is not None
    return store, workflow_store, command, started, recovered, durable.snapshot


def test_submit_recovers_terminal_child_without_live_application_from_frozen_execution(
    tmp_path: Path,
) -> None:
    """A crash after child terminalization can commit from durable frozen authority."""

    _, _, _, started, recovered, durable = _recover_offline_blocked_operation(tmp_path)

    assert recovered.status is RunStatus.BLOCKED
    assert recovered.resulting_state == started.state_ref
    assert recovered.resulting_state_sha256 == started.state_sha256
    assert durable.receipt_head == recovered.receipt


@pytest.mark.parametrize(
    "attack",
    [
        "terminal_time",
        "blocker",
        "supporting_artifacts",
        "registry_snapshot",
        "result_time",
        "workflow_predecessor",
        "workflow_base_revision",
        "operation_ended_at",
        "resulting_state",
    ],
)
def test_read_rejects_rehashed_but_uncorrelated_operation_evidence(
    tmp_path: Path,
    attack: str,
) -> None:
    store, workflow_store, _, started, operation, snapshot = _recover_offline_blocked_operation(
        tmp_path
    )
    assert operation.trusted_receipt is not None
    assert operation.tool_result is not None
    assert operation.receipt is not None
    tool_receipt = _read_cas_model(
        store,
        operation.trusted_receipt,
        TrustedToolReceiptV2,
    )
    result = _read_cas_model(store, operation.tool_result, ToolResultV2)
    workflow_receipt = _read_cas_model(
        store,
        operation.receipt,
        WorkflowOperationReceiptV2,
    )
    tool_updates: dict[str, object] = {}
    result_updates: dict[str, object] = {}
    receipt_updates: dict[str, object] = {}
    operation_updates: dict[str, object] = {}
    snapshot_updates: dict[str, object] = {}
    forged_time = NOW + timedelta(milliseconds=500)
    if attack == "terminal_time":
        tool_updates["ended_at"] = forged_time
        result_updates["ended_at"] = forged_time
        receipt_updates["committed_at"] = forged_time
        operation_updates["ended_at"] = forged_time
        snapshot_updates["updated_at"] = forged_time
    elif attack == "blocker":
        forged_blocker = tool_receipt.blocker.model_copy(
            update={"message": "coherently rehashed forged blocker"}
        )
        tool_updates["blocker"] = forged_blocker
        result_updates["blocker"] = forged_blocker
    elif attack == "supporting_artifacts":
        supporting = _put(
            store,
            tmp_path,
            name="forged-supporting.json",
            payload=b"{}\n",
            schema_version="harness.diagnostic.v1",
        )
        tool_updates["supporting_artifacts"] = (supporting,)
        result_updates["diagnostics"] = (supporting,)
    elif attack == "registry_snapshot":
        forged_registry = _put(
            store,
            tmp_path,
            name="forged-valid-registry.json",
            payload=_canonical_bytes(
                RegistrySnapshot(
                    schema_version="harness.registry_snapshot.v1",
                    qualified_skills=(),
                )
            ),
            schema_version="harness.registry_snapshot.v1",
        )
        tool_updates["registry_snapshot"] = forged_registry
    elif attack == "result_time":
        result_updates["started_at"] = forged_time
    elif attack == "workflow_predecessor":
        receipt_updates["predecessor_receipt"] = _put(
            store,
            tmp_path,
            name="forged-predecessor.json",
            payload=b"{}\n",
            schema_version="harness.workflow_start_receipt.v1",
        )
    elif attack == "workflow_base_revision":
        receipt_updates.update({"base_revision": 1, "committed_revision": 2})
    elif attack == "operation_ended_at":
        operation_updates["ended_at"] = forged_time
        snapshot_updates["updated_at"] = forged_time
    else:
        base_state = _read_cas_model(store, started.state_ref, TrustedWorldState)
        forged_state = build_world_state(
            base_state.facts,
            as_of=NOW + timedelta(seconds=2),
        )
        forged_state_ref = _put(
            store,
            tmp_path,
            name="forged-resulting-state.json",
            payload=_canonical_bytes(forged_state),
            schema_version="harness.trusted_world_state.v1",
        )
        receipt_updates.update(
            {
                "resulting_state": forged_state_ref,
                "resulting_state_sha256": forged_state.state_sha256,
            }
        )
        operation_updates.update(
            {
                "resulting_state": forged_state_ref,
                "resulting_state_sha256": forged_state.state_sha256,
            }
        )
        snapshot_updates.update(
            {
                "state_ref": forged_state_ref,
                "state_sha256": forged_state.state_sha256,
            }
        )

    tool_receipt = TrustedToolReceiptV2.model_validate(
        tool_receipt.model_copy(update=tool_updates).model_dump(mode="python")
    )
    tool_ref = _put(
        store,
        tmp_path,
        name="forged-tool-receipt.json",
        payload=_canonical_bytes(tool_receipt),
        schema_version="harness.trusted_tool_receipt.v2",
    )
    result_updates["trusted_receipt"] = tool_ref
    result = ToolResultV2.model_validate(
        result.model_copy(update=result_updates).model_dump(mode="python")
    )
    result_ref = _put(
        store,
        tmp_path,
        name="forged-tool-result.json",
        payload=_canonical_bytes(result),
        schema_version="harness.tool_result.v2",
    )
    receipt_updates.update({"tool_receipt": tool_ref, "tool_result": result_ref})
    workflow_receipt = WorkflowOperationReceiptV2.model_validate(
        workflow_receipt.model_copy(update=receipt_updates).model_dump(mode="python")
    )
    receipt_ref = _put(
        store,
        tmp_path,
        name="forged-workflow-receipt.json",
        payload=_canonical_bytes(workflow_receipt),
        schema_version="harness.workflow_operation_receipt.v2",
    )
    operation_updates.update(
        {
            "trusted_receipt": tool_ref,
            "tool_result": result_ref,
            "receipt": receipt_ref,
        }
    )
    forged_operation = OperationSnapshot.model_validate(
        operation.model_copy(update=operation_updates).model_dump(mode="python")
    )
    snapshot_updates["receipt_head"] = receipt_ref
    forged_snapshot = RunSnapshot.model_validate(
        snapshot.model_copy(update=snapshot_updates).model_dump(mode="python")
    )
    _replace_terminal_operation_and_head(
        workflow_store,
        forged_operation,
        forged_snapshot,
    )

    with pytest.raises(GoldenRunCorruptionError):
        _golden_harness(
            artifact_store=store,
            workflow_store=workflow_store,
        ).read(
            RunReadRequest(
                schema_version="harness.run_read_request.v1",
                principal_id=started.principal_id,
                workflow_run_id=started.workflow_run_id,
            )
        )


def test_submit_exact_qualified_compile_commits_one_operation_and_state_delta(
    tmp_path: Path,
) -> None:
    """One real qualified compile advances the durable aggregate exactly once."""

    assert set(RunCommandRequest.model_fields) == {
        "schema_version",
        "principal_id",
        "workflow_run_id",
        "idempotency_key",
        "expected_revision",
        "expected_state_sha256",
        "expected_receipt_head",
        "skill_ref",
        "parameters",
    }
    assert set(OperationSnapshot.model_fields) == {
        "schema_version",
        "operation_id",
        "workflow_run_id",
        "principal_id",
        "idempotency_key",
        "command",
        "qualified_execution",
        "skill_ref",
        "status",
        "base_revision",
        "committed_revision",
        "child_run_id",
        "tool_result",
        "trusted_receipt",
        "state_delta",
        "resulting_state",
        "resulting_state_sha256",
        "receipt",
        "started_at",
        "ended_at",
    }
    assert "planner_call_id" not in TrustedToolReceiptV2.model_fields
    assert StateDeltaV2.model_fields["receipt"].is_required()
    assert ToolResultV2.model_fields["state_delta"].is_required()

    settings = _compile_settings(tmp_path)
    application = create_compile_application(settings)
    store = LocalArtifactStore(application.artifact_root)
    objective = _put(
        store,
        tmp_path,
        name="generated-objective.json",
        payload=GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(store, tmp_path)
    catalog_path = settings.external_catalog_roots[0] / "empty.json"
    _empty_catalog(catalog_path, objects_root=settings.allowed_asset_roots[0] / "objects")
    catalog = application.snapshot_asset_catalog(catalog_path)
    operation_id = UUID("20000000-0000-4000-8000-000000000001")
    child_run_id = UUID("30000000-0000-4000-8000-000000000001")
    workflow_store = SQLiteGoldenRunStore(tmp_path / "golden.sqlite3")
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        compile_application=application,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
        operation_id_factory=lambda: operation_id,
        child_run_id_factory=lambda: child_run_id,
    )
    started = harness.start(_request(objective, registry))
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="compile-001",
        expected_revision=started.revision,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head=started.receipt_head,
        skill_ref="text2env.compile@1.0.0",
        parameters=Text2EnvCompileInput(
            request=GENERATED_OBJECTIVE,
            seed=77,
            asset_catalog=catalog,
            config=CompileConfig(generate_missing_assets=True),
        ).model_dump(mode="json"),
    )

    operation = harness.submit(command)

    assert operation.operation_id == operation_id
    assert operation.child_run_id == child_run_id
    assert operation.status is RunStatus.SUCCEEDED
    assert operation.base_revision == 0
    assert operation.committed_revision == 1
    assert operation.receipt is not None
    assert operation.tool_result is not None
    assert operation.trusted_receipt is not None
    assert operation.state_delta is not None
    receipt = WorkflowOperationReceiptV2.model_validate_json(
        store.resolve(operation.receipt).path.read_bytes()
    )
    result = ToolResultV2.model_validate_json(
        store.resolve(operation.tool_result).path.read_bytes()
    )
    tool_receipt = TrustedToolReceiptV2.model_validate_json(
        store.resolve(operation.trusted_receipt).path.read_bytes()
    )
    delta = StateDeltaV2.model_validate_json(store.resolve(operation.state_delta).path.read_bytes())
    assert receipt.command == operation.command
    assert receipt.predecessor_receipt == started.receipt_head
    assert receipt.tool_receipt == operation.trusted_receipt
    assert receipt.resulting_state_sha256 != started.state_sha256
    assert tool_receipt.registry_snapshot == registry
    assert tool_receipt.skill.skill_ref == "text2env.compile@1.0.0"
    assert tool_receipt.child_run_id == child_run_id
    assert result.trusted_receipt == operation.trusted_receipt
    assert result.state_delta == delta
    assert delta.receipt == operation.trusted_receipt
    assert tuple(mutation.key for mutation in delta.mutations) == (
        "assets.catalog",
        "environment.package",
    )
    current = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=started.principal_id,
            workflow_run_id=started.workflow_run_id,
        )
    )
    assert current.revision == 1
    assert current.active_operation is None
    assert current.child_runs == (child_run_id,)
    assert current.receipt_head == operation.receipt
    assert (
        TrustedWorldState.model_validate_json(
            store.resolve(current.state_ref).path.read_bytes()
        ).version
        == 2
    )
    assert application.run_state(child_run_id) is not None

    recovered_application = create_compile_application(settings)
    recovered = GoldenRunHarness(
        artifact_store=LocalArtifactStore(recovered_application.artifact_root),
        workflow_store=SQLiteGoldenRunStore(tmp_path / "golden.sqlite3"),
        compile_application=recovered_application,
    ).submit(command)
    assert recovered == operation


def test_terminal_commit_retry_rechecks_the_exact_committed_workflow_head(
    tmp_path: Path,
) -> None:
    workflow_store, running, terminal, committed_head = _committed_blocked_operation(tmp_path)
    attacked_head = RunSnapshot.model_validate(
        committed_head.model_copy(
            update={"updated_at": committed_head.updated_at + timedelta(seconds=1)}
        ).model_dump(mode="python")
    )
    attacked_payload = _canonical_bytes(attacked_head)
    with closing(sqlite3.connect(workflow_store.path)) as connection, connection:
        connection.execute(
            """
            UPDATE golden_workflow_starts
            SET snapshot_json = ?, snapshot_sha256 = ?
            WHERE workflow_run_id = ?
            """,
            (
                attacked_payload,
                sha256(attacked_payload).hexdigest(),
                str(running.workflow_run_id),
            ),
        )

    with pytest.raises(
        GoldenRunCorruptionError,
        match="terminal workflow head|exact operation transition",
    ):
        workflow_store.commit_operation(
            principal_id=running.principal_id,
            running=running,
            terminal=terminal,
            snapshot=committed_head,
        )


def test_submit_binds_a_compile_preflight_block_to_the_reserved_child(
    tmp_path: Path,
) -> None:
    settings = _compile_settings(tmp_path)
    application = create_compile_application(settings)
    artifact_store = LocalArtifactStore(application.artifact_root)
    objective = _put(
        artifact_store,
        tmp_path,
        name="blocked-objective.json",
        payload=GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry = _qualified_registry(artifact_store, tmp_path)
    invalid_catalog_path = settings.external_catalog_roots[0] / "invalid.json"
    invalid_catalog_path.write_bytes(b"{}\n")
    invalid_catalog = application.snapshot_asset_catalog(invalid_catalog_path)
    child_run_id = UUID("30000000-0000-4000-8000-000000000003")
    harness = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "blocked.sqlite3"),
        compile_application=application,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
        operation_id_factory=lambda: UUID("20000000-0000-4000-8000-000000000003"),
        child_run_id_factory=lambda: child_run_id,
    )
    started = harness.start(_request(objective, registry))
    command = RunCommandRequest(
        schema_version="harness.run_command_request.v1",
        principal_id=started.principal_id,
        workflow_run_id=started.workflow_run_id,
        idempotency_key="compile-blocked-001",
        expected_revision=started.revision,
        expected_state_sha256=started.state_sha256,
        expected_receipt_head=started.receipt_head,
        skill_ref="text2env.compile@1.0.0",
        parameters={
            "request": GENERATED_OBJECTIVE,
            "seed": 77,
            "asset_catalog": invalid_catalog.model_dump(mode="json"),
            "config": {},
        },
    )

    operation = harness.submit(command)

    assert operation.status is RunStatus.BLOCKED
    assert operation.child_run_id == child_run_id
    assert operation.state_delta is None
    state = application.run_state(child_run_id)
    assert state is not None
    assert state.run_id == child_run_id
    assert state.attempt == 0
    assert state.blocker is not None
    assert state.blocker.code == "HARN_DEPENDENCY_UNAVAILABLE"
    assert application.invocation(child_run_id) is None
    current = harness.read(
        RunReadRequest(
            schema_version="harness.run_read_request.v1",
            principal_id=started.principal_id,
            workflow_run_id=started.workflow_run_id,
        )
    )
    assert current.revision == 1
    assert current.state_sha256 == started.state_sha256
    assert current.state_ref == started.state_ref
    offline = GoldenRunHarness(
        artifact_store=artifact_store,
        workflow_store=SQLiteGoldenRunStore(tmp_path / "blocked.sqlite3"),
    ).submit(command)
    assert offline == operation


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


def test_golden_workflow_contracts_are_available_from_the_public_harness_facade() -> None:
    assert public_harness.GoldenRunHarness is GoldenRunHarness
    assert public_harness.GoldenRunNotFoundError is GoldenRunNotFoundError
    assert public_harness.GoldenRunConflictError is GoldenRunConflictError
    assert public_harness.GoldenRunCorruptionError is GoldenRunCorruptionError
    assert public_harness.GoldenRunRegistryError is GoldenRunRegistryError
    assert public_harness.RunReadRequest is RunReadRequest
    assert public_harness.RunCommandRequest is RunCommandRequest
    assert public_harness.OperationSnapshot is OperationSnapshot
    assert public_harness.QualifiedSkillExecutionV2 is QualifiedSkillExecutionV2
    assert public_harness.RunStartRequest is RunStartRequest
    assert public_harness.RunSnapshot is RunSnapshot
    assert public_harness.RegistrySnapshot is RegistrySnapshot
    assert public_harness.SQLiteGoldenRunStore is SQLiteGoldenRunStore
    assert public_harness.WorkflowStartReceipt is WorkflowStartReceipt
    assert public_harness.StateDeltaV2 is StateDeltaV2
    assert public_harness.ToolResultV2 is ToolResultV2
    assert public_harness.TrustedToolReceiptV2 is TrustedToolReceiptV2
    assert public_harness.WorkflowOperationReceiptV2 is WorkflowOperationReceiptV2


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

from self_improving.harness import (
    GoldenRunHarness,
    LocalArtifactStore,
    RunReadRequest,
    SQLiteGoldenRunStore,
)

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
        _operation_layout_ddl(operation_id="TEXT"),
        _operation_layout_ddl(workflow_unique=False),
        _operation_layout_ddl(operation_id="TEXT NOT NULL PRIMARY KEY ON CONFLICT REPLACE"),
        _operation_layout_ddl(table_suffix=" STRICT"),
        _operation_layout_ddl(extra_definitions=("shadow TEXT",)),
    ],
    ids=("constraints", "unique", "conflict-policy", "strict", "extra-column"),
)
def test_sqlite_golden_run_store_rejects_forged_operation_layout(
    tmp_path: Path,
    ddl: str,
) -> None:
    database = tmp_path / "forged-operation.sqlite3"
    SQLiteGoldenRunStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE golden_workflow_operations")
        connection.execute(ddl)

    with pytest.raises(GoldenRunCorruptionError, match="operation database.*layout"):
        SQLiteGoldenRunStore(database)


def test_sqlite_golden_run_store_rejects_forged_operation_indexes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "forged-operation-index.sqlite3"
    SQLiteGoldenRunStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE UNIQUE INDEX forged_operation_index "
            "ON golden_workflow_operations (principal_id)"
        )

    with pytest.raises(GoldenRunCorruptionError, match="operation database.*layout"):
        SQLiteGoldenRunStore(database)


@pytest.mark.parametrize("event", ["INSERT", "UPDATE", "DELETE"])
def test_operation_access_rejects_late_authority_triggers(
    tmp_path: Path,
    event: str,
) -> None:
    database = tmp_path / "late-operation-trigger.sqlite3"
    store = SQLiteGoldenRunStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            f"CREATE TRIGGER late_operation_trigger AFTER {event} "
            "ON golden_workflow_operations BEGIN SELECT 1; END"
        )

    with pytest.raises(GoldenRunCorruptionError, match="operation database.*layout"):
        store.read_operation(
            principal_id="tests/golden-e2e",
            workflow_run_id=WORKFLOW_ID,
            idempotency_key="late-trigger",
        )


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
