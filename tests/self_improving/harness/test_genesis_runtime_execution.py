"""Canonical contracts and public execution evidence verification."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_runtime_execution import (
    GenesisRuntimeExecutionProducer,
    GenesisRuntimeExecutionVerifier,
    LiveGenesisRuntimeExecution,
    runtime_backend_identity,
)
from self_improving.harness.genesis_runtime_manifest import (
    capture_runtime_manifest,
    read_runtime_manifest,
)
from self_improving.harness.schemas.genesis_runtime_execution import (
    GenesisRuntimeExecutionRequestV1,
)
from self_improving.harness.schemas.genesis_runtime_manifest import (
    GenesisRuntimeManifestV1,
    RuntimeProcessIdentityV1,
)
from self_improving.harness.schemas.x2env_compile_evidence import (
    GenesisRuntimeLockV1,
    canonical_evidence_sha256,
)


def ref(schema, name="record.json"):
    return {
        "name": name,
        "uri": "artifact://sha256/" + "a" * 64,
        "sha256": "a" * 64,
        "bytes": 1,
        "media_type": "application/json",
        "schema_version": schema,
    }


def request():
    runner_path = "self_improving/harness/genesis_runtime_launcher.py"
    runner = ref(None, "backend/" + runner_path)
    runner["media_type"] = "application/octet-stream"
    return {
        "schema_version": "harness.genesis_runtime_execution_request.v1",
        "manifest": ref("harness.genesis_runtime_manifest.v1"),
        "representation": ref("harness.asset_representation.v1"),
        "runtime_lock": ref("harness.genesis_runtime_lock.v1"),
        "runner": {"root": "backend", "path": runner_path, "artifact": runner},
    }


def test_request_binds_exact_fixed_runner_and_typed_cas_inputs():
    value = GenesisRuntimeExecutionRequestV1.model_validate(request())
    assert value.runner.root == "backend"
    assert value.manifest.schema_version == "harness.genesis_runtime_manifest.v1"
    invalid = request()
    invalid["manifest"]["schema_version"] = "harness.genesis_runtime_lock.v1"
    with pytest.raises(ValueError):
        GenesisRuntimeExecutionRequestV1.model_validate(invalid)


def test_backend_identity_binds_every_backend_member_only():
    members = []
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        artifact = ref(None, role + "/member")
        artifact["media_type"] = "application/octet-stream"
        members.append({"root": role, "path": "member", "artifact": artifact})
    manifest = GenesisRuntimeManifestV1.model_validate(
        {
            "schema_version": "harness.genesis_runtime_manifest.v1",
            "authority": "declared_byte_closure_only",
            "process_identity": {
                "implementation": "cpython",
                "version": "3",
                "abi": "abi",
                "platform": "linux",
                "environment": [],
            },
            "members": members,
        }
    )
    expected = hashlib.sha256(
        json.dumps([members[0]], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert runtime_backend_identity(manifest) == expected


def test_verifier_rejects_missing_request_before_execution(tmp_path):
    from self_improving.harness.schemas.common import ArtifactRef

    verifier = GenesisRuntimeExecutionVerifier(LocalArtifactStore(tmp_path / "cas"))
    with pytest.raises(ValueError):
        verifier.verify_request(
            ArtifactRef.model_validate(ref("harness.genesis_runtime_execution_request.v1"))
        )


def publish(store, tmp_path, value, *, name="record.json", legacy=False):
    path = tmp_path / "publish.json"
    path.write_bytes(
        json.dumps(
            value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        + (b"\n" if legacy else b"")
    )
    return store.put_file(
        path, name=name, media_type="application/json", schema_version=value.schema_version
    )


@pytest.fixture
def execution_request(tmp_path):
    from tests.self_improving.harness.test_genesis_asset_probe_runner import _representation

    store = LocalArtifactStore(tmp_path / "cas")
    roots = {}
    for role in ("backend", "distributions", "genesis", "interpreter", "native", "stdlib"):
        root = tmp_path / role
        root.mkdir()
        (root / "member").write_bytes(b"controlled fixture bytes; no execution claim")
        roots[role] = root
    runner_path = "self_improving/harness/genesis_runtime_launcher.py"
    runner = roots["backend"] / runner_path
    runner.parent.mkdir(parents=True)
    runner.write_bytes(b"fixture runner, not an executable capability")
    (runner.parent / "_genesis_cpu_child.py").write_bytes(b"fixture child")
    for name in ("libGLX.so.0.0.0", "libOpenGL.so.0.0.0"):
        path = roots["native"] / "usr/lib/x86_64-linux-gnu" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture native")
    identity = RuntimeProcessIdentityV1(
        implementation="cpython", version="3", abi="abi", platform="linux", environment=()
    )
    manifest_ref = capture_runtime_manifest(store, roots=roots, process_identity=identity)
    manifest = read_runtime_manifest(store, manifest_ref)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, representation_ref = _representation(store, scratch)
    payload = {
        "schema_version": "harness.genesis_runtime_lock.v1",
        "runtime_lock_sha256": "0" * 64,
        "backend": "genesis",
        "backend_version": "0.3.3",
        "runtime_implementation_sha256": runtime_backend_identity(manifest),
        "loader_contract": "harness.genesis_rigid_loader.v1",
        "requested_profile": "genesis.rigid_scene@1",
        "dependencies": [
            {
                "name": "genesis_runtime_manifest",
                "version": "harness.genesis_runtime_manifest.v1",
                "sha256": manifest_ref.sha256,
            }
        ],
    }
    payload["runtime_lock_sha256"] = canonical_evidence_sha256(payload, "runtime_lock_sha256")
    runtime = GenesisRuntimeLockV1.model_validate(payload)
    runtime_ref = publish(store, tmp_path, runtime, name="genesis-runtime-lock.json", legacy=True)
    value = GenesisRuntimeExecutionRequestV1(
        schema_version="harness.genesis_runtime_execution_request.v1",
        manifest=manifest_ref,
        representation=representation_ref,
        runtime_lock=runtime_ref,
        runner=next(member for member in manifest.members if member.path == runner_path),
    )
    request_ref = publish(store, tmp_path, value)
    return store, roots, manifest, runtime, value, request_ref


def test_request_preflight_binds_new_runtime_lock(execution_request):
    store, _, manifest, runtime, value, request_ref = execution_request
    assert GenesisRuntimeExecutionVerifier(store).verify_request(request_ref) == (
        value,
        manifest,
        runtime,
    )


@pytest.fixture
def observation(execution_request, tmp_path):
    from self_improving.harness.genesis_asset_probe import GenesisAssetProbeProducer
    from self_improving.harness.schemas.genesis_runtime_execution import GenesisRuntimeObservationV1
    from tests.self_improving.harness.test_genesis_asset_probe_runner import (
        ControlledCpuGenesisBackend,
    )

    store, roots, manifest, runtime, value, request_ref = execution_request
    backend = ControlledCpuGenesisBackend()
    backend.implementation_sha256 = runtime.runtime_implementation_sha256
    probe = GenesisAssetProbeProducer(store, backend).produce(
        representation_ref=value.representation, runtime_lock_ref=value.runtime_lock
    )
    path = tmp_path / "stream.txt"
    path.write_bytes(b"controlled fixture, not live execution")
    stream = store.put_file(path, name="stream.txt", media_type="text/plain", schema_version=None)
    members = manifest.members
    result = GenesisRuntimeObservationV1(
        schema_version="harness.genesis_runtime_observation.v1",
        request=request_ref,
        process_identity=manifest.process_identity,
        torch_threads=(1, 1),
        modules=tuple(m for m in members if m.root == "backend"),
        native_files=tuple(m for m in members if m.root == "interpreter"),
        external_native_files=tuple(
            m for m in members if m.root == "native" and m.path != "member"
        ),
        landlock_abi=6,
        filesystem_policy="runtime_roots_readonly_two_verified_gl_files_kernel_devices_run_output_write",
        kernel_devices=("/dev/null", "/dev/urandom"),
        network_policy="socket_syscalls_denied",
        generated_namespaces=("torch.classes", "torch.ops"),
        frozen_modules=(),
        builtin_modules=(),
        other_non_file_modules=(),
        kernel_facts={
            "cpu_name": "controlled fixture CPU",
            "memory_bytes": 1,
            "kernel_release": "fixture",
            "kernel_machine": "x86_64",
            "process_maps_preopened": True,
        },
        probe_input=probe.probe_input_ref,
        load_observation=probe.load_observation_ref,
        load_evidence=probe.load_evidence_ref,
        trace_rows=probe.trace_rows_ref,
        contact_trace=probe.contact_trace_ref,
        probe_report=probe.probe_report_ref,
        stdout=stream,
        stderr=stream,
    )
    return execution_request, result, publish(store, tmp_path, result)


def test_cas_only_verifier_recomputes_controlled_closure_without_live_authority(observation):
    execution_request, value, observation_ref = observation
    store, _, _, _, _, request_ref = execution_request
    result = GenesisRuntimeExecutionVerifier(store).verify_observation(request_ref, observation_ref)
    assert result.observation == value
    assert len(result.probe.rows) == 1001
    assert result.execution_authority is False


def test_receipt_read_does_not_create_live_execution_capability(observation, tmp_path):
    from self_improving.harness.schemas.genesis_runtime_execution import (
        GenesisRuntimeExecutionReceiptV1,
    )

    execution_request, value, observation_ref = observation
    store, _, _, _, _, request_ref = execution_request
    receipt = GenesisRuntimeExecutionReceiptV1(
        schema_version="harness.genesis_runtime_execution_receipt.v1",
        authority="observed_execution_only",
        request=request_ref,
        observation=observation_ref,
        probe_report=value.probe_report,
    )
    receipt_ref = publish(store, tmp_path, receipt)
    closure = GenesisRuntimeExecutionVerifier(store).read_receipt(receipt_ref)
    assert closure.execution_authority is False
    assert not isinstance(closure, LiveGenesisRuntimeExecution)
    with pytest.raises(TypeError):
        LiveGenesisRuntimeExecution(receipt_ref, closure)


def test_producer_refuses_unapproved_manifest_before_child(execution_request):
    store, roots, _, _, value, request_ref = execution_request
    producer = GenesisRuntimeExecutionProducer(
        store, approved_manifest_ref=value.manifest.model_copy(update={"sha256": "0" * 64})
    )
    with pytest.raises(ValueError, match="approved") as failed:
        producer.produce(request_ref, roots=roots)
    assert failed.value.diagnostic_ref.schema_version is None
    diagnostic = json.loads(store.resolve(failed.value.diagnostic_ref).path.read_bytes())
    assert diagnostic["status"] == "failed"
    assert diagnostic["request"]["sha256"] == request_ref.sha256


@pytest.mark.parametrize(
    "change", ["dependency", "implementation", "runner", "whitespace", "schema"]
)
def test_request_drift_is_rejected(execution_request, tmp_path, change):
    store, _, _, runtime, value, request_ref = execution_request
    if change in {"dependency", "implementation"}:
        payload = runtime.model_dump(mode="json")
        if change == "dependency":
            payload["dependencies"][0]["sha256"] = "b" * 64
        else:
            payload["runtime_implementation_sha256"] = "b" * 64
        payload["runtime_lock_sha256"] = canonical_evidence_sha256(payload, "runtime_lock_sha256")
        runtime_ref = publish(
            store,
            tmp_path,
            GenesisRuntimeLockV1.model_validate(payload),
            name="genesis-runtime-lock.json",
            legacy=True,
        )
        value = value.model_copy(update={"runtime_lock": runtime_ref})
    elif change == "runner":
        changed = value.runner.artifact.model_copy(
            update={"sha256": "b" * 64, "uri": "artifact://sha256/" + "b" * 64}
        )
        value = value.model_copy(
            update={"runner": value.runner.model_copy(update={"artifact": changed})}
        )
    if change == "schema":
        invalid = request_ref.model_copy(update={"media_type": "text/plain"})
    else:
        invalid = publish(store, tmp_path, value, legacy=change == "whitespace")
    with pytest.raises(ValueError):
        GenesisRuntimeExecutionVerifier(store).verify_request(invalid)


@pytest.mark.parametrize(
    "change", ["request", "process", "duplicate", "member", "external", "child", "stream"]
)
def test_observed_identity_drift_is_rejected(observation, tmp_path, change):
    execution_request, value, _ = observation
    store, _, _, _, _, request_ref = execution_request
    if change == "request":
        value = value.model_copy(update={"request": request_ref.model_copy(update={"bytes": 0})})
    elif change == "process":
        value = value.model_copy(
            update={
                "process_identity": value.process_identity.model_copy(update={"abi": "different"})
            }
        )
    elif change == "duplicate":
        value = value.model_copy(update={"modules": value.modules + (value.modules[0],)})
    elif change == "member":
        member = value.modules[0]
        artifact = member.artifact.model_copy(update={"bytes": 0})
        value = value.model_copy(
            update={
                "modules": (member.model_copy(update={"artifact": artifact}),) + value.modules[1:]
            }
        )
    elif change == "external":
        value = value.model_copy(update={"external_native_files": value.modules[:2]})
    elif change == "child":
        value = value.model_copy(
            update={
                "modules": tuple(
                    m for m in value.modules if not m.path.endswith("_genesis_cpu_child.py")
                )
            }
        )
    else:
        value = value.model_copy(
            update={"stdout": value.stdout.model_copy(update={"media_type": "text/html"})}
        )
    invalid = publish(store, tmp_path, value)
    with pytest.raises(ValueError):
        GenesisRuntimeExecutionVerifier(store).verify_observation(request_ref, invalid)


def test_receipt_cannot_substitute_another_report(observation, tmp_path):
    from self_improving.harness.schemas.genesis_runtime_execution import (
        GenesisRuntimeExecutionReceiptV1,
    )

    execution_request, value, observation_ref = observation
    store, _, _, _, _, request_ref = execution_request
    receipt = GenesisRuntimeExecutionReceiptV1(
        schema_version="harness.genesis_runtime_execution_receipt.v1",
        authority="observed_execution_only",
        request=request_ref,
        observation=observation_ref,
        probe_report=value.probe_report.model_copy(update={"bytes": 0}),
    )
    with pytest.raises(ValueError):
        GenesisRuntimeExecutionVerifier(store).read_receipt(publish(store, tmp_path, receipt))


def test_request_cannot_select_another_runner():
    invalid = request()
    invalid["runner"]["path"] = "another.py"
    invalid["runner"]["artifact"]["name"] = "backend/another.py"
    with pytest.raises(ValueError):
        GenesisRuntimeExecutionRequestV1.model_validate(invalid)


@pytest.mark.parametrize("change", ["duplicate", "overlap"])
def test_non_file_module_classifications_are_exact(observation, change):
    from self_improving.harness.schemas.genesis_runtime_execution import GenesisRuntimeObservationV1

    _, value, _ = observation
    payload = value.model_dump(mode="json")
    payload["frozen_modules"] = ["os", "os"] if change == "duplicate" else ["os"]
    if change == "overlap":
        payload["builtin_modules"] = ["os"]
    with pytest.raises(ValueError):
        GenesisRuntimeObservationV1.model_validate(payload)


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_RUNTIME_EXECUTION_CONFIG"),
    reason="requires operator-approved staged CPU execution config",
)
def test_formal_runtime_execution_publishes_live_capability_after_fresh_probe():
    from self_improving.harness.schemas.common import ArtifactRef

    configuration = json.loads(Path(os.environ["GOLDEN_RUNTIME_EXECUTION_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(configuration["cas_root"]))
    producer = GenesisRuntimeExecutionProducer(
        store, ArtifactRef.model_validate(configuration["approved_manifest_ref"])
    )
    live = producer.produce(
        ArtifactRef.model_validate(configuration["request_ref"]),
        roots={key: Path(value) for key, value in configuration["roots"].items()},
    )
    assert isinstance(live, LiveGenesisRuntimeExecution)
    assert len(live.closure.probe.rows) == 1001
    assert live.closure.probe.probe_report.simulator_executed is True
    assert GenesisRuntimeExecutionVerifier(store).read_receipt(live.receipt_ref) == live.closure
    (Path(configuration["cas_root"]).parent / "live-execution-receipt-ref.json").write_text(
        live.receipt_ref.model_dump_json() + "\n"
    )


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_RUNTIME_EXECUTION_CONFIG"),
    reason="requires operator-approved staged CPU execution config",
)
def test_formal_child_failure_retains_stderr_after_temporary_directory_cleanup(tmp_path):
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.x2env_compile_evidence import (
        AssetRepresentationV1,
        asset_loader_closure_sha256,
    )

    configuration = json.loads(Path(os.environ["GOLDEN_RUNTIME_EXECUTION_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(configuration["cas_root"]))
    request_ref = ArtifactRef.model_validate(configuration["request_ref"])
    request, _, _ = GenesisRuntimeExecutionVerifier(store).verify_request(request_ref)
    representation = AssetRepresentationV1.model_validate_json(
        store.resolve(request.representation).path.read_bytes()
    )
    source = tmp_path / "invalid.urdf"
    source.write_bytes(b"invalid XML retained as a negative runtime input")
    member = next(
        m for m in representation.members if m.logical_path == representation.root_logical_path
    )
    invalid_ref = store.put_file(
        source,
        name=member.artifact.name,
        media_type=member.artifact.media_type,
        schema_version=member.artifact.schema_version,
    )
    members = tuple(
        m.model_copy(update={"artifact": invalid_ref}) if m == member else m
        for m in representation.members
    )
    payload = representation.model_dump(mode="json")
    payload["members"] = [m.model_dump(mode="json") for m in members]
    payload["loader_closure_sha256"] = asset_loader_closure_sha256(
        representation.subject, representation.root_logical_path, members
    )
    payload["representation_sha256"] = canonical_evidence_sha256(payload, "representation_sha256")
    changed_ref = publish(
        store,
        tmp_path,
        AssetRepresentationV1.model_validate(payload),
        name=request.representation.name,
        legacy=True,
    )
    invalid_request = request.model_copy(update={"representation": changed_ref})
    invalid_request_ref = publish(store, tmp_path, invalid_request)
    producer = GenesisRuntimeExecutionProducer(
        store, ArtifactRef.model_validate(configuration["approved_manifest_ref"])
    )
    with pytest.raises(ValueError) as failed:
        producer.produce(
            invalid_request_ref,
            roots={key: Path(value) for key, value in configuration["roots"].items()},
        )
    diagnostic = json.loads(store.resolve(failed.value.diagnostic_ref).path.read_bytes())
    streams = [ArtifactRef.model_validate(value) for value in diagnostic["failure_artifacts"]]
    (Path(configuration["cas_root"]).parent / "failed-execution-diagnostic-ref.json").write_text(
        failed.value.diagnostic_ref.model_dump_json() + "\n"
    )
    assert {ref.name for ref in streams} == {"stdout.txt", "stderr.txt", "partial-cas.json"}
    assert any(store.resolve(ref).path.read_bytes() for ref in streams if ref.name == "stderr.txt")
    partial_ref = next(ref for ref in streams if ref.name == "partial-cas.json")
    partial = json.loads(store.resolve(partial_ref).path.read_bytes())
    assert partial["complete_probe"] is False
    assert partial["available_artifacts"]
    for value in partial["available_artifacts"]:
        store.resolve(ArtifactRef.model_validate(value))
