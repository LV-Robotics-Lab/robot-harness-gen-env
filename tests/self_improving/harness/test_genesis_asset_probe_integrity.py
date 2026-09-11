from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_probe import (
    GenesisAssetBackendLoad,
    GenesisAssetProbeArtifacts,
    GenesisAssetProbeError,
    GenesisAssetProbeEvidenceVerifier,
    GenesisAssetProbeProducer,
    VerifiedGenesisAssetProbeEvidence,
)
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.x2env_compile_evidence import (
    AssetRepresentationV1,
    canonical_evidence_sha256,
)
from tests.self_improving.harness.test_genesis_asset_probe_runner import (
    ControlledCpuGenesisBackend,
    _canonical_json,
    _put_bytes,
    _put_document,
    _representation,
    _runtime_lock,
)


@pytest.fixture
def probe_graph(tmp_path: Path) -> tuple[
    LocalArtifactStore,
    Path,
    AssetRepresentationV1,
    ArtifactRef,
    ArtifactRef,
    GenesisAssetProbeArtifacts,
]:
    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    representation, representation_ref = _representation(store, scratch)
    _, runtime_lock_ref = _runtime_lock(store, scratch)
    artifacts = GenesisAssetProbeProducer(
        artifact_store=store,
        backend=ControlledCpuGenesisBackend(),
    ).produce(
        representation_ref=representation_ref,
        runtime_lock_ref=runtime_lock_ref,
    )
    return store, scratch, representation, representation_ref, runtime_lock_ref, artifacts


def _verify(
    graph: tuple[
        LocalArtifactStore,
        Path,
        AssetRepresentationV1,
        ArtifactRef,
        ArtifactRef,
        GenesisAssetProbeArtifacts,
    ],
    artifacts: GenesisAssetProbeArtifacts | None = None,
    *,
    representation_ref: ArtifactRef | None = None,
    runtime_lock_ref: ArtifactRef | None = None,
    max_trace_bytes: int = 64 * 1024 * 1024,
) -> VerifiedGenesisAssetProbeEvidence:
    store, _, _, expected_representation, expected_runtime, expected_artifacts = graph
    return GenesisAssetProbeEvidenceVerifier(
        artifact_store=store,
        max_trace_bytes=max_trace_bytes,
    ).verify(
        expected_artifacts if artifacts is None else artifacts,
        representation_ref=(
            expected_representation if representation_ref is None else representation_ref
        ),
        runtime_lock_ref=expected_runtime if runtime_lock_ref is None else runtime_lock_ref,
    )


def _put_trace(
    store: LocalArtifactStore,
    scratch: Path,
    payload: bytes,
) -> ArtifactRef:
    return _put_bytes(
        store,
        scratch,
        name="genesis-asset-contact-trace.ndjson",
        payload=payload,
        media_type="application/x-ndjson",
        schema_version="harness.genesis_asset_contact_trace_row.v1",
    )


def _rehash(value: Any, digest_field: str, **updates: object) -> Any:
    payload = value.model_dump(mode="json")
    payload.update(updates)
    payload[digest_field] = canonical_evidence_sha256(payload, digest_field)
    return type(value).model_validate(payload)


def test_verified_probe_graph_is_reusable_and_supports_only_cas_artifacts(
    probe_graph: tuple[Any, ...],
) -> None:
    verified = _verify(probe_graph)  # type: ignore[arg-type]

    assert type(verified) is VerifiedGenesisAssetProbeEvidence
    assert len(verified.rows) == 1001
    assert verified.rows[0].phase == "initial"
    assert verified.rows[-1].step_index == 1000
    assert verified.contact_trace.total_normal_impulse_ns == pytest.approx(2.4528)
    assert len(verified.supporting_artifacts) == 23
    assert all(
        ref.uri == f"artifact://sha256/{ref.sha256}"
        for ref in verified.supporting_artifacts
    )
    assert (
        _verify(probe_graph).supporting_artifacts == verified.supporting_artifacts  # type: ignore[arg-type]
    )


