"""Fixed runtime launcher refuses malformed requests before execution or CAS writes."""

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
    publish,
)


def test_malformed_execution_request_has_no_execution_or_derived_artifacts(tmp_path):
    source = tmp_path / "malformed.json"
    source.write_bytes(b'{"not":"an execution request"}')
    store = LocalArtifactStore(tmp_path / "cas")
    request = store.put_file(
        source,
        name="request.json",
        media_type="application/json",
        schema_version="harness.genesis_runtime_execution_request.v1",
    )
    before = sorted(path.relative_to(store.root) for path in store.root.rglob("*"))
    launcher = importlib.import_module("self_improving.harness.genesis_runtime_launcher")
    with pytest.raises(launcher.GenesisRuntimeLaunchError, match="execution request"):
        launcher.execute_genesis_runtime_probe(store=store, request_ref=request, roots={})
    assert sorted(path.relative_to(store.root) for path in store.root.rglob("*")) == before


def test_cpu_metadata_binding_uses_kernel_facts_and_restores_external_engine():
    worker = importlib.import_module("self_improving.harness._genesis_cpu_child")

    class TestGenesisAdapter:
        cpu = "test-cpu-backend"
        get_device = object()

    engine = TestGenesisAdapter()
    original = engine.get_device
    facts = {"cpu_name": "Test CPU", "memory_bytes": 8 * 1024**3}
    with pytest.raises(RuntimeError, match="test interruption"):
        with worker.bind_cpu_device_metadata(engine, cpu_device="test-device", facts=facts):
            assert engine.get_device(engine.cpu) == ("test-device", "Test CPU", 8.0, engine.cpu)
            with pytest.raises(ValueError, match="CPU"):
                engine.get_device("test-gpu-backend")
            with pytest.raises(ValueError, match="CPU"):
                engine.get_device(engine.cpu, device_idx=1)
            raise RuntimeError("test interruption")
    assert engine.get_device is original


