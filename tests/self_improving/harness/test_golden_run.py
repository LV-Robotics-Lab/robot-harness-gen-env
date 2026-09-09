"""Public S1 tracer tests for the Golden Workflow aggregate."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import self_improving.harness as public_harness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_run import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHarness,
    GoldenRunRegistryError,
)
from self_improving.harness.handlers.text2env_compile import text2env_compile_descriptor
from self_improving.harness.qualification import ImplementationManifestV1
from self_improving.harness.schemas.common import SkillDescriptor
from self_improving.harness.schemas.registry_snapshot import (
    QualifiedSkillSnapshot,
    RegistrySnapshot,
)
from self_improving.harness.schemas.workflow import (
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
    return RegistrySnapshot.model_validate_json(
        store.resolve(registry_ref).path.read_bytes()
    )


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
            "UPDATE golden_workflow_starts SET snapshot_json = ?",
            (payload,),
        )


class _ArtifactStoreLookalike:
    def __init__(self, root: Path) -> None:
        self.root = root


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
    harness = GoldenRunHarness(
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
        GoldenRunHarness(artifact_store=_ArtifactStoreLookalike(tmp_path))  # type: ignore[arg-type]


def test_golden_workflow_contracts_are_available_from_the_public_harness_facade() -> None:
    assert public_harness.GoldenRunHarness is GoldenRunHarness
    assert public_harness.GoldenRunConflictError is GoldenRunConflictError
    assert public_harness.GoldenRunCorruptionError is GoldenRunCorruptionError
    assert public_harness.GoldenRunRegistryError is GoldenRunRegistryError
    assert public_harness.RunStartRequest is RunStartRequest
    assert public_harness.RunSnapshot is RunSnapshot
    assert public_harness.RegistrySnapshot is RegistrySnapshot
    assert public_harness.WorkflowStartReceipt is WorkflowStartReceipt


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
    first = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)

    def unexpected_clock() -> datetime:
        pytest.fail("an idempotent retry must not read the execution clock")

    def unexpected_workflow_id() -> UUID:
        pytest.fail("an idempotent retry must not allocate another workflow identity")

    retry = GoldenRunHarness(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        clock=unexpected_clock,
        workflow_id_factory=unexpected_workflow_id,
    ).start(request)

    assert retry == first


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
    harness = GoldenRunHarness(
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
    harness = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )
    harness.start(request)
    database = store.root / "golden-workflows.sqlite3"
    with closing(sqlite3.connect(database)) as connection, connection:
        payload = connection.execute(
            "SELECT snapshot_json FROM golden_workflow_starts"
        ).fetchone()[0]
        connection.execute(
            "UPDATE golden_workflow_starts SET snapshot_json = ?",
            (payload.replace(b"{", b"{ ", 1),),
        )

    with pytest.raises(GoldenRunCorruptionError, match="canonical snapshot bytes"):
        GoldenRunHarness(artifact_store=store).start(request)


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
    harness = GoldenRunHarness(
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
            SELECT snapshot_json FROM golden_workflow_starts
            WHERE idempotency_key = 'start-text-002'
            """
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE golden_workflow_starts SET snapshot_json = ?
            WHERE idempotency_key = 'start-text-001'
            """,
            (swapped,),
        )

    with pytest.raises(GoldenRunCorruptionError, match="request receipt binding"):
        GoldenRunHarness(artifact_store=store).start(first_request)


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
    snapshot = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)
    store.resolve(snapshot.state_ref).path.write_bytes(b'{"tampered":true}\n')

    with pytest.raises(GoldenRunCorruptionError, match="CAS closure"):
        GoldenRunHarness(artifact_store=store).start(request)


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
    snapshot = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(request)

    _replace_persisted_snapshot(store, b"{")
    with pytest.raises(GoldenRunCorruptionError, match="cannot reconstruct"):
        GoldenRunHarness(artifact_store=store).start(request)

    _replace_persisted_snapshot(
        store,
        _canonical_bytes(snapshot.model_copy(update={"revision": 1})),
    )
    with pytest.raises(GoldenRunCorruptionError, match="request receipt binding"):
        GoldenRunHarness(artifact_store=store).start(request)


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
    snapshot = GoldenRunHarness(
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
        GoldenRunHarness(artifact_store=store).start(request)


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
        GoldenRunHarness(
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
        GoldenRunHarness(
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
    registry = RegistrySnapshot.model_validate_json(
        store.resolve(registry_ref).path.read_bytes()
    )
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
        GoldenRunHarness(
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
        GoldenRunHarness(artifact_store=store).start(_request(objective, registry_ref))
    assert missing_registry.value.code == "HARN_ARTIFACT_UNAVAILABLE"

    registry_ref = _qualified_registry(store, tmp_path)
    registry = _load_registry(store, registry_ref)
    store.resolve(registry.qualified_skills[0].descriptor_ref).path.unlink()
    with pytest.raises(GoldenRunRegistryError) as missing_descriptor:
        GoldenRunHarness(artifact_store=store).start(_request(objective, registry_ref))
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
        GoldenRunHarness(artifact_store=store).start(_request(objective, invalid_registry))
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
        GoldenRunHarness(artifact_store=store).start(_request(objective, invalid_registry))
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
        GoldenRunHarness(
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
        GoldenRunHarness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: UUID(
                "10000000-0000-1000-8000-000000000001"
            ),
        ).start(request)

    snapshot = GoldenRunHarness(
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
    snapshot = GoldenRunHarness(
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
    snapshot = GoldenRunHarness(
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
    snapshot = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    payload = snapshot.model_dump(mode="python")
    payload["receipt_head"] = snapshot.receipt_head.model_copy(update=changes)

    with pytest.raises(ValidationError, match=message):
        RunSnapshot.model_validate(payload)