def test_constructor_and_argument_types_are_exact(
    probe_graph: tuple[Any, ...],
) -> None:
    store = probe_graph[0]

    class StoreSubclass(LocalArtifactStore):
        pass

    class ArtifactRefSubclass(ArtifactRef):
        pass

    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        GenesisAssetProbeProducer(
            artifact_store=StoreSubclass(store.root),
            backend=ControlledCpuGenesisBackend(),
        )
    with pytest.raises(TypeError, match="implement GenesisAssetProbeBackend"):
        GenesisAssetProbeProducer(artifact_store=store, backend=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact LocalArtifactStore"):
        GenesisAssetProbeEvidenceVerifier(artifact_store=StoreSubclass(store.root))
    for limit in (0, -1, True, 1.0):
        with pytest.raises(ValueError, match="positive exact integer"):
            GenesisAssetProbeEvidenceVerifier(artifact_store=store, max_trace_bytes=limit)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact GenesisAssetProbeArtifacts"):
        GenesisAssetProbeEvidenceVerifier(artifact_store=store).verify(  # type: ignore[arg-type]
            object(),
            representation_ref=probe_graph[3],
            runtime_lock_ref=probe_graph[4],
        )

    derived = ArtifactRefSubclass.model_validate(probe_graph[3].model_dump(mode="json"))
    with pytest.raises(TypeError, match="exact ArtifactRef"):
        _verify(probe_graph, representation_ref=derived)  # type: ignore[arg-type]


def test_trace_reference_cas_uri_media_schema_name_and_bytes_drift_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, _, _, _, _, artifacts = probe_graph
    ref = artifacts.trace_rows_ref
    identical_path = store.resolve(ref).path
    candidates = (
        ref.model_copy(update={"uri": identical_path.as_uri()}),
        ref.model_copy(update={"media_type": "application/octet-stream"}),
        ref.model_copy(update={"schema_version": "harness.other.v1"}),
        ref.model_copy(update={"name": "foreign-trace.ndjson"}),
        ref.model_copy(update={"bytes": ref.bytes + 1}),
    )

    for candidate in candidates:
        with pytest.raises(GenesisAssetProbeError) as caught:
            _verify(probe_graph, replace(artifacts, trace_rows_ref=candidate))  # type: ignore[arg-type]
        assert caught.value.code in {
            "HARN_ARTIFACT_UNAVAILABLE",
            "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH",
        }


def test_missing_cas_object_and_model_identity_drift_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, _, _, _, _, artifacts = probe_graph
    store.resolve(artifacts.probe_report_ref).path.unlink()

    with pytest.raises(GenesisAssetProbeError) as caught:
        _verify(probe_graph)  # type: ignore[arg-type]
    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"


def test_noncanonical_and_invalid_json_documents_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    payload = artifacts.probe_input.model_dump(mode="json")
    candidates = (
        json.dumps(payload, indent=2).encode() + b"\n",
        b"{}\n",
    )
    for index, candidate in enumerate(candidates):
        ref = _put_bytes(
            store,
            scratch,
            name=f"probe-input-{index}.json",
            payload=candidate,
            media_type="application/json",
            schema_version="harness.genesis_asset_probe_input.v1",
        )
        with pytest.raises(GenesisAssetProbeError) as caught:
            _verify(probe_graph, replace(artifacts, probe_input_ref=ref))  # type: ignore[arg-type]
        assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"


def test_trace_row_count_crlf_noncanonical_and_invalid_rows_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    payload = store.resolve(artifacts.trace_rows_ref).path.read_bytes()
    lines = payload.splitlines()
    candidates = (
        b"\n".join(lines[:-1]) + b"\n",
        payload.replace(b"\n", b"\r\n"),
        b" " + lines[0] + b"\n" + b"\n".join(lines[1:]) + b"\n",
        b"{}\n" + b"\n".join(lines[1:]) + b"\n",
    )

    for candidate in candidates:
        ref = _put_trace(store, scratch, candidate)
        with pytest.raises(GenesisAssetProbeError) as caught:
            _verify(probe_graph, replace(artifacts, trace_rows_ref=ref))  # type: ignore[arg-type]
        assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"

    with pytest.raises(GenesisAssetProbeError, match="oversized|noncanonical"):
        _verify(probe_graph, max_trace_bytes=1)  # type: ignore[arg-type]


def test_reordered_rows_and_supplied_row_identity_drift_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    with pytest.raises(GenesisAssetProbeError, match="row identity"):
        _verify(probe_graph, replace(artifacts, rows=artifacts.rows[:-1]))  # type: ignore[arg-type]

    reordered = (artifacts.rows[1], artifacts.rows[0], *artifacts.rows[2:])
    ref = _put_trace(
        store,
        scratch,
        b"".join(_canonical_json(row.model_dump(mode="json")) for row in reordered),
    )
    with pytest.raises(GenesisAssetProbeError, match="contiguous"):
        _verify(
            probe_graph,
            replace(artifacts, rows=reordered, trace_rows_ref=ref),
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "update"),
    (
        ("probe_input", {"seed": 1}),
        ("load_observation", {"friction": 0.3}),
        ("load_evidence", {"friction": 0.3}),
        ("contact_trace", {"total_normal_impulse_ns": 99.0}),
        ("probe_report", {"steps_executed": 999}),
    ),
)
def test_supplied_cross_layer_model_drift_fails_closed(
    probe_graph: tuple[Any, ...],
    field: str,
    update: dict[str, object],
) -> None:
    artifacts = probe_graph[5]
    changed = getattr(artifacts, field).model_copy(update=update)

    with pytest.raises(GenesisAssetProbeError, match="differs"):
        _verify(probe_graph, replace(artifacts, **{field: changed}))  # type: ignore[arg-type]