def test_unrestricted_process_cannot_pass_runtime_constraints(tmp_path):
    worker = importlib.import_module("self_improving.harness._genesis_cpu_child")
    with pytest.raises(RuntimeError, match="network restriction"):
        worker.verify_cpu_runtime_constraints({"backend": tmp_path})


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux seccomp")
def test_network_denial_without_runtime_write_denial_is_rejected(tmp_path):
    source = tmp_path / "self_improving/harness/_genesis_cpu_child.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Writable runtime member in this negative deployment.\n")
    program = r"""
import ctypes, os, sys
from pathlib import Path
from self_improving.harness._genesis_cpu_child import verify_cpu_runtime_constraints
coverage_path = os.environ.get("RUNTIME_CONSTRAINT_COVERAGE")
measurement = None
if coverage_path:
    import coverage
    measurement = coverage.Coverage(data_file=coverage_path, branch=True, config_file=False)
    measurement.start()
class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]
libc = ctypes.CDLL(None, use_errno=True)
assert os.uname().machine == "x86_64"
filters = (Filter * 4)(Filter(0x20,0,0,0), Filter(0x15,0,1,41),
                       Filter(0x06,0,0,0x50001), Filter(0x06,0,0,0x7fff0000))
policy = Program(4, filters)
assert libc.prctl(38,1,0,0,0) == 0
assert libc.prctl(22,2,ctypes.byref(policy),0,0) == 0
try:
    verify_cpu_runtime_constraints({"backend": Path(sys.argv[1])})
except RuntimeError as error:
    assert str(error) == "runtime write restriction is absent"
else:
    raise AssertionError("partial constraints unexpectedly accepted")
finally:
    if measurement:
        measurement.stop()
        measurement.save()
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["missing", "member_drift", "symlink", "native_drift"])
def test_fixed_installed_runner_rejects_deployment_drift_without_execution(
    execution_request,  # noqa: F811
    tmp_path,
    failure,
):
    from self_improving.harness.genesis_runtime_execution import runtime_backend_identity
    from self_improving.harness.genesis_runtime_launcher import (
        GENESIS_RUNTIME_ENVIRONMENT,
        execute_genesis_runtime_probe,
        genesis_runtime_platform_identity,
    )
    from self_improving.harness.genesis_runtime_manifest import (
        capture_runtime_manifest,
        read_runtime_manifest,
    )
    from self_improving.harness.schemas.x2env_compile_evidence import (
        GenesisRuntimeLockV1,
        canonical_evidence_sha256,
    )

    store, roots, manifest, runtime, request, _ = execution_request
    installed = Path(importlib.import_module(execute_genesis_runtime_probe.__module__).__file__)
    for name in ("genesis_runtime_launcher.py", "_genesis_cpu_child.py", "genesis_cpu_backend.py"):
        target = roots["backend"] / "self_improving/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(installed.with_name(name), target)
    for name in ("libGLX.so.0.0.0", "libOpenGL.so.0.0.0"):
        target = roots["native"] / "usr/lib/x86_64-linux-gnu" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"different native deployment")
    process = manifest.process_identity.model_copy(
        update={
            "environment": tuple(sorted(GENESIS_RUNTIME_ENVIRONMENT.items())),
            "platform": genesis_runtime_platform_identity(),
        }
    )
    manifest_ref = capture_runtime_manifest(store, roots=roots, process_identity=process)
    manifest = read_runtime_manifest(store, manifest_ref)
    payload = runtime.model_dump(mode="json")
    payload["runtime_implementation_sha256"] = runtime_backend_identity(manifest)
    payload["dependencies"][0]["sha256"] = manifest_ref.sha256
    payload["runtime_lock_sha256"] = canonical_evidence_sha256(payload, "runtime_lock_sha256")
    runtime_ref = publish(
        store, tmp_path, GenesisRuntimeLockV1.model_validate(payload), legacy=True
    )
    runner = next(
        member
        for member in manifest.members
        if member.root == "backend"
        and member.path == "self_improving/harness/genesis_runtime_launcher.py"
    )
    request_ref = publish(
        store,
        tmp_path,
        request.model_copy(
            update={"manifest": manifest_ref, "runtime_lock": runtime_ref, "runner": runner}
        ),
    )
    member_path = next(path for path in roots["stdlib"].rglob("*") if path.is_file())
    if failure == "missing":
        member_path.unlink()
    elif failure == "member_drift":
        member_path.write_bytes(b"changed after capture")
    elif failure == "symlink":
        member_path.unlink()
        member_path.symlink_to(installed)
    before = set(store.root.rglob("*"))
    expected = {
        "missing": "root must not be empty",
        "member_drift": "inventory or bytes differ",
        "symlink": "regular files",
        "native_drift": "native identity differs",
    }[failure]
    with pytest.raises(ValueError, match=expected):
        execute_genesis_runtime_probe(store=store, request_ref=request_ref, roots=roots)
    assert set(store.root.rglob("*")) == before


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_RUNTIME_EXECUTION_CONFIG"),
    reason="requires the real operator-approved CPU runtime",
)
def test_postflight_deployment_drift_retains_complete_physical_probe(tmp_path):
    from self_improving.harness.genesis_runtime_execution import GenesisRuntimeExecutionProducer
    from self_improving.harness.schemas.common import ArtifactRef

    config = json.loads(Path(os.environ["GOLDEN_RUNTIME_EXECUTION_CONFIG"]).read_text())
    roots = {key: Path(value) for key, value in config["roots"].items()}
    backend = tmp_path / "backend"
    shutil.copytree(roots["backend"], backend)
    roots["backend"] = backend
    store = LocalArtifactStore(Path(config["cas_root"]))
    producer = GenesisRuntimeExecutionProducer(
        store, ArtifactRef.model_validate(config["approved_manifest_ref"])
    )
    changed = threading.Event()
    finished = threading.Event()

    def change_owned_deployment_after_probe():
        while not finished.wait(0.01):
            for invocation_path in Path("/tmp").glob("genesis-fixed-execution-*/invocation.json"):
                try:
                    invocation = json.loads(invocation_path.read_text())
                    if invocation["roots"]["backend"] != str(backend):
                        continue
                    if not (invocation_path.parent / "partial-probe.json").exists():
                        continue
                    (backend / "postflight-drift.txt").write_text("added after physical probe\n")
                    changed.set()
                    return
                except (OSError, ValueError):
                    continue

    observer = threading.Thread(target=change_owned_deployment_after_probe, daemon=True)
    observer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match="inventory or bytes differ") as failed:
            producer.produce(ArtifactRef.model_validate(config["request_ref"]), roots=roots)
    finally:
        finished.set()
        observer.join(timeout=2)
    assert changed.is_set()
    diagnostic = json.loads(store.resolve(failed.value.diagnostic_ref).path.read_bytes())
    refs = [ArtifactRef.model_validate(value) for value in diagnostic["failure_artifacts"]]
    assert {ref.name for ref in refs} == {"stdout.txt", "stderr.txt", "partial-probe.json"}
    partial = next(ref for ref in refs if ref.name == "partial-probe.json")
    probe_refs = json.loads(store.resolve(partial).path.read_bytes())
    assert set(probe_refs) == {
        "probe_input",
        "load_observation",
        "load_evidence",
        "trace_rows",
        "contact_trace",
        "probe_report",
    }
    for value in probe_refs.values():
        store.resolve(ArtifactRef.model_validate(value))
    rows = store.resolve(ArtifactRef.model_validate(probe_refs["trace_rows"])).path.read_bytes()
    assert len(rows.splitlines()) == 1001
    output = Path(config["cas_root"]).parent / "postflight-drift-summary.json"
    output.write_text(
        json.dumps(
            {
                "diagnostic_ref": failed.value.diagnostic_ref.model_dump(mode="json"),
                "elapsed_seconds": time.monotonic() - started,
                "rows": 1001,
                "production_receipt_issued": False,
            },
            indent=2,
        )
        + "\n"
    )
