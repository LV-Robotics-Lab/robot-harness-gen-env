"""Public S1 tracer tests for the Golden Workflow aggregate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_run import GoldenRunHarness
from self_improving.harness.schemas.workflow import RunSnapshot, RunStartRequest

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
WORKFLOW_ID = UUID("10000000-0000-4000-8000-000000000001")
OBJECTIVE_EVIDENCE = (
    b'{"key":"request.objective","observed_at":"2026-09-09T00:00:00Z",'
    b'"schema_version":"harness.world_fact_evidence.v1",'
    b'"source_kind":"user_input","valid_until":null,'
    b'"value":"Place a can on a plate."}\n'
)
REGISTRY_SNAPSHOT = (
    b'{"qualified_skill_refs":["text2env.compile@2.0.0"],'
    b'"schema_version":"harness.registry_snapshot.v1",'
    b'"snapshot_id":"registry-20260909"}\n'
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


def test_start_builds_trusted_state_and_receipt_chain_from_cas_inputs(tmp_path: Path) -> None:
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
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )
    harness = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    )

    snapshot = harness.start(_request(objective, registry))

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
    assert snapshot.registry_snapshot == registry
    assert snapshot.receipt_head.sha256 == (
        "75a6a58929964c2aa7aba3c1c0bdda208aa36f7a21e17b8b3fafbbb43a647929"
    )
    assert snapshot.receipt_head.schema_version == "harness.workflow_start_receipt.v1"
    assert snapshot.active_operation is None
    assert snapshot.child_runs == ()
    assert snapshot.started_at == NOW
    assert snapshot.updated_at == NOW


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
    registry = _put(
        store,
        tmp_path,
        name="registry.json",
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )

    with pytest.raises(ValueError, match=message):
        GoldenRunHarness(
            artifact_store=store,
            clock=lambda: NOW,
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, registry))


def test_start_rejects_a_non_utc_execution_clock(tmp_path: Path) -> None:
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
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )

    with pytest.raises(ValueError, match="clock must return a UTC datetime"):
        GoldenRunHarness(
            artifact_store=store,
            clock=lambda: NOW.astimezone(timezone(timedelta(hours=8))),
            workflow_id_factory=lambda: WORKFLOW_ID,
        ).start(_request(objective, registry))


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
    registry = _put(
        store,
        tmp_path,
        name="registry.json",
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )
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
    registry = _put(
        store,
        tmp_path,
        name="registry.json",
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )
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
    registry = _put(
        store,
        tmp_path,
        name="registry.json",
        payload=REGISTRY_SNAPSHOT,
        schema_version="harness.registry_snapshot.v1",
    )
    snapshot = GoldenRunHarness(
        artifact_store=store,
        clock=lambda: NOW,
        workflow_id_factory=lambda: WORKFLOW_ID,
    ).start(_request(objective, registry))
    payload = snapshot.model_dump(mode="python")
    payload.update(changes)

    with pytest.raises(ValidationError, match=message):
        RunSnapshot.model_validate(payload)