def test_rehashed_trace_and_report_drift_still_fail_deep_recomputation(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    trace = _rehash(
        artifacts.contact_trace,
        "contact_trace_sha256",
        total_normal_impulse_ns=artifacts.contact_trace.total_normal_impulse_ns + 1.0,
    )
    trace_ref = _put_document(
        store,
        scratch,
        trace,
        name="genesis-asset-contact-trace.json",
        schema_version="harness.genesis_asset_contact_trace.v1",
    )
    with pytest.raises(GenesisAssetProbeError, match="summary"):
        _verify(
            probe_graph,
            replace(artifacts, contact_trace=trace, contact_trace_ref=trace_ref),
        )  # type: ignore[arg-type]

    report_payload = artifacts.probe_report.model_dump(mode="json")
    report_payload["checks"][0]["evidence"][0]["name"] = "foreign-load-observation.json"
    report_payload["probe_report_sha256"] = canonical_evidence_sha256(
        report_payload,
        "probe_report_sha256",
    )
    report = type(artifacts.probe_report).model_validate(report_payload)
    report_ref = _put_document(
        store,
        scratch,
        report,
        name="genesis-asset-probe-report.json",
        schema_version="harness.genesis_asset_probe_report.v1",
    )
    with pytest.raises(GenesisAssetProbeError, match="checks|evidence"):
        _verify(
            probe_graph,
            replace(artifacts, probe_report=report, probe_report_ref=report_ref),
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("backend", "other"),
        ("backend_version", "0.3.4"),
        ("implementation_sha256", "f" * 64),
        ("loader_contract", "harness.other_loader.v1"),
    ),
)
def test_runtime_backend_drift_is_rejected_before_loading(
    tmp_path: Path,
    attribute: str,
    value: str,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, representation_ref = _representation(store, scratch)
    _, runtime_lock_ref = _runtime_lock(store, scratch)
    backend = ControlledCpuGenesisBackend()
    setattr(backend, attribute, value)

    with pytest.raises(GenesisAssetProbeError) as caught:
        GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
    assert caught.value.code == "HARN_DEPENDENCY_DRIFT"
    assert backend.load_requests == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source", "mesh"),
        ("root_link_count", 2),
        ("joint_count", 1),
        ("collision_shape_count", 7),
        ("collision_shape_members", ("foreign.obj",) * 8),
        ("dynamic", False),
        ("fixed", True),
    ),
)
def test_loaded_asset_contract_drift_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    class DriftedLoadBackend(ControlledCpuGenesisBackend):
        def load(self, request: Any) -> GenesisAssetBackendLoad:
            loaded = super().load(request)
            return replace(loaded, **{field: value})

    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, representation_ref = _representation(store, scratch)
    _, runtime_lock_ref = _runtime_lock(store, scratch)

    with pytest.raises(GenesisAssetProbeError) as caught:
        GenesisAssetProbeProducer(
            artifact_store=store,
            backend=DriftedLoadBackend(),
        ).produce(
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
    assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"


@pytest.mark.parametrize("failure", ("no_contact", "penetration"))
def test_contact_absence_and_penetration_violation_never_publish_a_report(
    tmp_path: Path,
    failure: str,
) -> None:
    class NoContactBackend(ControlledCpuGenesisBackend):
        def _observation(self, step_index: int, phase: str) -> Any:
            observed = super()._observation(step_index, phase)
            return replace(observed, asset_ground_contacts=())

    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, representation_ref = _representation(store, scratch)
    _, runtime_lock_ref = _runtime_lock(store, scratch)
    backend = (
        NoContactBackend()
        if failure == "no_contact"
        else ControlledCpuGenesisBackend(failure="penetration")
    )

    with pytest.raises(GenesisAssetProbeError) as caught:
        GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
            representation_ref=representation_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
    assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_FAILED"
    assert not any(
        path.read_bytes().find(b"harness.genesis_asset_probe_report.v1") >= 0
        for path in store.root.glob("sha256/*/*")
        if path.is_file()
    )
