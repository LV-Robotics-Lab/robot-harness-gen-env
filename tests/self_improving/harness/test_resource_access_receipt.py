"""RED contracts for durable, authority-bound MCP resource delivery receipts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.golden_run import (
    GoldenRunHarness,
    GoldenRunNotFoundError,
)
from self_improving.harness.golden_store import (
    GoldenRunConflictError,
    GoldenRunCorruptionError,
    GoldenRunHeadConflictError,
    SQLiteGoldenRunStore,
)
from self_improving.harness.mcp_adapter import (
    HarnessMCPAdapterError,
    HarnessMCPProtocolError,
    MCPResourceRead,
    create_harness_mcp_adapter,
)
from self_improving.harness.schemas.golden_control import AdvisoryRecordCommand
from self_improving.harness.schemas.registry_snapshot import RegistrySnapshot
from self_improving.harness.schemas.resource_access import (
    RESOURCE_ACCESS_RECEIPT_SCHEMA_ID,
    ResourceAccessReceipt,
    resource_access_receipt_sha256,
    resource_access_request_sha256,
)
from self_improving.harness.schemas.workflow import RunReadRequest, RunSnapshot
from tests.self_improving.harness import test_golden_control_submit as control
from tests.self_improving.harness import test_golden_run as base

SERVED_AT = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
ACCESS_ID = UUID("40000000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class _Case:
    root: Path
    database: Path
    store: LocalArtifactStore
    workflow_store: SQLiteGoldenRunStore
    harness: GoldenRunHarness
    snapshot: RunSnapshot
    resource_uri: str


def _canonical_bytes(value: object) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
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


def _put_json(
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


def _case(tmp_path: Path) -> _Case:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = LocalArtifactStore(tmp_path / "cas")
    objective = _put_json(
        store,
        tmp_path,
        name="objective.json",
        payload=base.GENERATED_OBJECTIVE_EVIDENCE,
        schema_version="harness.world_fact_evidence.v1",
    )
    registry_model = RegistrySnapshot(
        schema_version="harness.registry_snapshot.v1",
        qualified_skills=(),
    )
    registry = _put_json(
        store,
        tmp_path,
        name="registry.json",
        payload=_canonical_bytes(registry_model),
        schema_version="harness.registry_snapshot.v1",
    )
    database = tmp_path / "golden.sqlite3"
    workflow_store = SQLiteGoldenRunStore(database)
    harness = GoldenRunHarness(
        artifact_store=store,
        workflow_store=workflow_store,
        clock=lambda: SERVED_AT,
        workflow_id_factory=lambda: base.WORKFLOW_ID,
        resource_access_id_factory=lambda: ACCESS_ID,
    )
    snapshot = harness.start(base._request(objective, registry))
    return _Case(
        root=tmp_path,
        database=database,
        store=store,
        workflow_store=workflow_store,
        harness=harness,
        snapshot=snapshot,
        resource_uri=(
            f"harness://workflows/{snapshot.workflow_run_id}/artifacts/{objective.sha256}"
        ),
    )


def _request(snapshot: RunSnapshot, *, principal_id: str | None = None) -> RunReadRequest:
    return RunReadRequest(
        schema_version="harness.run_read_request.v1",
        principal_id=principal_id or snapshot.principal_id,
        workflow_run_id=snapshot.workflow_run_id,
    )


def _adapter(case: _Case, *, principal_id: str | None = None):
    return create_harness_mcp_adapter(
        harness=case.harness,
        principal_id=principal_id or case.snapshot.principal_id,
        registry_snapshot=case.snapshot.registry_snapshot,
    )


def _row_count(database: Path, table: str) -> int:
    assert table in {
        "golden_workflow_resource_accesses",
        "golden_workflow_turns",
    }
    with sqlite3.connect(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_artifact_resource_returns_exact_bytes_and_a_bounded_receipt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    before = case.harness.read(_request(case.snapshot))

    result = _adapter(case).read_resource(MCPResourceRead(uri=case.resource_uri))

    assert result.text is not None
    assert result.text.encode("utf-8") == base.GENERATED_OBJECTIVE_EVIDENCE
    assert result.blob is None
    assert result.resource.sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    receipt_payload = result.access_receipt_text.encode("utf-8")
    assert hashlib.sha256(receipt_payload).hexdigest() == result.access_receipt.sha256
    assert result.access_receipt.schema_version == RESOURCE_ACCESS_RECEIPT_SCHEMA_ID
    assert result.access_receipt_uri == (
        f"harness://workflows/{case.snapshot.workflow_run_id}/resource-access/"
        f"{result.access_receipt.sha256}"
    )

    receipt = ResourceAccessReceipt.model_validate_json(receipt_payload)
    assert receipt.schema_version == RESOURCE_ACCESS_RECEIPT_SCHEMA_ID
    assert receipt.access_id == ACCESS_ID
    assert receipt.principal_id == case.snapshot.principal_id
    assert receipt.workflow_run_id == case.snapshot.workflow_run_id
    assert receipt.resource_uri == case.resource_uri
    assert receipt.resource == result.resource
    assert receipt.delivered_sha256 == result.resource.sha256
    assert receipt.delivered_bytes == result.resource.bytes
    assert receipt.representation == "text_utf8"
    assert receipt.observed_snapshot == before
    assert receipt.observed_snapshot_sha256 == hashlib.sha256(_canonical_bytes(before)).hexdigest()
    assert receipt.observed_snapshot.active_operation == before.active_operation
    assert receipt.served_at == SERVED_AT
    assert receipt.delivery_scope == "server_response_payload"
    assert receipt.transport_acknowledged is False
    assert receipt.model_understanding_claimed is False
    assert receipt.receipt_sha256 == resource_access_receipt_sha256(receipt)

    after = case.harness.read(_request(case.snapshot))
    assert after == before
    assert _row_count(case.database, "golden_workflow_resource_accesses") == 1
    assert _row_count(case.database, "golden_workflow_turns") == 0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"delivered_sha256": "f" * 64}, "delivered_sha256"),
        ({"delivered_bytes": 999_999}, "delivered_bytes"),
        ({"observed_snapshot_sha256": "f" * 64}, "observed_snapshot_sha256"),
        ({"principal_id": "foreign-principal"}, "principal"),
        ({"workflow_run_id": UUID("10000000-0000-4000-8000-000000000099")}, "workflow"),
        ({"resource_uri": "artifact://sha256/" + "f" * 64}, "workflow-scoped"),
        (
            {
                "resource_uri": (
                    "harness://workflows/10000000-0000-4000-8000-000000000001/artifacts/" + "f" * 64
                )
            },
            "differs from workflow or resource",
        ),
        ({"representation": "blob_base64"}, "representation"),
        (
            {"served_at": datetime.fromisoformat("2026-09-11T09:00:00+01:00")},
            "served_at must use UTC",
        ),
        (
            {"served_at": datetime(2026, 9, 11, 7, 59, 59, tzinfo=timezone.utc)},
            "predates",
        ),
        ({"receipt_sha256": "f" * 64}, "receipt_sha256"),
        ({"delivery_scope": "model_context"}, "delivery_scope"),
        ({"transport_acknowledged": True}, "transport_acknowledged"),
        ({"transport_acknowledged": 0}, "transport_acknowledged"),
        ({"model_understanding_claimed": True}, "model_understanding_claimed"),
    ],
)
def test_resource_access_receipt_rejects_unbound_or_overclaimed_values(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    case = _case(tmp_path)
    result = _adapter(case).read_resource(MCPResourceRead(uri=case.resource_uri))
    values = ResourceAccessReceipt.model_validate_json(result.access_receipt_text).model_dump(
        mode="python"
    )
    values.update(changes)

    with pytest.raises(ValidationError, match=message):
        ResourceAccessReceipt.model_validate(values)


def test_resource_access_receipt_rejects_an_observed_snapshot_from_another_workflow(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    result = _adapter(case).read_resource(MCPResourceRead(uri=case.resource_uri))
    values = ResourceAccessReceipt.model_validate_json(result.access_receipt_text).model_dump(
        mode="python"
    )
    observed = dict(values["observed_snapshot"])
    observed["workflow_run_id"] = UUID("10000000-0000-4000-8000-000000000099")
    values["observed_snapshot"] = observed

    with pytest.raises(ValidationError, match="workflow differs from observed snapshot"):
        ResourceAccessReceipt.model_validate(values)


def test_same_snapshot_read_and_restart_reuse_one_append_only_receipt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    adapter = _adapter(case)
    first = adapter.read_resource(MCPResourceRead(uri=case.resource_uri))
    first_snapshot = case.harness.read(_request(case.snapshot))
    second = adapter.read_resource(MCPResourceRead(uri=case.resource_uri))

    assert second == first
    assert case.harness.read(_request(case.snapshot)) == first_snapshot
    assert _row_count(case.database, "golden_workflow_resource_accesses") == 1
    assert _row_count(case.database, "golden_workflow_turns") == 0

    def unexpected_factory_call():
        raise AssertionError("stable resource access replay must not call server factories")

    reopened_harness = GoldenRunHarness(
        artifact_store=case.store,
        workflow_store=SQLiteGoldenRunStore(case.database),
        clock=unexpected_factory_call,
        resource_access_id_factory=unexpected_factory_call,
    )
    reopened = create_harness_mcp_adapter(
        harness=reopened_harness,
        principal_id=case.snapshot.principal_id,
        registry_snapshot=case.snapshot.registry_snapshot,
    )

    assert reopened.read_resource(MCPResourceRead(uri=case.resource_uri)) == first
    assert _row_count(case.database, "golden_workflow_resource_accesses") == 1
    assert _row_count(case.database, "golden_workflow_turns") == 0


def test_foreign_unreachable_and_tampered_resources_fail_closed_without_journaling(
    tmp_path: Path,
) -> None:
    foreign = _case(tmp_path / "foreign")
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        _adapter(foreign, principal_id="foreign-principal").read_resource(
            MCPResourceRead(uri=foreign.resource_uri)
        )
    with pytest.raises(HarnessMCPProtocolError, match="unknown Harness resource"):
        _adapter(foreign).read_resource(
            MCPResourceRead(
                uri=(f"harness://workflows/{foreign.snapshot.workflow_run_id}/artifacts/{'f' * 64}")
            )
        )
    assert _row_count(foreign.database, "golden_workflow_resource_accesses") == 0

    tampered = _case(tmp_path / "tampered")
    target = tampered.store.resolve_digest(tampered.resource_uri.rsplit("/", 1)[1])
    target.write_bytes(b"tampered objective bytes\n")
    with pytest.raises(HarnessMCPAdapterError) as caught:
        _adapter(tampered).read_resource(MCPResourceRead(uri=tampered.resource_uri))
    assert caught.value.code == "HARN_PERSISTENCE_CONFLICT"
    assert _row_count(tampered.database, "golden_workflow_resource_accesses") == 0


def test_receipt_uses_dedicated_lookup_and_enters_closure_only_when_control_cites_it(
    tmp_path: Path,
) -> None:
    case = control._case(tmp_path)
    request = _request(case.head)
    deliveries = tuple(
        case.harness.read_workflow_artifact_audited(
            request,
            sha256=artifact.sha256,
            resource_uri=(
                f"harness://workflows/{case.head.workflow_run_id}/artifacts/{artifact.sha256}"
            ),
        )
        for artifact in (
            case.source_prompt,
            case.failure_receipt,
            case.observation,
            case.diagnostic,
        )
    )
    access_refs = tuple(
        sorted(
            (delivery.access_receipt for delivery in deliveries),
            key=lambda ref: (ref.sha256, ref.schema_version or "", ref.media_type),
        )
    )
    delivery = next(item for item in deliveries if item.resource == case.observation)

    with pytest.raises(GoldenRunNotFoundError, match="workflow artifact"):
        case.harness.read_workflow_artifact(
            request,
            sha256=delivery.access_receipt.sha256,
        )
    receipt_ref, receipt_payload = case.harness.read_resource_access_receipt(
        request,
        sha256=delivery.access_receipt.sha256,
    )
    assert receipt_ref == delivery.access_receipt
    assert receipt_payload == delivery.access_receipt_payload

    command_values = control._advisory_command(case, audit_accesses=False).model_dump(mode="python")
    command_values["resource_access_receipts"] = access_refs
    command = AdvisoryRecordCommand.model_validate(command_values)
    with pytest.raises(ValidationError, match="sorted and unique"):
        AdvisoryRecordCommand.model_validate(
            {**command_values, "resource_access_receipts": (receipt_ref, receipt_ref)}
        )
    with pytest.raises(ValidationError, match=RESOURCE_ACCESS_RECEIPT_SCHEMA_ID):
        AdvisoryRecordCommand.model_validate(
            {
                **command_values,
                "resource_access_receipts": (
                    receipt_ref.model_copy(update={"schema_version": "harness.prompt_text.v1"}),
                ),
            }
        )

    committed = case.harness.submit_control(command)
    advisory = json.loads(case.store.resolve(committed.receipt).path.read_bytes())
    assert advisory["resource_access_receipts"] == [
        access_ref.model_dump(mode="json") for access_ref in access_refs
    ]
    for access_ref in access_refs:
        served_ref, served_payload = case.harness.read_workflow_artifact(
            request,
            sha256=access_ref.sha256,
        )
        assert served_ref == access_ref
        assert hashlib.sha256(served_payload).hexdigest() == access_ref.sha256


def test_advisory_rejects_access_receipts_that_do_not_cover_every_claimed_source(
    tmp_path: Path,
) -> None:
    case = control._case(tmp_path)
    request = _request(case.head)
    unrelated = case.head.state_ref
    unrelated_delivery = case.harness.read_workflow_artifact_audited(
        request,
        sha256=unrelated.sha256,
        resource_uri=(
            f"harness://workflows/{case.head.workflow_run_id}/artifacts/{unrelated.sha256}"
        ),
    )
    before = case.harness.read(request)
    command_values = control._advisory_command(case, audit_accesses=False).model_dump(mode="python")
    command_values["resource_access_receipts"] = (unrelated_delivery.access_receipt,)
    command = AdvisoryRecordCommand.model_validate(command_values)

    with pytest.raises(GoldenRunNotFoundError, match="resource access.*cover"):
        case.harness.submit_control(command)

    assert case.harness.read(request) == before
    assert _row_count(case.workflow_store.path, "golden_workflow_turns") == before.turn_seq
    assert _row_count(case.workflow_store.path, "golden_workflow_resource_accesses") == 1


def test_advisory_rejects_zero_access_receipts_without_advancing_the_workflow(
    tmp_path: Path,
) -> None:
    case = control._case(tmp_path)
    request = _request(case.head)
    command = AdvisoryRecordCommand.model_validate(
        control._advisory_command(case, audit_accesses=False).model_dump(mode="python")
    )
    assert command.resource_access_receipts == ()
    before = case.harness.read(request)

    with pytest.raises(GoldenRunNotFoundError, match="resource access.*cover"):
        case.harness.submit_control(command)

    assert case.harness.read(request) == before
    assert _row_count(case.workflow_store.path, "golden_workflow_turns") == before.turn_seq
    assert _row_count(case.workflow_store.path, "golden_workflow_resource_accesses") == 0


def test_receipt_resource_lookup_does_not_recursively_create_a_read_receipt(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    adapter = _adapter(case)
    delivered = adapter.read_resource(MCPResourceRead(uri=case.resource_uri))

    looked_up = adapter.read_resource(MCPResourceRead(uri=delivered.access_receipt_uri))

    assert looked_up.uri == delivered.access_receipt_uri
    assert looked_up.media_type == "application/json"
    assert looked_up.text == delivered.access_receipt_text
    assert looked_up.blob is None
    assert looked_up.resource == delivered.access_receipt
    assert looked_up.access_receipt is None
    assert looked_up.access_receipt_uri is None
    assert looked_up.access_receipt_text is None
    assert _row_count(case.database, "golden_workflow_resource_accesses") == 1


def test_resource_access_store_rejects_invalid_queries_and_stale_authority(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    delivery = case.harness.read_workflow_artifact_audited(
        _request(case.snapshot),
        sha256=case.resource_uri.rsplit("/", 1)[1],
        resource_uri=case.resource_uri,
    )
    receipt = ResourceAccessReceipt.model_validate_json(delivery.access_receipt_payload)
    snapshot_sha256 = hashlib.sha256(_canonical_bytes(case.snapshot)).hexdigest()
    identity = resource_access_request_sha256(
        principal_id=receipt.principal_id,
        workflow_run_id=receipt.workflow_run_id,
        resource_uri=receipt.resource_uri,
        resource=receipt.resource,
        observed_snapshot_sha256=receipt.observed_snapshot_sha256,
    )

    with pytest.raises(ValueError, match="exactly one"):
        case.workflow_store.read_resource_access(
            principal_id=receipt.principal_id,
            workflow_run_id=receipt.workflow_run_id,
        )
    with pytest.raises(ValueError, match="exactly one"):
        case.workflow_store.read_resource_access(
            principal_id=receipt.principal_id,
            workflow_run_id=receipt.workflow_run_id,
            access_identity_sha256=identity,
            receipt_payload_sha256=delivery.access_receipt.sha256,
        )
    assert (
        case.workflow_store.read_resource_access(
            principal_id="foreign-principal",
            workflow_run_id=receipt.workflow_run_id,
            access_identity_sha256=identity,
        )
        is None
    )
    assert (
        case.workflow_store.read_resource_access(
            principal_id=receipt.principal_id,
            workflow_run_id=receipt.workflow_run_id,
            access_identity_sha256="f" * 64,
        )
        is None
    )
    assert (
        case.workflow_store.commit_resource_access(
            principal_id="foreign-principal",
            access_identity_sha256=identity,
            expected_snapshot_sha256=snapshot_sha256,
            receipt=receipt,
        )
        is None
    )
    assert (
        case.workflow_store.commit_resource_access(
            principal_id=receipt.principal_id,
            access_identity_sha256=identity,
            expected_snapshot_sha256=snapshot_sha256,
            receipt=receipt,
        ).receipt
        == receipt
    )
    with pytest.raises(GoldenRunHeadConflictError, match="snapshot changed"):
        case.workflow_store.commit_resource_access(
            principal_id=receipt.principal_id,
            access_identity_sha256="e" * 64,
            expected_snapshot_sha256="f" * 64,
            receipt=receipt,
        )
    with pytest.raises(GoldenRunCorruptionError, match="current workflow authority"):
        case.workflow_store.commit_resource_access(
            principal_id=receipt.principal_id,
            access_identity_sha256="e" * 64,
            expected_snapshot_sha256=snapshot_sha256,
            receipt=receipt.model_copy(update={"principal_id": "foreign-principal"}),
        )
    with pytest.raises(GoldenRunConflictError, match="already bound"):
        case.workflow_store.commit_resource_access(
            principal_id=receipt.principal_id,
            access_identity_sha256="e" * 64,
            expected_snapshot_sha256=snapshot_sha256,
            receipt=receipt,
        )


def test_resource_access_store_rejects_a_tampered_request_identity(tmp_path: Path) -> None:
    case = _case(tmp_path)
    delivery = case.harness.read_workflow_artifact_audited(
        _request(case.snapshot),
        sha256=case.resource_uri.rsplit("/", 1)[1],
        resource_uri=case.resource_uri,
    )
    with sqlite3.connect(case.database) as connection:
        connection.execute(
            "UPDATE golden_workflow_resource_accesses SET access_identity_sha256 = ?",
            ("f" * 64,),
        )

    with pytest.raises(GoldenRunCorruptionError, match="inconsistent authority metadata"):
        case.workflow_store.read_resource_access(
            principal_id=case.snapshot.principal_id,
            workflow_run_id=case.snapshot.workflow_run_id,
            receipt_payload_sha256=delivery.access_receipt.sha256,
        )
