from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

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
    asset_loader_closure_sha256,
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
    payload.update(
        {
            key: update.model_dump(mode="json") if hasattr(update, "model_dump") else update
            for key, update in updates.items()
        }
    )
    payload[digest_field] = canonical_evidence_sha256(payload, digest_field)
    return type(value).model_validate(payload)


def _replace_evidence_ref(value: object, old: ArtifactRef, new: ArtifactRef) -> object:
    if type(value) is dict:
        if value == old.model_dump(mode="json"):
            return new.model_dump(mode="json")
        return {key: _replace_evidence_ref(child, old, new) for key, child in value.items()}
    if type(value) is list:
        return [_replace_evidence_ref(child, old, new) for child in value]
    return value


def _resign_load_graph(
    probe_graph: tuple[Any, ...],
    *,
    observation_updates: dict[str, object] | None = None,
    load_updates: dict[str, object] | None = None,
) -> GenesisAssetProbeArtifacts:
    store, scratch, _, _, _, artifacts = probe_graph
    observation = _rehash(
        artifacts.load_observation,
        "load_observation_sha256",
        **(observation_updates or {}),
    )
    observation_ref = _put_document(
        store,
        scratch,
        observation,
        name="genesis-asset-load-observation.json",
        schema_version="harness.genesis_asset_load_observation.v1",
    )
    observation_values = observation.model_dump(mode="json")
    load_values = artifacts.load_evidence.model_dump(mode="json")
    for field in tuple(load_values):
        if (
            field != "schema_version"
            and field in observation_values
            and not field.endswith("_sha256")
        ):
            load_values[field] = observation_values[field]
    for field in (
        "source_record_sha256",
        "representation_sha256",
        "loader_closure_sha256",
        "backend_implementation_sha256",
    ):
        load_values[field] = observation_values[field]
    load_values.update(load_updates or {})
    load_values["load_observation"] = observation_ref.model_dump(mode="json")
    load_values["load_evidence_sha256"] = canonical_evidence_sha256(
        load_values,
        "load_evidence_sha256",
    )
    load_evidence = type(artifacts.load_evidence).model_validate(load_values)
    load_evidence_ref = _put_document(
        store,
        scratch,
        load_evidence,
        name="genesis-asset-load-evidence.json",
        schema_version="harness.genesis_asset_load_evidence.v1",
    )
    trace = _rehash(
        artifacts.contact_trace,
        "contact_trace_sha256",
        load_evidence=load_evidence_ref,
    )
    trace_ref = _put_document(
        store,
        scratch,
        trace,
        name="genesis-asset-contact-trace.json",
        schema_version="harness.genesis_asset_contact_trace.v1",
    )
    report_values: object = artifacts.probe_report.model_dump(mode="json")
    report_values = _replace_evidence_ref(
        report_values,
        artifacts.load_observation_ref,
        observation_ref,
    )
    report_values = _replace_evidence_ref(
        report_values,
        artifacts.load_evidence_ref,
        load_evidence_ref,
    )
    report_values = _replace_evidence_ref(
        report_values,
        artifacts.contact_trace_ref,
        trace_ref,
    )
    assert type(report_values) is dict
    report_values["load_evidence"] = load_evidence_ref.model_dump(mode="json")
    report_values["contact_trace"] = trace_ref.model_dump(mode="json")
    for check in report_values["checks"]:
        check["evidence"].sort(
            key=lambda ref: (ref["sha256"], ref["bytes"], ref["media_type"])
        )
    report_values["probe_report_sha256"] = canonical_evidence_sha256(
        report_values,
        "probe_report_sha256",
    )
    report = type(artifacts.probe_report).model_validate(report_values)
    report_ref = _put_document(
        store,
        scratch,
        report,
        name="genesis-asset-probe-report.json",
        schema_version="harness.genesis_asset_probe_report.v1",
    )
    return replace(
        artifacts,
        load_observation=observation,
        load_observation_ref=observation_ref,
        load_evidence=load_evidence,
        load_evidence_ref=load_evidence_ref,
        contact_trace=trace,
        contact_trace_ref=trace_ref,
        probe_report=report,
        probe_report_ref=report_ref,
    )


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
        ("source_link_count", 2),
        ("source_joint_count", 1),
        ("loaded_link_count", 2),
        ("loaded_joint_count", 0),
        ("loaded_dof_count", 5),
        ("source_collision_members", ("foreign.obj",) * 8),
        ("loaded_collision_shape_count", 0),
        ("loaded_collision_shapes_merged", 1),
        ("loaded_collision_vertex_count", 0),
        ("loaded_collision_face_count", 0),
        ("dynamic", False),
        ("fixed", True),
        ("mass_kg", 0.0),
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


