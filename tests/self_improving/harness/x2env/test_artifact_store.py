"""Artifact persistence seam: immutable content identities, verified on read."""

import pytest

from self_improving.harness.x2env.store import Store


def test_artifacts_are_reused_across_restart_and_verified_on_read(tmp_path):
    store = Store(tmp_path)
    ref = store.write_artifact(b"abc", "text/plain")
    assert ref.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert ref.size_bytes == 3
    restarted = Store(tmp_path)
    assert restarted.write_artifact(b"abc", "text/plain") == ref
    assert restarted.read_artifact(ref) == b"abc"
    path = tmp_path / "cas" / ref.sha256[:2] / ref.sha256
    path.write_bytes(b"def")
    with pytest.raises(ValueError, match="integrity"):
        restarted.read_artifact(ref)
    with pytest.raises(ValueError, match="integrity"):
        restarted.write_artifact(b"abc", "text/plain")


@pytest.mark.parametrize(
    "fault", ["wrong_operation", "ownerless", "parallel_begin", "missing_output", "stale_head"]
)
def test_operation_advancement_requires_owned_head_and_readable_outputs(tmp_path, fault):
    from self_improving.harness.x2env.contracts import ArtifactRef, ToolResult, X2EnvRequest

    store = Store(tmp_path / "state")
    initial = store.submit(
        X2EnvRequest(
            text="mouse", seed=1, idempotency_key="operation", output_dir=str(tmp_path / "package")
        )
    )
    snapshot = store.claim(initial.workflow_id)
    result = ToolResult(operation_id=snapshot.operations[-1].operation_id, status="succeeded")
    if fault == "wrong_operation":
        result = result.model_copy(update={"operation_id": "not-the-running-operation"})
    elif fault == "ownerless":
        snapshot = initial
    elif fault == "missing_output":
        result = result.model_copy(
            update={
                "outputs": (ArtifactRef(sha256="f" * 64, size_bytes=1, media_type="text/plain"),)
            }
        )
    elif fault == "stale_head":
        snapshot = snapshot.model_copy(update={"operations": ()})
    before = store.status(initial.workflow_id)
    with pytest.raises((ValueError, FileNotFoundError)):
        if fault == "parallel_begin":
            store.begin_operation(snapshot, "codex.interpret")
        else:
            store.complete_operation(snapshot, result, None, status="active")
    assert store.status(initial.workflow_id) == before


def test_completed_operation_cannot_be_completed_again_or_ownerless_advanced(tmp_path):
    from self_improving.harness.x2env.contracts import ToolResult, X2EnvRequest

    store = Store(tmp_path / "state")
    initial = store.submit(
        X2EnvRequest(
            text="mouse", seed=1, idempotency_key="duplicate", output_dir=str(tmp_path / "package")
        )
    )
    snapshot = store.claim(initial.workflow_id)
    result = ToolResult(operation_id=snapshot.operations[-1].operation_id, status="succeeded")
    completed = store.complete_operation(snapshot, result, None, status="active")
    with pytest.raises(ValueError):
        store.complete_operation(completed, result, None, status="active")
    stopped = store.complete_operation(completed, None, None, status="blocked")
    with pytest.raises(ValueError):
        store.begin_operation(stopped, "codex.interpret")


def test_another_real_process_cannot_complete_current_owner_operation(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from self_improving.harness.x2env.contracts import X2EnvRequest

    store = Store(tmp_path / "state")
    initial = store.submit(
        X2EnvRequest(
            text="mouse", seed=1, idempotency_key="owner", output_dir=str(tmp_path / "package")
        )
    )
    owned = store.claim(initial.workflow_id)
    code = """
import sys
from pathlib import Path
from self_improving.harness.x2env.store import Store
from self_improving.harness.x2env.contracts import ToolResult
s=Store(Path(sys.argv[1])); snapshot=s.status(sys.argv[2])
try:
 s.complete_operation(snapshot,ToolResult(operation_id=snapshot.operations[-1].operation_id,status='succeeded'),None,status='active')
except ValueError:
 print('owner rejected')
else:
 raise RuntimeError('foreign process advanced workflow')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "state"), initial.workflow_id],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[4])},
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.stdout.strip() == "owner rejected"
    assert store.status(initial.workflow_id) == owned


def test_two_concurrent_begin_calls_cannot_create_parallel_running_heads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from self_improving.harness.x2env.contracts import ToolResult, X2EnvRequest

    store = Store(tmp_path / "state")
    initial = store.submit(
        X2EnvRequest(
            text="mouse", seed=1, idempotency_key="begin", output_dir=str(tmp_path / "package")
        )
    )
    claimed = store.claim(initial.workflow_id)
    ready = store.complete_operation(
        claimed,
        ToolResult(operation_id=claimed.operations[-1].operation_id, status="succeeded"),
        None,
        status="active",
    )

    def begin(_):
        try:
            return store.begin_operation(ready, "codex.interpret")
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, range(2)))
    assert sum(result is not None for result in results) == 1
    assert sum(op.status == "running" for op in store.status(initial.workflow_id).operations) == 1
