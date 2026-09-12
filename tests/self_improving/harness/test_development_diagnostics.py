"""Development measurements observe real reads without changing qualified code."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.development_diagnostics import (
    append_development_diagnostic_sample,
    build_development_execution_metrics,
    install_development_diagnostics,
    note_qualified_skill_binding,
    read_development_diagnostic_samples,
)
from self_improving.harness.genesis_runtime_manifest import read_runtime_manifest
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.genesis_runtime_manifest import (
    GenesisRuntimeManifestV1,
    RuntimeManifestMemberV1,
    RuntimeProcessIdentityV1,
)


def _put(store: LocalArtifactStore, tmp_path: Path, name: str, payload: bytes) -> ArtifactRef:
    path = tmp_path / name.replace("/", "_")
    path.write_bytes(payload)
    return store.put_file(path, name=name, media_type="application/octet-stream", schema_version=None)


def _runtime_manifest(store: LocalArtifactStore, tmp_path: Path) -> ArtifactRef:
    members = tuple(
        RuntimeManifestMemberV1(
            root=role,
            path=f"{role}.bin",
            artifact=_put(store, tmp_path, f"{role}/{role}.bin", role.encode()),
        )
        for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib")
    )
    manifest = GenesisRuntimeManifestV1(
        schema_version="harness.genesis_runtime_manifest.v1",
        authority="declared_byte_closure_only",
        process_identity=RuntimeProcessIdentityV1(
            implementation="CPython",
            version="3.11",
            abi="cpython-311",
            platform="linux",
            environment=(),
        ),
        members=members,
    )
    raw = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    return store.put_file(
        path,
        name="genesis-runtime-manifest.json",
        media_type="application/json",
        schema_version="harness.genesis_runtime_manifest.v1",
    )


def test_development_measurement_is_request_bounded_and_persisted(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    manifest_ref = _runtime_manifest(store, tmp_path)
    tracker = install_development_diagnostics(store, runtime_manifests=(manifest_ref,))
    workflow_id = UUID("10000000-0000-4000-8000-000000000001")

    token = tracker.begin(workflow_id=workflow_id, operation="image2env_replay_v2_0_0")
    read_runtime_manifest(store, manifest_ref)
    note_qualified_skill_binding(store, "image2env.replay@2.0.0")
    sample = tracker.finish(token, status="succeeded")

    # Existing qualified LocalArtifactStore resolves each artifact URI through
    # resolve_digest and then hashes it again in resolve.  This is an observed
    # baseline, not a claim that the duplicate scan has been removed.
    assert sample.counters.cas_full_hash_scan_count == 14
    assert sample.counters.cas_full_hash_logical_bytes == 2 * (
        manifest_ref.bytes + sum(member.artifact.bytes for member in read_runtime_manifest(store, manifest_ref).members)
    )
    assert sample.counters.runtime_manifest_deep_read_count == 1
    assert sample.counters.qualified_skill_binding_count == 1
    assert sample.counters.cas_storage_read_bytes is None
    assert sample.counters.runtime_deployment_tree_scan_count is None
    assert sample.measurement_basis.scope == "one development MCP process and exact tool call"

    database = tmp_path / "golden.sqlite"
    append_development_diagnostic_sample(database, sample)
    assert read_development_diagnostic_samples(database, workflow_id) == (sample,)
    metrics = build_development_execution_metrics((sample,))
    assert metrics.sample_count == 1
    assert metrics.totals == sample.counters
    assert metrics.qualification_status == "development_unqualified"
    assert metrics.created_at == sample.ended_at

    clean = tracker.snapshot()
    assert clean.cas_full_hash_scan_count == 28
    tracker.reset()
    assert tracker.snapshot().cas_full_hash_scan_count == 0


def test_development_diagnostic_rows_fail_closed_and_are_workflow_scoped(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    payload = _put(store, tmp_path, "payload.bin", b"payload")
    tracker = install_development_diagnostics(store, runtime_manifests=())
    workflow_id = UUID("20000000-0000-4000-8000-000000000002")
    token = tracker.begin(workflow_id=workflow_id, operation="image2env_compile_v2_0_0")
    store.resolve(payload)
    sample = tracker.finish(token, status="failed")
    database = tmp_path / "golden.sqlite"
    append_development_diagnostic_sample(database, sample)

    assert read_development_diagnostic_samples(
        database, UUID("30000000-0000-4000-8000-000000000003")
    ) == ()

    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE development_preview_diagnostic_samples SET digest=?",
            (hashlib.sha256(b"tampered").hexdigest(),),
        )
    with pytest.raises(ValueError, match="diagnostic sample is corrupt"):
        read_development_diagnostic_samples(database, workflow_id)


def test_development_diagnostic_timestamps_are_real_utc_samples(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    tracker = install_development_diagnostics(store, runtime_manifests=())
    before = datetime.now(UTC)
    token = tracker.begin(
        workflow_id=UUID("40000000-0000-4000-8000-000000000004"), operation="status"
    )
    sample = tracker.finish(token, status="succeeded")
    after = datetime.now(UTC)

    assert before <= sample.started_at <= sample.ended_at <= after
    assert sample.sequence == 1


def test_development_mcp_call_persists_one_bounded_measurement(tmp_path: Path) -> None:
    from mcp import Client

    from self_improving.harness.limited_image_preview_mcp import (
        create_limited_image_preview_mcp_server,
    )
    from tests.self_improving.harness.test_golden_control_v2_runtime import native_case

    store = LocalArtifactStore(tmp_path / "cas")
    payload = _put(store, tmp_path, "payload.json", b"{}")
    install_development_diagnostics(store, runtime_manifests=())
    database = tmp_path / "golden.sqlite"
    head = native_case(tmp_path / "state").head
    workflow_id = head.workflow_run_id

    class Service:
        execution_mode = "development"
        journal = SimpleNamespace(path=database)

        def __init__(self):
            self.store = store

        def status(self, **arguments):
            assert arguments == {"workflow_id": str(workflow_id)}
            self.store.resolve(payload)
            return {
                "workflow_id": str(workflow_id),
                "revision": head.revision,
                "stage": "status",
                "status": "active",
                "snapshot": head.model_dump(mode="json"),
            }

    async def exercise():
        async with Client(create_limited_image_preview_mcp_server(Service())) as client:
            result = await client.call_tool(
                "image2env_preview_status_v1", {"workflow_id": str(workflow_id)}
            )
            assert result.structured_content["status"] == "active"

    import asyncio

    asyncio.run(exercise())
    samples = read_development_diagnostic_samples(database, workflow_id)
    assert len(samples) == 1
    assert samples[0].operation == "image2env_preview_status_v1"
    assert samples[0].status == "active"
    assert samples[0].counters.cas_full_hash_scan_count == 2