@pytest.mark.parametrize(
    "observation_updates",
    (
        {"probe_input": None},
        {"representation": None},
        {"runtime_lock": None},
        {"backend_version": "0.3.4"},
        {"backend_implementation_sha256": "f" * 64},
    ),
)
def test_fully_resigned_raw_load_authority_drift_fails_closed(
    probe_graph: tuple[Any, ...],
    observation_updates: dict[str, object],
) -> None:
    artifacts = probe_graph[5]
    updates = dict(observation_updates)
    if updates.get("probe_input") is None:
        updates["probe_input"] = artifacts.probe_input_ref.model_copy(
            update={"name": "foreign-probe-input.json"}
        ).model_dump(mode="json")
    if updates.get("representation") is None:
        updates["representation"] = artifacts.load_observation.representation.model_copy(
            update={"name": "foreign-representation.json"}
        ).model_dump(mode="json")
    if updates.get("runtime_lock") is None:
        updates["runtime_lock"] = artifacts.load_observation.runtime_lock.model_copy(
            update={"name": "foreign-runtime-lock.json"}
        ).model_dump(mode="json")
    resigned = _resign_load_graph(probe_graph, observation_updates=updates)

    with pytest.raises(GenesisAssetProbeError, match="raw load observation") as caught:
        _verify(probe_graph, resigned)
    assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"


def test_resigned_load_summary_and_representation_digest_drift_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    bad_summary = _resign_load_graph(probe_graph, load_updates={"friction": 0.3})
    with pytest.raises(GenesisAssetProbeError, match="raw load observation"):
        _verify(probe_graph, bad_summary)

    bad_representation = _resign_load_graph(
        probe_graph,
        observation_updates={"representation_sha256": "f" * 64},
    )
    with pytest.raises(GenesisAssetProbeError, match="representation identity"):
        _verify(probe_graph, bad_representation)


def test_probe_input_reference_and_profile_cross_binding_fail_closed(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    changed_input = _rehash(
        artifacts.probe_input,
        "probe_input_sha256",
        runtime_lock=artifacts.probe_input.runtime_lock.model_copy(
            update={"name": "foreign-runtime-lock.json"}
        ),
    )
    changed_input_ref = _put_document(
        store,
        scratch,
        changed_input,
        name="genesis-asset-probe-input.json",
        schema_version="harness.genesis_asset_probe_input.v1",
    )
    with pytest.raises(GenesisAssetProbeError, match="probe input representation or runtime"):
        _verify(
            probe_graph,
            replace(
                artifacts,
                probe_input=changed_input,
                probe_input_ref=changed_input_ref,
            ),
        )

    _, robot_runtime_ref = _runtime_lock(
        store,
        scratch,
        requested_profile="genesis.robot_policy@1",
    )
    robot_input = _rehash(
        artifacts.probe_input,
        "probe_input_sha256",
        runtime_lock=robot_runtime_ref,
    )
    robot_input_ref = _put_document(
        store,
        scratch,
        robot_input,
        name="genesis-asset-probe-input.json",
        schema_version="harness.genesis_asset_probe_input.v1",
    )
    with pytest.raises(GenesisAssetProbeError, match="profile"):
        _verify(
            probe_graph,
            replace(
                artifacts,
                probe_input=robot_input,
                probe_input_ref=robot_input_ref,
            ),
            runtime_lock_ref=robot_runtime_ref,
        )


def test_invalid_backend_load_and_step_types_or_values_fail_closed(
    tmp_path: Path,
) -> None:
    class WrongLoadTypeBackend(ControlledCpuGenesisBackend):
        def load(self, request: Any) -> Any:
            super().load(request)
            return object()

    class WrongStepTypeBackend(ControlledCpuGenesisBackend):
        def observe_initial(self) -> Any:
            return object()

    class InvalidStepValueBackend(ControlledCpuGenesisBackend):
        def observe_initial(self) -> Any:
            observed = super().observe_initial()
            return replace(observed, orientation_wxyz=(2.0, 0.0, 0.0, 0.0))

    for index, backend in enumerate(
        (WrongLoadTypeBackend(), WrongStepTypeBackend(), InvalidStepValueBackend())
    ):
        case_root = tmp_path / str(index)
        store = LocalArtifactStore(case_root / "cas")
        scratch = case_root / "scratch"
        scratch.mkdir(parents=True)
        _, representation_ref = _representation(store, scratch)
        _, runtime_lock_ref = _runtime_lock(store, scratch)
        with pytest.raises(GenesisAssetProbeError) as caught:
            GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
                representation_ref=representation_ref,
                runtime_lock_ref=runtime_lock_ref,
            )
        assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"


