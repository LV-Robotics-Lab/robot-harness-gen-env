"""Real Store contracts; only exceptional /proc reads use explicit OS-boundary doubles."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from self_improving.harness.x2env.contracts import ToolResult, X2EnvRequest
from self_improving.harness.x2env.store import Store, process_identity
from tests.self_improving.harness.x2env.test_operation_recovery import orphan, stop_owned_child


def request(tmp_path):
    return X2EnvRequest(
        text="fixture", seed=1, idempotency_key="contract", output_dir=str(tmp_path / "out")
    )


@pytest.mark.parametrize("operation", ["claim", "begin", "complete"])
def test_unknown_workflow_never_creates_a_snapshot_or_operation(tmp_path, operation):
    existing = Store(tmp_path / "existing").submit(request(tmp_path))
    store = Store(tmp_path / "empty")
    with pytest.raises(KeyError):
        if operation == "claim":
            store.claim(existing.workflow_id)
        elif operation == "begin":
            store.begin_operation(existing, "asset.resolve")
        else:
            store.complete_operation(existing, None, None, status="blocked")
    with pytest.raises(KeyError):
        store.status(existing.workflow_id)


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_terminal_workflow_claim_is_exactly_idempotent_after_restart(tmp_path, status):
    store = Store(tmp_path / "state")
    snapshot = store.claim(store.submit(request(tmp_path)).workflow_id)
    final = store.complete_operation(
        snapshot,
        ToolResult(
            operation_id=snapshot.operations[-1].operation_id,
            status=status,
            error_code=None if status == "succeeded" else "fixture_terminal",
        ),
        None,
        status=status,
    )
    restarted = Store(tmp_path / "state")
    assert restarted.claim(final.workflow_id) == final
    assert restarted.status(final.workflow_id) == final


def test_running_operation_cannot_be_completed_without_its_result(tmp_path):
    store = Store(tmp_path / "state")
    snapshot = store.claim(store.submit(request(tmp_path)).workflow_id)
    with pytest.raises(ValueError, match="running operation requires its own result"):
        store.complete_operation(snapshot, None, None, status="blocked")
    assert store.status(snapshot.workflow_id) == snapshot


def test_unreaped_exited_child_is_not_a_live_workflow_owner():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert process_identity(child.pid) is None
    finally:
        child.wait(timeout=5)


@pytest.mark.parametrize("fault", ["pid", "pgid", "command", "operation_symlink"])
def test_invalid_operation_identity_blocks_recovery_without_mutation(tmp_path, fault):
    info = orphan(tmp_path, relative="process.json")
    stop_owned_child(info)
    record = Path(info["record"])
    data = json.loads(record.read_bytes())
    if fault == "pid":
        data["pid"] = True
    elif fault == "pgid":
        data["pgid"] = data["pid"] + 1
    elif fault == "command":
        identity = process_identity(os.getpid())
        data.update(pid=os.getpid(), pgid=os.getpid(), start_ticks=identity.split(":")[1])
    record.write_text(json.dumps(data))
    if fault == "operation_symlink":
        target = record.parent.with_name("moved-operation")
        record.parent.rename(target)
        record.parent.symlink_to(target, target_is_directory=True)
    store = Store(tmp_path / "state")
    before = store.status(info["workflow_id"])
    blocked = store.claim(info["workflow_id"])
    assert blocked.stop_reason == "orphaned_backend_identity_unverified"
    assert blocked.status == "blocked"
    assert blocked.operations == before.operations and blocked.revision == before.revision


def test_reused_pid_outside_recorded_group_does_not_hold_the_workflow(tmp_path):
    info = orphan(tmp_path)
    stop_owned_child(info)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        assert os.getpgid(child.pid) != child.pid
        record = Path(info["record"])
        data = json.loads(record.read_bytes())
        ticks = process_identity(child.pid).split(":")[1]
        data.update(pid=child.pid, pgid=child.pid, start_ticks=str(int(ticks) + 1))
        record.write_text(json.dumps(data))
        store = Store(tmp_path / "state")
        recovered = store.claim(info["workflow_id"])
        assert recovered.status == "active"
        assert recovered.operations[-1].result.error_code == "recoverable_dead_owner"
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize("fault", ["disappeared", "malformed"])
def test_exceptional_proc_read_preserves_recovery_contract(tmp_path, monkeypatch, fault):
    info = orphan(tmp_path)
    stop_owned_child(info)
    store = Store(tmp_path / "state")
    before = store.status(info["workflow_id"])
    read_text = Path.read_text
    # This is an explicit kernel-filesystem boundary double, not an observed kernel failure.
    target = Path("/proc/1/stat")

    def exceptional(path, *args, **kwargs):
        if path == target:
            if fault == "disappeared":
                raise FileNotFoundError("test-only process enumeration race")
            return "test-only malformed kernel stat"
        return read_text(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", exceptional)
        result = store.claim(info["workflow_id"])
    if fault == "malformed":
        assert result.status == "blocked"
        assert result.stop_reason == "orphaned_backend_identity_unverified"
        assert result.operations == before.operations
    else:
        assert result.status == "active"
        assert result.operations[-1].result.error_code == "recoverable_dead_owner"
