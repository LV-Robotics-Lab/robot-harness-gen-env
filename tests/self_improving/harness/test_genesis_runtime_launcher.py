"""Fixed runtime launcher refuses malformed requests before execution or CAS writes."""

import importlib

import pytest

from self_improving.harness.artifacts import LocalArtifactStore


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
            raise RuntimeError("test interruption")
    assert engine.get_device is original


def test_unrestricted_process_cannot_pass_runtime_constraints(tmp_path):
    worker = importlib.import_module("self_improving.harness._genesis_cpu_child")
    with pytest.raises(RuntimeError, match="network restriction"):
        worker.verify_cpu_runtime_constraints({"backend": tmp_path})