def test_deep_verifier_recomputes_penetration_from_resigned_rows(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    rows = list(artifacts.rows)
    row = rows[125]
    contact_payload = row.contacts[0].model_dump(mode="json")
    contact_payload["penetration_m"] = 0.002
    contact = type(row.contacts[0]).model_validate(contact_payload)
    row_payload = row.model_dump(mode="json")
    row_payload["contacts"] = [contact.model_dump(mode="json")]
    row_payload["max_penetration_m"] = 0.002
    rows[125] = type(row).model_validate(row_payload)
    resigned_rows = tuple(rows)
    trace_ref = _put_trace(
        store,
        scratch,
        b"".join(_canonical_json(value.model_dump(mode="json")) for value in resigned_rows),
    )

    with pytest.raises(GenesisAssetProbeError, match="penetration") as caught:
        _verify(
            probe_graph,
            replace(artifacts, rows=resigned_rows, trace_rows_ref=trace_ref),
        )
    assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_FAILED"


def test_exact_named_invalid_and_noncanonical_documents_reach_model_reader(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    payload = artifacts.probe_input.model_dump(mode="json")
    for candidate in (b"{}\n", json.dumps(payload, indent=1).encode() + b"\n"):
        ref = _put_bytes(
            store,
            scratch,
            name="genesis-asset-probe-input.json",
            payload=candidate,
            media_type="application/json",
            schema_version="harness.genesis_asset_probe_input.v1",
        )
        with pytest.raises(GenesisAssetProbeError) as caught:
            _verify(probe_graph, replace(artifacts, probe_input_ref=ref))
        assert caught.value.code == "HARN_GENESIS_ASSET_PROBE_EVIDENCE_MISMATCH"


def test_empty_and_blank_trace_payloads_are_rejected_as_noncanonical(
    probe_graph: tuple[Any, ...],
) -> None:
    store, scratch, _, _, _, artifacts = probe_graph
    payload = store.resolve(artifacts.trace_rows_ref).path.read_bytes()
    lines = payload.splitlines()
    candidates = (
        b"",
        b"\n" + b"\n".join(lines[1:]) + b"\n",
        b"\n".join(lines),
    )
    for candidate in candidates:
        ref = _put_trace(store, scratch, candidate)
        with pytest.raises(GenesisAssetProbeError, match="row|noncanonical"):
            _verify(probe_graph, replace(artifacts, trace_rows_ref=ref))


def test_bounded_representation_requires_all_fifteen_members_before_backend(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    representation, _ = _representation(store, scratch)
    members = tuple(
        member for member in representation.members if member.logical_path != "physics.json"
    )
    payload = representation.model_dump(mode="json")
    payload["members"] = [member.model_dump(mode="json") for member in members]
    payload["loader_closure_sha256"] = asset_loader_closure_sha256(
        representation.subject,
        representation.root_logical_path,
        members,
    )
    payload["representation_sha256"] = canonical_evidence_sha256(
        payload,
        "representation_sha256",
    )
    shortened = AssetRepresentationV1.model_validate(payload)
    shortened_ref = _put_document(
        store,
        scratch,
        shortened,
        name="short-representation.json",
        schema_version="harness.asset_representation.v1",
    )
    _, runtime_lock_ref = _runtime_lock(store, scratch)
    backend = ControlledCpuGenesisBackend()

    with pytest.raises(GenesisAssetProbeError, match="fifteen-member"):
        GenesisAssetProbeProducer(artifact_store=store, backend=backend).produce(
            representation_ref=shortened_ref,
            runtime_lock_ref=runtime_lock_ref,
        )
    assert backend.load_requests == []


def test_probe_schema_validators_reject_physics_and_summary_drift(
    probe_graph: tuple[Any, ...],
) -> None:
    artifacts = probe_graph[5]
    observation = artifacts.load_observation
    contact = artifacts.rows[125].contacts[0]
    row = artifacts.rows[125]
    trace = artifacts.contact_trace

    schema_attacks = (
        (
            observation,
            "load_observation_sha256",
            {"test_translation_m": (0.0, 0.0, 0.04)},
        ),
        (
            observation,
            "load_observation_sha256",
            {
                "inertia_kg_m2": {
                    "ixx": 1.0,
                    "ixy": 2.0,
                    "ixz": 0.0,
                    "iyy": 1.0,
                    "iyz": 0.0,
                    "izz": 1.0,
                }
            },
        ),
        (contact, "", {"normal_on_asset": (0.0, 0.0, 2.0)}),
        (contact, "", {"force_on_ground_n": (0.0, 0.0, -0.6)}),
        (contact, "", {"normal_force_n": 0.6}),
        (row, "", {"orientation_wxyz": (2.0, 0.0, 0.0, 0.0)}),
        (
            trace,
            "contact_trace_sha256",
            {
                "contact_step_count": 877,
                "first_contact_step": 125,
                "last_contact_step": 1000,
                "total_contact_count": 877,
            },
        ),
        (
            trace,
            "contact_trace_sha256",
            {"collision_pair_count": trace.total_contact_count + 1},
        ),
        (
            trace,
            "contact_trace_sha256",
            {"mean_contact_normal_force_n": trace.max_normal_force_n + 1.0},
        ),
    )
    for value, digest_field, updates in schema_attacks:
        payload = value.model_dump(mode="json")
        payload.update(updates)
        if digest_field:
            payload[digest_field] = canonical_evidence_sha256(payload, digest_field)
        with pytest.raises(ValidationError):
            type(value).model_validate(payload)


def test_initial_row_contact_and_loaded_collision_member_drift_are_invalid(
    probe_graph: tuple[Any, ...],
) -> None:
    artifacts = probe_graph[5]
    initial_payload = artifacts.rows[0].model_dump(mode="json")
    contact = artifacts.rows[125].contacts[0]
    initial_payload.update(
        contacts=[contact.model_dump(mode="json")],
        contact_count=1,
        total_normal_force_n=contact.normal_force_n,
        max_penetration_m=contact.penetration_m,
    )
    with pytest.raises(ValidationError, match="contact-free"):
        type(artifacts.rows[0]).model_validate(initial_payload)

    observation_payload = artifacts.load_observation.model_dump(mode="json")
    collision_members = list(observation_payload["source_collision_members"])
    collision_members[0] = "urdf/shape/collision/foreign.obj"
    observation_payload["source_collision_members"] = collision_members
    observation_payload["load_observation_sha256"] = canonical_evidence_sha256(
        observation_payload,
        "load_observation_sha256",
    )
    with pytest.raises(ValidationError, match="representation collision members"):
        type(artifacts.load_observation).model_validate(observation_payload)
