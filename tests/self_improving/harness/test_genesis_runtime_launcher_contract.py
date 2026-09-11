"""Public launcher preflight rejects non-fixed execution configurations."""

import pytest

from self_improving.harness.genesis_runtime_execution import runtime_backend_identity
from self_improving.harness.genesis_runtime_launcher import (
    GENESIS_RUNTIME_ENVIRONMENT,
    GenesisRuntimeLaunchError,
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
from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
    publish,
)


def test_nonfixed_environment_is_rejected_without_cas_write(execution_request):  # noqa: F811
    store, roots, _, _, _, request_ref = execution_request
    before = set(store.root.rglob("*"))
    with pytest.raises(GenesisRuntimeLaunchError, match="environment"):
        execute_genesis_runtime_probe(store=store, request_ref=request_ref, roots=roots)
    assert set(store.root.rglob("*")) == before


@pytest.mark.parametrize("platform_drift", [False, True])
def test_fixed_profile_rejects_platform_drift_or_arbitrary_backend(
    execution_request,  # noqa: F811
    tmp_path,
    platform_drift,
):
    store, roots, manifest, runtime, request, _ = execution_request
    process = manifest.process_identity.model_copy(
        update={
            "environment": tuple(sorted(GENESIS_RUNTIME_ENVIRONMENT.items())),
            "platform": "different-kernel"
            if platform_drift
            else genesis_runtime_platform_identity(),
        }
    )
    manifest_ref = capture_runtime_manifest(store, roots=roots, process_identity=process)
    manifest = read_runtime_manifest(store, manifest_ref)
    payload = runtime.model_dump(mode="json")
    payload["runtime_implementation_sha256"] = runtime_backend_identity(manifest)
    payload["dependencies"][0]["sha256"] = manifest_ref.sha256
    payload["runtime_lock_sha256"] = canonical_evidence_sha256(payload, "runtime_lock_sha256")
    runtime_ref = publish(
        store,
        tmp_path,
        GenesisRuntimeLockV1.model_validate(payload),
        name="genesis-runtime-lock.json",
        legacy=True,
    )
    request = request.model_copy(update={"manifest": manifest_ref, "runtime_lock": runtime_ref})
    request_ref = publish(store, tmp_path, request)
    before = set(store.root.rglob("*"))
    expected = "kernel platform" if platform_drift else "installed Harness"
    with pytest.raises(GenesisRuntimeLaunchError, match=expected):
        execute_genesis_runtime_probe(store=store, request_ref=request_ref, roots=roots)
    assert set(store.root.rglob("*")) == before
