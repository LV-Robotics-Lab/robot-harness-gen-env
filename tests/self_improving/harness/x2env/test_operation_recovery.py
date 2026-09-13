"""Actual short process boundary doubles, never Genesis or a live model invocation."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from self_improving.harness.x2env.store import Store


def orphan(
    tmp_path, capability="asset.resolve", relative="web/item-visual/process.json", fork=False
):
    worker = r"""
import json,os,sys,subprocess,time
from pathlib import Path
from self_improving.harness.x2env.store import Store
from self_improving.harness.x2env.contracts import X2EnvRequest,ToolResult
state=Path(sys.argv[1]); store=Store(state)
s=store.submit(X2EnvRequest(text='fixture',seed=1,idempotency_key='recovery',output_dir=str(state/'output')))
s=store.claim(s.workflow_id)
ref=store.write_artifact(b'unit input','text/plain')
s=store.complete_operation(s,ToolResult(operation_id=s.operations[-1].operation_id,status='succeeded',outputs=(ref,)),ref,status='active')
s=store.begin_operation(s,sys.argv[2])
record=state/'attempts'/s.workflow_id/s.operations[-1].operation_id/sys.argv[3]
record.parent.mkdir(parents=True)
child_code='import time;time.sleep(60)'
if sys.argv[4]=='fork':
    child_code='''import os,time,json,sys
from pathlib import Path
root=Path(sys.argv[1])
while not (root/'record-ready').exists(): time.sleep(.01)
if os.fork(): os._exit(0)
fields=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
(root/'grandchild.json').write_text(json.dumps({'pid':os.getpid(),'ticks':fields[19],'pgid':os.getpgrp()}))
time.sleep(60)
'''
p=subprocess.Popen([sys.executable,'-c',child_code,str(record.parent)],
    cwd=record.parent,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
ticks=Path(f'/proc/{p.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
record.write_text(json.dumps({'pid':p.pid,'pgid':p.pid,'start_ticks':ticks,'attempt_root':str(record.parent)}))
(record.parent/'record-ready').touch()
if sys.argv[4]=='fork':
    while not (record.parent/'grandchild.json').exists(): time.sleep(.01)
p.poll()
print(json.dumps({'workflow_id':s.workflow_id,'record':str(record),'pid':p.pid,'ticks':ticks,'pgid':p.pid}),flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(tmp_path / "state"),
            capability,
            relative,
            "fork" if fork else "single",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def stop_owned_child(info):
    process = Path(f"/proc/{info['pid']}")
    if process.exists() and not info.get("stopped"):
        # This PID came only from the just-created test worker, never a historical service.
        assert str(Path(info["record"]).parent).encode() in (process / "cmdline").read_bytes()
        fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
        assert fields[19] == info["ticks"] and int(fields[2]) == info["pgid"] == info["pid"]
        os.killpg(info["pid"], signal.SIGTERM)
        info["stopped"] = True
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                not process.exists()
                or (process / "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
            ):
                return
            time.sleep(0.01)
        raise AssertionError("test-owned child did not exit")


def test_resolver_orphan_blocks_reclaim_without_changing_operation(tmp_path):
    info = orphan(tmp_path)
    try:
        store = Store(tmp_path / "state")
        before = store.status(info["workflow_id"])
        after = store.claim(info["workflow_id"])
        assert after.status == "blocked"
        assert after.stop_reason == "orphaned_backend_still_running"
        assert after.operations == before.operations
        assert after.revision == before.revision
        assert after.required_resources == (info["record"],)
    finally:
        stop_owned_child(info)


@pytest.mark.parametrize(
    "capability,path",
    [
        ("asset.resolve", "reconstruction/item-generation/reconstruction-process.json"),
        ("asset.resolve", "previews/uuid/runtime/process.json"),
        ("x2env.replay", "baseline/process.json"),
        ("x2env.replay", "half_dt/process.json"),
        ("codex.diagnose", "process.json"),
    ],
)
def test_each_recorded_operation_child_blocks_until_original_instance_exits(
    tmp_path, capability, path
):
    info = orphan(tmp_path, capability, path)
    try:
        store = Store(tmp_path / "state")
        blocked = store.claim(info["workflow_id"])
        assert blocked.status == "blocked"
        assert blocked.required_resources == (info["record"],)
        assert store.claim(info["workflow_id"]) == blocked
        stop_owned_child(info)
        recovered = store.claim(info["workflow_id"])
        assert recovered.status == "active"
        assert recovered.required_resources == ()
        assert recovered.operations[-1].result.error_code == "recoverable_dead_owner"
        assert recovered.operations[-1].operation_id == blocked.operations[-1].operation_id
        assert recovered.revision == blocked.revision
    finally:
        stop_owned_child(info)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_ticks",
        "invalid_ticks",
        "wrong_directory",
        "malformed",
        "oversized",
        "symlink",
        "missing",
    ],
)
def test_live_or_unverifiable_process_is_never_reclaimed_by_pid_alone(tmp_path, fault):
    info = orphan(tmp_path)
    try:
        record = Path(info["record"])
        data = json.loads(record.read_bytes())
        if fault == "missing_ticks":
            data.pop("start_ticks")
        if fault == "invalid_ticks":
            data["start_ticks"] = True
        if fault == "wrong_directory":
            data["attempt_root"] = str(tmp_path / "other")
        record.write_text(json.dumps(data))
        if fault == "malformed":
            record.write_bytes(b"partial{")
        if fault == "oversized":
            record.write_bytes(b" " * 65537)
        if fault == "symlink":
            old = record.with_name("source-record.json")
            record.rename(old)
            record.symlink_to(old)
        if fault == "missing":
            record.unlink()
        store = Store(tmp_path / "state")
        before = store.status(info["workflow_id"])
        after = store.claim(info["workflow_id"])
        assert after.status == "blocked"
        assert after.stop_reason == "orphaned_backend_identity_unverified"
        assert after.operations == before.operations and after.revision == before.revision
    finally:
        stop_owned_child(info)


def test_reused_pid_with_different_start_ticks_does_not_bind_unrelated_process(tmp_path):
    info = orphan(tmp_path)
    try:
        record = Path(info["record"])
        data = json.loads(record.read_bytes())
        data["start_ticks"] = str(int(data["start_ticks"]) + 1)
        record.write_text(json.dumps(data))
        store = Store(tmp_path / "state")
        recovered = store.claim(info["workflow_id"])
        assert recovered.status == "blocked"
        assert recovered.stop_reason == "orphaned_backend_identity_unverified"
        os.kill(info["pid"], 0)  # The mismatching process was not signalled.
    finally:
        stop_owned_child(info)


def test_dead_session_leader_does_not_hide_live_grandchild_in_exact_group(tmp_path):
    info = orphan(tmp_path, "x2env.replay", "baseline/process.json", fork=True)
    child = json.loads((Path(info["record"]).parent / "grandchild.json").read_bytes())
    try:
        store = Store(tmp_path / "state")
        before = store.status(info["workflow_id"])
        blocked = store.claim(info["workflow_id"])
        assert blocked.status == "blocked"
        assert blocked.stop_reason == "orphaned_backend_identity_unverified"
        assert blocked.operations == before.operations
    finally:
        fields = Path(f"/proc/{child['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        assert fields[19] == child["ticks"] and int(fields[2]) == child["pgid"] == info["pid"]
        os.kill(child["pid"], signal.SIGTERM)


@pytest.mark.parametrize("fault", ["symlink_directory", "depth", "count"])
def test_process_discovery_is_bounded_and_does_not_follow_external_roots(tmp_path, fault):
    info = orphan(tmp_path, "x2env.replay", "process.json")
    try:
        root = Path(info["record"]).parent
        if fault == "symlink_directory":
            outside = tmp_path / "outside-cas"
            outside.mkdir()
            (outside / "process.json").write_text("not consulted")
            (root / "linked-runtime").symlink_to(outside, target_is_directory=True)
        elif fault == "depth":
            root.joinpath(*["nested"] * 17).mkdir(parents=True)
        else:
            for i in range(8193):
                (root / f"entry-{i}").touch()
        store = Store(tmp_path / "state")
        after = store.claim(info["workflow_id"])
        assert (
            after.status == "blocked"
            and after.stop_reason == "orphaned_backend_identity_unverified"
        )
        assert after.operations[-1].status == "running"
    finally:
        stop_owned_child(info)


@pytest.mark.parametrize("fault", ["denied", "budget"])
def test_unverifiable_exact_group_scan_never_claims_a_dead_owner_operation(
    tmp_path, monkeypatch, fault
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    info = orphan(tmp_path)
    stop_owned_child(info)
    scandir = os.scandir

    @contextmanager
    def limited(path):
        if str(path) == "/proc":
            if fault == "denied":
                raise PermissionError("explicit filesystem boundary denial")
            yield (SimpleNamespace(name="nonpid") for _ in range(32769))
        else:
            with scandir(path) as entries:
                yield entries

    monkeypatch.setattr(os, "scandir", limited)
    store = Store(tmp_path / "state")
    before = store.status(info["workflow_id"])
    blocked = store.claim(info["workflow_id"])
    assert blocked.status == "blocked"
    assert blocked.stop_reason == "orphaned_backend_identity_unverified"
    assert blocked.operations == before.operations
