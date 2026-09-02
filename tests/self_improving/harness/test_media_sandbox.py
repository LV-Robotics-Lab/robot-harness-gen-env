from __future__ import annotations

import dataclasses
import json
import os
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

import self_improving.harness.media_sandbox as media_sandbox_module
from self_improving.harness.media_sandbox import (
    MediaSandbox,
    NativeCgroupSandbox,
    SandboxCapture,
    SandboxError,
    SandboxPolicy,
    SandboxReason,
)

_ROOT = Path(__file__).parents[3]
_LAUNCHER_SOURCE = _ROOT / "self_improving/harness/native/media_sandbox.c"
_STATIC_FFMPEG = Path(
    "/home/jingxiang/miniconda3/envs/robotwin-5090/lib/python3.10/site-packages/"
    "imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
)


@pytest.fixture(scope="session")
def static_launcher(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("native-media-sandbox") / "media-sandbox"
    subprocess.run(
        [
            "cc",
            "-std=c17",
            "-O2",
            "-static",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_LAUNCHER_SOURCE),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture(scope="session")
def static_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("native-media-payload")
    source = root / "payload.c"
    output = root / "payload"
    source.write_text(
        r"""
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/random.h>
#include <unistd.h>

int main(void) {
    char byte = 0;
    if (read(4, &byte, 1) != 1 || byte != 'M') return 10;
    errno = 0;
    int host = open("/etc/passwd", O_RDONLY);
    if (host >= 0 || (errno != EACCES && errno != EPERM)) return 11;
    errno = 0;
    int network = socket(AF_INET, SOCK_STREAM, 0);
    if (network >= 0 || errno != EPERM) return 12;
    errno = 0;
    char random_byte = 0;
    if (getrandom(&random_byte, 1, 0) >= 0 || errno != EPERM) return 14;
    if (write(STDOUT_FILENO, "isolated\n", 9) != 9) return 13;
    return 0;
}
""",
        encoding="utf-8",
    )
    subprocess.run(
        ["cc", "-std=c17", "-O2", "-static", str(source), "-o", str(output)],
        check=True,
        capture_output=True,
    )
    return output


@pytest.fixture(scope="session")
def static_behavior_payload(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("native-media-behavior")
    source = root / "behavior.c"
    output = root / "behavior"
    source.write_text(
        r"""
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) return 20;
    if (strcmp(argv[1], "spin") == 0) {
        volatile uint64_t value = 0;
        for (;;) value++;
    }
    if (strcmp(argv[1], "output") == 0) {
        char block[4096] = {0};
        for (;;) if (write(STDOUT_FILENO, block, sizeof(block)) < 0) return 0;
    }
    if (strcmp(argv[1], "memory") == 0) {
        size_t bytes = 128U * 1024U * 1024U;
        volatile unsigned char *memory = malloc(bytes);
        if (memory == NULL) return 21;
        for (size_t offset = 0; offset < bytes; offset += 4096) memory[offset] = 1;
        return 22;
    }
    if (strcmp(argv[1], "pids") == 0) {
        pid_t child = fork();
        if (child == 0) for (;;) pause();
        if (child < 0) return 23;
        pid_t rejected = fork();
        if (rejected == 0) _exit(0);
        int rejected_errno = errno;
        (void)kill(child, SIGKILL);
        (void)waitpid(child, NULL, 0);
        if (rejected >= 0) (void)waitpid(rejected, NULL, 0);
        return rejected < 0 && rejected_errno == EAGAIN ? 0 : 24;
    }
    if (strcmp(argv[1], "sigsys") == 0) {
        (void)syscall(0x40000000U);
        return 25;
    }
    if (strcmp(argv[1], "orphan") == 0) {
        pid_t child = fork();
        if (child == 0) {
            (void)close(STDOUT_FILENO);
            (void)close(STDERR_FILENO);
            for (;;) pause();
        }
        return child < 0 ? 26 : 0;
    }
    return 27;
}
""",
        encoding="utf-8",
    )
    subprocess.run(
        ["cc", "-std=c17", "-O2", "-static", str(source), "-o", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def test_native_sandbox_rejects_a_dynamic_decoder_before_touching_cgroup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "launcher.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    with pytest.raises(SandboxError) as caught:
        NativeCgroupSandbox(
            launcher=Path("/usr/bin/true"),
            launcher_source=source,
            ffmpeg=Path("/usr/bin/true"),
            delegated_cgroup_root=tmp_path,
        )

    assert caught.value.reason is SandboxReason.TOOL_NOT_STATIC
    assert str(tmp_path) not in str(caught.value)


def test_sandbox_policy_and_identity_records_are_immutable_and_canonical() -> None:
    policy = SandboxPolicy()

    assert dataclasses.is_dataclass(policy)
    assert policy.__dataclass_params__.frozen is True
    document = json.loads(policy.canonical_bytes)
    assert document == {
        "cgroup": {
            "cpu_period_us": 100000,
            "cpu_quota_us": 100000,
            "max_depth": 0,
            "max_descendants": 0,
            "memory_bytes": 1073741824,
            "memory_oom_group": True,
            "pids": 32,
            "swap_bytes": 0,
        },
        "process": {
            "max_output_bytes": 16777216,
            "timeout_seconds": 30.0,
        },
        "rlimit": {
            "address_space_bytes": 1073741824,
            "core_bytes": 0,
            "cpu_seconds": 30,
            "file_bytes": 16777216,
            "open_files": 64,
            "processes": 4096,
        },
        "schema_version": "harness.media_sandbox_policy.v1",
    }
    assert len(policy.sha256) == 64


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("timeout_seconds", 0),
        ("timeout_seconds", float("inf")),
        ("max_output_bytes", True),
        ("address_space_bytes", 0),
        ("cpu_seconds", -1),
        ("open_files", 15),
        ("processes", "32"),
        ("file_bytes", 0),
        ("core_bytes", 1),
        ("memory_bytes", 0),
        ("swap_bytes", 1),
        ("pids", 0),
        ("cpu_quota_us", 0),
        ("cpu_period_us", 0),
        ("memory_oom_group", False),
        ("max_depth", 1),
        ("max_descendants", 1),
    ),
)
def test_sandbox_policy_rejects_unsafe_or_ambiguous_limits(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(SandboxError) as caught:
        dataclasses.replace(SandboxPolicy(), **{field_name: value})
    assert caught.value.reason is SandboxReason.CONFIGURATION_INVALID


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("launcher_sha256", "x" * 64),
        ("launcher_bytes", 0),
        ("landlock_abi", 5),
        ("kernel_architecture", ""),
        ("kernel_release", 7),
        ("policy", object()),
        ("schema_version", "v4"),
    ),
)
def test_native_identity_rejects_unbound_fields(field_name: str, value: object) -> None:
    identity = {
        "launcher_sha256": "1" * 64,
        "launcher_bytes": 1,
        "launcher_source_sha256": "2" * 64,
        "launcher_source_bytes": 2,
        "ffmpeg_sha256": "3" * 64,
        "ffmpeg_bytes": 3,
        "implementation_sha256": "4" * 64,
        "implementation_bytes": 4,
        "landlock_abi": 8,
        "kernel_architecture": "x86_64",
        "kernel_release": "test",
        "policy": SandboxPolicy(),
        "schema_version": "harness.native_media_sandbox_identity.v3",
    }
    identity[field_name] = value
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module.NativeSandboxIdentity(**identity)  # type: ignore[arg-type]
    assert caught.value.reason is SandboxReason.CONFIGURATION_INVALID


def test_native_sandbox_identity_binds_static_tools_source_kernel_and_policy(
    tmp_path: Path,
    static_launcher: Path,
) -> None:
    if not _STATIC_FFMPEG.is_file():
        pytest.skip("pinned static ffmpeg is not installed")
    first = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=_STATIC_FFMPEG,
        delegated_cgroup_root=tmp_path,
    ).identity
    second = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=_STATIC_FFMPEG,
        delegated_cgroup_root=tmp_path / "a-different-deployment-path",
    ).identity
    document = json.loads(first.canonical_bytes)

    assert first == second
    assert first.sha256 == second.sha256
    assert document["schema_version"] == "harness.native_media_sandbox_identity.v3"
    assert document["landlock_abi"] >= 5
    assert document["kernel"]["architecture"] == "x86_64"
    assert document["kernel"]["release"]
    assert document["launcher"]["sha256"] != document["launcher_source"]["sha256"]
    assert document["ffmpeg"]["sha256"]
    assert document["policy_sha256"] == SandboxPolicy().sha256
    assert str(tmp_path).encode() not in first.canonical_bytes
    assert dataclasses.is_dataclass(first)
    assert first.__dataclass_params__.frozen is True


def test_constructor_rejects_an_unqualified_landlock_abi(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_sandbox_module, "_probe_landlock_abi", lambda launcher: 5)
    with pytest.raises(SandboxError) as caught:
        NativeCgroupSandbox(
            launcher=static_launcher,
            launcher_source=_LAUNCHER_SOURCE,
            ffmpeg=static_payload,
            delegated_cgroup_root=tmp_path,
        )
    assert caught.value.reason is SandboxReason.SANDBOX_UNAVAILABLE


def test_constructor_accepts_a_future_backward_compatible_landlock_abi(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_sandbox_module, "_probe_landlock_abi", lambda launcher: 9)

    identity = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=static_payload,
        delegated_cgroup_root=tmp_path,
    ).identity

    assert identity.landlock_abi == 9


def test_run_rejects_invalid_descriptors_and_argument_shapes_before_cgroup_access(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
) -> None:
    sandbox = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=static_payload,
        delegated_cgroup_root=tmp_path,
    )
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    closed_fd = os.open(tmp_path / "closed", os.O_RDWR | os.O_CREAT, 0o600)
    os.close(closed_fd)
    cases = (
        (-1, ()),
        (True, ()),
        (closed_fd, ()),
        (read_fd, ()),
        (0, []),
        (0, (1,)),
        (0, ("bad\0argument",)),
    )
    try:
        for descriptor, arguments in cases:
            with pytest.raises(SandboxError) as caught:
                sandbox.run(descriptor, arguments)  # type: ignore[arg-type]
            assert caught.value.reason is SandboxReason.CONFIGURATION_INVALID
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_production_run_fails_closed_outside_a_preconfigured_delegated_topology(
    tmp_path: Path,
    static_launcher: Path,
) -> None:
    if not _STATIC_FFMPEG.is_file():
        pytest.skip("pinned static ffmpeg is not installed")
    current_relative = Path(Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1])
    current_cgroup = Path("/sys/fs/cgroup") / current_relative.relative_to("/")
    sandbox = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=_STATIC_FFMPEG,
        delegated_cgroup_root=current_cgroup,
    )
    media = tmp_path / "media"
    media.write_bytes(b"not decoded because isolation is unavailable")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(SandboxError) as caught:
            sandbox.run(descriptor, ("-version",))
    finally:
        os.close(descriptor)

    assert caught.value.reason is SandboxReason.CGROUP_UNAVAILABLE
    assert "preconfigured delegated cgroup" in str(caught.value)


def test_launcher_blocks_until_go_then_landlock_and_seccomp_hold_exact_fds(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
) -> None:
    media = tmp_path / "media"
    media.write_bytes(b"M")
    media_fd = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    payload_fd = os.open(static_payload, os.O_RDONLY | os.O_CLOEXEC)
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
    command = (
        str(static_launcher),
        "--run",
        str(status_write),
        str(gate_read),
        str(payload_fd),
        str(media_fd),
        str(1024 * 1024 * 1024),
        "30",
        "64",
        "32",
        str(16 * 1024 * 1024),
        "0",
        "--",
        "media-decoder",
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        pass_fds=(status_write, gate_read, payload_fd, media_fd),
        env={"LANG": "C"},
    )
    os.close(status_write)
    os.close(gate_read)
    try:
        ready, _, _ = select.select([status_read], [], [], 2.0)
        assert ready == [status_read]
        assert os.read(status_read, 1) == b"R"
        assert process.poll() is None
        os.write(gate_write, b"G")
        stdout, stderr = process.communicate(timeout=3.0)
        assert process.returncode == 0
        assert stdout == b"isolated\n"
        assert stderr == b""
        assert os.read(status_read, 1) == b""
    finally:
        for descriptor in (media_fd, payload_fd, status_read, gate_write):
            os.close(descriptor)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1.0)


def test_native_adapter_runs_only_inside_delegated_job_and_cleans_it(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
) -> None:
    delegated = os.environ.get("MEDIA_SANDBOX_DELEGATED_ROOT")
    if not delegated:
        pytest.skip("requires a preconfigured delegated cgroup supervisor/jobs topology")
    sandbox: MediaSandbox = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=static_payload,
        delegated_cgroup_root=Path(delegated),
    )
    media = tmp_path / "media"
    media.write_bytes(b"M")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        capture = sandbox.run(descriptor, ())
    finally:
        os.close(descriptor)

    assert isinstance(capture, SandboxCapture)
    assert capture.exit_code == 0
    assert capture.stdout == b"isolated\n"
    assert capture.stderr == b""
    assert capture.metrics.memory_peak_bytes > 0
    assert capture.metrics.memory_events
    assert capture.metrics.pids_peak >= 1
    assert tuple(child for child in (Path(delegated) / "jobs").iterdir() if child.is_dir()) == ()


def _delegated_root() -> Path:
    delegated = os.environ.get("MEDIA_SANDBOX_DELEGATED_ROOT")
    if not delegated:
        pytest.skip("requires a preconfigured delegated cgroup supervisor/jobs topology")
    return Path(delegated)


def _sandbox_for_payload(
    *,
    launcher: Path,
    payload: Path,
    policy: SandboxPolicy,
) -> NativeCgroupSandbox:
    return NativeCgroupSandbox(
        launcher=launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=payload,
        delegated_cgroup_root=_delegated_root(),
        policy=policy,
    )


def _test_metrics() -> media_sandbox_module.SandboxMetrics:
    return media_sandbox_module.SandboxMetrics(
        memory_peak_bytes=4096,
        memory_events=(("oom", 0), ("oom_kill", 0)),
        pids_peak=1,
        pids_events=(("max", 0),),
        cpu_stats=(("usage_usec", 1),),
    )


@pytest.mark.parametrize(
    ("scenario", "reason"),
    (
        ("popen", SandboxReason.LAUNCHER_START_FAILED),
        ("gate", SandboxReason.LAUNCHER_START_FAILED),
        ("gate_short", SandboxReason.LAUNCHER_START_FAILED),
        ("generic", SandboxReason.LAUNCHER_START_FAILED),
        ("killpg", SandboxReason.LAUNCHER_START_FAILED),
        ("wait", SandboxReason.CLEANUP_FAILED),
        ("unpopulated", SandboxReason.CLEANUP_FAILED),
        ("metrics", SandboxReason.CLEANUP_FAILED),
        ("capture_none", SandboxReason.CLEANUP_FAILED),
        ("final_fstat", SandboxReason.MEDIA_DRIFT),
        ("final_stat", SandboxReason.MEDIA_DRIFT),
    ),
)
def test_run_reaps_and_classifies_each_parent_side_failure(
    scenario: str,
    reason: SandboxReason,
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = NativeCgroupSandbox(
        launcher=static_launcher,
        launcher_source=_LAUNCHER_SOURCE,
        ffmpeg=static_payload,
        delegated_cgroup_root=tmp_path,
    )
    media = tmp_path / "media"
    media.write_bytes(b"M")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    job = tmp_path / "job"
    job.mkdir()
    original_rmdir = Path.rmdir

    class FakeProcess:
        pid = 2_000_000
        stdout = None
        stderr = None

        def poll(self) -> int | None:
            return None if scenario in {"gate", "gate_short", "killpg", "wait"} else 0

        def wait(self, timeout: float) -> int:
            del timeout
            if scenario == "wait":
                raise subprocess.TimeoutExpired("decoder", 1)
            return 0

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        if scenario == "popen":
            raise OSError("spawn failed")
        return FakeProcess()

    monkeypatch.setattr(media_sandbox_module, "_validate_delegated_topology", lambda root: None)
    monkeypatch.setattr(media_sandbox_module, "_create_job", lambda jobs, policy: job)
    monkeypatch.setattr(media_sandbox_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(media_sandbox_module, "_await_ready", lambda *args: None)
    monkeypatch.setattr(media_sandbox_module, "_attach_process", lambda *args: None)
    monkeypatch.setattr(media_sandbox_module, "_kill_job", lambda target: None)
    monkeypatch.setattr(
        media_sandbox_module.os,
        "killpg",
        (lambda pid, sig: (_ for _ in ()).throw(OSError("gone")))
        if scenario == "killpg"
        else (lambda pid, sig: None),
    )
    monkeypatch.setattr(
        media_sandbox_module.os,
        "write",
        (lambda fd, value: (_ for _ in ()).throw(OSError("closed")))
        if scenario in {"gate", "killpg", "wait"}
        else (
            (lambda fd, value: 0) if scenario == "gate_short" else (lambda fd, value: len(value))
        ),
    )

    def capture(*args: object, **kwargs: object) -> tuple[int, bytes, bytes] | None:
        del args, kwargs
        if scenario == "capture_none":
            return None
        if scenario == "final_stat":
            os.utime(media, ns=(media.stat().st_atime_ns, media.stat().st_mtime_ns + 1))
        return (0, b"", b"")

    monkeypatch.setattr(media_sandbox_module, "_capture_process", capture)
    if scenario == "generic":
        monkeypatch.setattr(
            media_sandbox_module,
            "_open_bound_file",
            lambda expected: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )
    if scenario == "unpopulated":
        monkeypatch.setattr(
            media_sandbox_module,
            "_wait_unpopulated",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                SandboxError(SandboxReason.CLEANUP_FAILED)
            ),
        )
    else:
        monkeypatch.setattr(media_sandbox_module, "_wait_unpopulated", lambda *args, **kwargs: None)
    if scenario == "metrics":
        monkeypatch.setattr(
            media_sandbox_module,
            "_read_metrics",
            lambda target: (_ for _ in ()).throw(SandboxError(SandboxReason.CLEANUP_FAILED)),
        )
    else:
        monkeypatch.setattr(media_sandbox_module, "_read_metrics", lambda target: _test_metrics())
    monkeypatch.setattr(
        media_sandbox_module.Path,
        "rmdir",
        lambda target: None if target == job else original_rmdir(target),
    )
    if scenario == "final_fstat":
        monkeypatch.setattr(
            media_sandbox_module,
            "_raise_resource_failure",
            lambda exit_code, metrics: os.close(descriptor),
        )

    try:
        with pytest.raises(SandboxError) as caught:
            sandbox.run(descriptor, ())
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    assert caught.value.reason is reason


@pytest.mark.parametrize(
    ("behavior", "policy", "reason"),
    (
        (
            "spin",
            dataclasses.replace(SandboxPolicy(), timeout_seconds=0.05, cpu_seconds=10),
            SandboxReason.TIMEOUT,
        ),
        (
            "output",
            dataclasses.replace(SandboxPolicy(), max_output_bytes=1024),
            SandboxReason.OUTPUT_LIMIT,
        ),
        (
            "memory",
            dataclasses.replace(
                SandboxPolicy(),
                memory_bytes=16 * 1024 * 1024,
                address_space_bytes=256 * 1024 * 1024,
            ),
            SandboxReason.RESOURCE_MEMORY,
        ),
        (
            "pids",
            dataclasses.replace(SandboxPolicy(), pids=2),
            SandboxReason.RESOURCE_PIDS,
        ),
        (
            "spin",
            dataclasses.replace(SandboxPolicy(), timeout_seconds=5.0, cpu_seconds=1),
            SandboxReason.RESOURCE_CPU,
        ),
        ("sigsys", SandboxPolicy(), SandboxReason.SECCOMP_VIOLATION),
        ("orphan", SandboxPolicy(), SandboxReason.CLEANUP_FAILED),
    ),
)
def test_native_adapter_classifies_limits_and_cleans_every_job(
    behavior: str,
    policy: SandboxPolicy,
    reason: SandboxReason,
    tmp_path: Path,
    static_launcher: Path,
    static_behavior_payload: Path,
) -> None:
    sandbox = _sandbox_for_payload(
        launcher=static_launcher,
        payload=static_behavior_payload,
        policy=policy,
    )
    media = tmp_path / "media"
    media.write_bytes(b"M")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(SandboxError) as caught:
            sandbox.run(descriptor, (behavior,))
    finally:
        os.close(descriptor)

    assert caught.value.reason is reason
    assert caught.value.metrics is not None
    assert caught.value.metrics.memory_peak_bytes > 0
    assert tuple(child for child in (_delegated_root() / "jobs").iterdir() if child.is_dir()) == ()


def test_each_run_gets_a_fresh_seekable_media_description(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
) -> None:
    sandbox = _sandbox_for_payload(
        launcher=static_launcher,
        payload=static_payload,
        policy=SandboxPolicy(),
    )
    media = tmp_path / "media"
    media.write_bytes(b"MX")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        assert os.lseek(descriptor, 1, os.SEEK_SET) == 1
        first = sandbox.run(descriptor, ())
        second = sandbox.run(descriptor, ())
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 1
    finally:
        os.close(descriptor)

    assert first.stdout == second.stdout == b"isolated\n"


def test_bound_decoder_path_drift_fails_before_exec_and_job_is_removed(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
    static_behavior_payload: Path,
) -> None:
    bound_payload = tmp_path / "bound-payload"
    shutil.copyfile(static_payload, bound_payload)
    bound_payload.chmod(0o755)
    sandbox = _sandbox_for_payload(
        launcher=static_launcher,
        payload=bound_payload,
        policy=SandboxPolicy(),
    )
    shutil.copyfile(static_behavior_payload, bound_payload)
    media = tmp_path / "media"
    media.write_bytes(b"M")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with pytest.raises(SandboxError) as caught:
            sandbox.run(descriptor, ())
    finally:
        os.close(descriptor)

    assert caught.value.reason is SandboxReason.TOOL_DRIFT
    assert tuple(child for child in (_delegated_root() / "jobs").iterdir() if child.is_dir()) == ()


def test_job_policy_is_written_and_each_setup_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(media_sandbox_module.secrets, "token_hex", lambda count: "a" * 24)
    job = media_sandbox_module._create_job(jobs, SandboxPolicy())
    assert (job / "memory.max").read_text().strip() == str(1024 * 1024 * 1024)
    assert (job / "memory.swap.max").read_text().strip() == "0"
    assert (job / "pids.max").read_text().strip() == "32"
    shutil.rmtree(job)

    missing = tmp_path / "missing"
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._create_job(missing, SandboxPolicy())
    assert caught.value.reason is SandboxReason.CGROUP_SETUP_FAILED


@pytest.mark.parametrize("failure", ("write", "readback"))
def test_partial_job_setup_is_removed_or_fails_closed(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setattr(media_sandbox_module.secrets, "token_hex", lambda count: failure)
    original_write = Path.write_text
    original_read = Path.read_text

    def write(target: Path, value: str, **kwargs: object) -> int:
        if failure == "write" and target.name == "memory.max":
            raise OSError("denied")
        return original_write(target, value, **kwargs)

    def read(target: Path, **kwargs: object) -> str:
        value = original_read(target, **kwargs)
        if failure == "readback" and target.name == "memory.max":
            return "different\n"
        return value

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._create_job(jobs, SandboxPolicy())
    assert caught.value.reason is SandboxReason.CGROUP_SETUP_FAILED
    residual = jobs / f"media-{os.getpid()}-{failure}"
    if residual.exists():
        shutil.rmtree(residual)


def test_bound_file_open_and_path_rechecks_detect_replacement(
    tmp_path: Path,
    static_payload: Path,
) -> None:
    bound_path = tmp_path / "bound"
    shutil.copyfile(static_payload, bound_path)
    bound_path.chmod(0o755)
    expected = media_sandbox_module._bind_file(
        bound_path,
        executable=True,
        require_static_elf=True,
    )
    bound_path.unlink()
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._open_bound_file(expected)
    assert caught.value.reason is SandboxReason.TOOL_DRIFT
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._require_bound_path_unchanged(expected)
    assert caught.value.reason is SandboxReason.TOOL_DRIFT

    shutil.copyfile(static_payload, bound_path)
    bound_path.chmod(0o755)
    current = media_sandbox_module._bind_file(
        bound_path,
        executable=True,
        require_static_elf=True,
    )
    bound_path.write_bytes(bound_path.read_bytes() + b"x")
    bound_path.chmod(0o755)
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._require_bound_path_unchanged(current)
    assert caught.value.reason is SandboxReason.TOOL_DRIFT


@pytest.mark.parametrize("case", ("relative", "symlink", "missing", "directory", "nonexec"))
def test_tool_binding_rejects_unsafe_paths_and_modes(case: str, tmp_path: Path) -> None:
    target = tmp_path / "tool"
    if case == "relative":
        target = Path("tool")
    elif case == "symlink":
        backing = tmp_path / "backing"
        backing.write_bytes(b"x")
        target.symlink_to(backing)
    elif case == "directory":
        target.mkdir()
    elif case == "nonexec":
        target.write_bytes(b"not executable")
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._bind_file(
            target,
            executable=True,
            require_static_elf=False,
        )
    assert caught.value.reason is SandboxReason.TOOL_INVALID


@pytest.mark.parametrize("case", ("short_read", "stat_drift"))
def test_tool_binding_rejects_changes_during_hash(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "tool"
    target.write_bytes(b"payload")
    target.chmod(0o755)
    if case == "short_read":
        monkeypatch.setattr(media_sandbox_module.os, "pread", lambda *args: b"")
    else:
        calls = 0

        def changing_stat(info: os.stat_result) -> tuple[int, ...]:
            nonlocal calls
            calls += 1
            return (calls,)

        monkeypatch.setattr(media_sandbox_module, "_stat_key", changing_stat)
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._bind_file(
            target,
            executable=True,
            require_static_elf=False,
        )
    assert caught.value.reason is SandboxReason.TOOL_INVALID


def test_descriptor_identity_rejects_wrong_path_short_read_and_stat_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    descriptor = os.open(first, os.O_RDONLY | os.O_CLOEXEC)
    try:
        measured = media_sandbox_module._identity_from_descriptor(first, descriptor)
        assert measured.sha256
        with pytest.raises(OSError):
            media_sandbox_module._identity_from_descriptor(second, descriptor)

        monkeypatch.setattr(media_sandbox_module.os, "pread", lambda *args: b"")
        with pytest.raises(OSError):
            media_sandbox_module._identity_from_descriptor(first, descriptor)
        monkeypatch.undo()

        calls = 0

        def changing_stat(info: os.stat_result) -> tuple[int, ...]:
            nonlocal calls
            calls += 1
            return (calls,)

        monkeypatch.setattr(media_sandbox_module, "_stat_key", changing_stat)
        with pytest.raises(OSError):
            media_sandbox_module._identity_from_descriptor(first, descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("case", ("closed", "identity", "seek"))
def test_media_reopen_is_fresh_and_fails_closed_on_identity_or_seek_errors(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    descriptor = os.open(first, os.O_RDONLY | os.O_CLOEXEC)
    expected = os.fstat(descriptor) if case != "identity" else second.stat()
    if case == "closed":
        os.close(descriptor)
    if case == "seek":
        monkeypatch.setattr(media_sandbox_module.os, "lseek", lambda *args: 1)
    try:
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._reopen_media(descriptor, expected)
    finally:
        if case != "closed":
            os.close(descriptor)
    assert caught.value.reason is SandboxReason.MEDIA_DRIFT


def _elf_fixture(
    *,
    machine: int = 62,
    program_offset: int = 64,
    entry_bytes: int = 56,
    count: int = 1,
    program_type: int = 1,
    total_bytes: int = 120,
) -> bytes:
    payload = bytearray(total_bytes)
    payload[:6] = b"\x7fELF\x02\x01"
    payload[18:20] = machine.to_bytes(2, "little")
    payload[32:40] = program_offset.to_bytes(8, "little")
    payload[54:56] = entry_bytes.to_bytes(2, "little")
    payload[56:58] = count.to_bytes(2, "little")
    if program_offset + 4 <= total_bytes:
        payload[program_offset : program_offset + 4] = program_type.to_bytes(4, "little")
    return bytes(payload)


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"x" * 64,
        _elf_fixture(machine=3),
        _elf_fixture(entry_bytes=55),
        _elf_fixture(count=0),
        _elf_fixture(total_bytes=100),
        _elf_fixture(program_type=2),
        _elf_fixture(program_type=3),
    ),
)
def test_static_elf_parser_rejects_wrong_architecture_or_dynamic_loading(payload: bytes) -> None:
    assert media_sandbox_module._is_static_elf(payload) is False


def test_static_elf_parser_accepts_only_bounded_x86_64_program_headers() -> None:
    assert media_sandbox_module._is_static_elf(_elf_fixture()) is True


def test_landlock_probe_executes_only_the_already_bound_launcher(
    tmp_path: Path,
    static_launcher: Path,
    static_payload: Path,
) -> None:
    launcher = tmp_path / "launcher"
    shutil.copyfile(static_launcher, launcher)
    launcher.chmod(0o755)
    expected = media_sandbox_module._bind_file(
        launcher,
        executable=True,
        require_static_elf=True,
    )
    shutil.copyfile(static_payload, launcher)
    launcher.chmod(0o755)

    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._probe_landlock_abi(expected)
    assert caught.value.reason is SandboxReason.TOOL_DRIFT


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    (
        (1, b"8\n", b""),
        (0, b"8\n", b"error"),
        (0, b"1" * 17, b""),
        (0, b"eight\n", b""),
        (0, b"\xff\n", b""),
    ),
)
def test_landlock_probe_rejects_noncanonical_results(
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    static_launcher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = media_sandbox_module._bind_file(
        static_launcher,
        executable=True,
        require_static_elf=True,
    )
    capture = subprocess.CompletedProcess((), returncode, stdout, stderr)
    monkeypatch.setattr(media_sandbox_module.subprocess, "run", lambda *args, **kwargs: capture)
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._probe_landlock_abi(expected)
    assert caught.value.reason is SandboxReason.SANDBOX_UNAVAILABLE


def test_landlock_probe_maps_spawn_and_timeout_failures(
    static_launcher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = media_sandbox_module._bind_file(
        static_launcher,
        executable=True,
        require_static_elf=True,
    )
    monkeypatch.setattr(
        media_sandbox_module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("probe", 1)),
    )
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._probe_landlock_abi(expected)
    assert caught.value.reason is SandboxReason.SANDBOX_UNAVAILABLE


def _fake_delegated_topology(root: Path) -> tuple[Path, Path]:
    supervisor = root / "supervisor"
    jobs = root / "jobs"
    supervisor.mkdir(parents=True)
    jobs.mkdir()
    (root / "cgroup.procs").write_text("")
    (jobs / "cgroup.procs").write_text("")
    (root / "cgroup.controllers").write_text("cpu memory pids\n")
    (root / "cgroup.subtree_control").write_text("cpu memory pids\n")
    (jobs / "cgroup.subtree_control").write_text("cpu memory pids\n")
    return supervisor, jobs


def test_delegated_topology_accepts_a_supervisor_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, _ = _fake_delegated_topology(tmp_path)
    current = supervisor / "worker"
    current.mkdir()
    monkeypatch.setattr(media_sandbox_module.os, "access", lambda *args: True)
    media_sandbox_module._validate_delegated_topology(tmp_path, current=current)


def test_delegated_topology_rejects_symlinked_control_nodes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    backing = tmp_path / "backing"
    backing.mkdir()
    (root / "supervisor").symlink_to(backing)
    (root / "jobs").mkdir()
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._validate_delegated_topology(root, current=backing)
    assert caught.value.reason is SandboxReason.CGROUP_UNAVAILABLE


@pytest.mark.parametrize(
    "case",
    (
        "relative",
        "missing_children",
        "outside_supervisor",
        "root_process",
        "jobs_process",
        "controllers",
        "permissions",
    ),
)
def test_delegated_topology_fails_closed_for_each_invalid_shape(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if case == "relative":
        root = Path("relative")
        current = tmp_path
    elif case == "missing_children":
        root = tmp_path
        current = tmp_path
    else:
        root = tmp_path
        supervisor, jobs = _fake_delegated_topology(root)
        current = supervisor
        if case == "outside_supervisor":
            current = root
        elif case == "root_process":
            (root / "cgroup.procs").write_text("1\n")
        elif case == "jobs_process":
            (jobs / "cgroup.procs").write_text("1\n")
        elif case == "controllers":
            (jobs / "cgroup.subtree_control").write_text("cpu memory\n")
    monkeypatch.setattr(
        media_sandbox_module.os,
        "access",
        (lambda *args: False) if case == "permissions" else (lambda *args: True),
    )
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._validate_delegated_topology(root, current=current)
    assert caught.value.reason is SandboxReason.CGROUP_UNAVAILABLE


def test_current_cgroup_parser_rejects_non_unified_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = Path.read_text

    def invalid_record(path: Path, **kwargs: object) -> str:
        if path == Path("/proc/self/cgroup"):
            return "1:name:/legacy\n"
        return original_read(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", invalid_record)
    with pytest.raises(ValueError):
        media_sandbox_module._current_cgroup()


class _PollProcess:
    def __init__(self, result: int | None) -> None:
        self._result = result

    def poll(self) -> int | None:
        return self._result


def test_ready_handshake_classifies_deadline_select_and_protocol_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    try:
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._await_ready(
                _PollProcess(None),  # type: ignore[arg-type]
                read_fd,
                time.monotonic() - 1,
            )
        assert caught.value.reason is SandboxReason.TIMEOUT

        monkeypatch.setattr(
            media_sandbox_module.select,
            "select",
            lambda *args: (_ for _ in ()).throw(OSError("select failed")),
        )
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._await_ready(
                _PollProcess(None),  # type: ignore[arg-type]
                read_fd,
                time.monotonic() + 1,
            )
        assert caught.value.reason is SandboxReason.LAUNCHER_START_FAILED
        monkeypatch.undo()

        os.write(write_fd, b"X")
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._await_ready(
                _PollProcess(None),  # type: ignore[arg-type]
                read_fd,
                time.monotonic() + 1,
            )
        assert caught.value.reason is SandboxReason.LAUNCHER_START_FAILED
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("events", ("populated 0\n", "not-an-integer\n"))
def test_cgroup_attachment_requires_pid_and_populated_confirmation(
    events: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "cgroup.procs").write_text("")
    (tmp_path / "cgroup.events").write_text(events)
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._attach_process(tmp_path, 123)
    assert caught.value.reason is SandboxReason.CGROUP_MIGRATION_FAILED


def _popen_with_pipes(command: tuple[str, ...]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_capture_rejects_missing_pipes_and_expired_deadline() -> None:
    without_pipes = subprocess.Popen(("/usr/bin/true",))
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._capture_process(
            without_pipes,
            status_read=0,
            deadline=time.monotonic() + 1,
            max_output_bytes=1024,
        )
    without_pipes.wait(timeout=1)
    assert caught.value.reason is SandboxReason.LAUNCHER_START_FAILED

    process = _popen_with_pipes(("/usr/bin/sleep", "2"))
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    os.close(status_write)
    try:
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._capture_process(
                process,
                status_read=status_read,
                deadline=time.monotonic() - 1,
                max_output_bytes=1024,
            )
        assert caught.value.reason is SandboxReason.TIMEOUT
    finally:
        os.close(status_read)
        process.kill()
        process.wait(timeout=1)


@pytest.mark.parametrize("case", ("read", "status"))
def test_capture_rejects_stream_read_and_post_ready_status_failures(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _popen_with_pipes(("/usr/bin/sleep", "2"))
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    if case == "status":
        os.write(status_write, b"unexpected")
    os.close(status_write)
    if case == "read":
        monkeypatch.setattr(
            media_sandbox_module.os,
            "read",
            lambda *args: (_ for _ in ()).throw(OSError("read failed")),
        )
    try:
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._capture_process(
                process,
                status_read=status_read,
                deadline=time.monotonic() + 1,
                max_output_bytes=1024,
            )
        assert caught.value.reason is SandboxReason.LAUNCHER_START_FAILED
    finally:
        os.close(status_read)
        process.kill()
        process.wait(timeout=1)


def test_capture_maps_a_wait_timeout_after_all_streams_close(tmp_path: Path) -> None:
    stdout_read, stdout_write = os.pipe2(os.O_CLOEXEC)
    stderr_read, stderr_write = os.pipe2(os.O_CLOEXEC)
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    for descriptor in (stdout_write, stderr_write, status_write):
        os.close(descriptor)

    class WaitTimeoutProcess:
        stdout = os.fdopen(stdout_read, "rb", buffering=0)
        stderr = os.fdopen(stderr_read, "rb", buffering=0)

        def wait(self, timeout: float) -> int:
            raise subprocess.TimeoutExpired("decoder", timeout)

    try:
        with pytest.raises(SandboxError) as caught:
            media_sandbox_module._capture_process(
                WaitTimeoutProcess(),  # type: ignore[arg-type]
                status_read=status_read,
                deadline=time.monotonic() + 1,
                max_output_bytes=1024,
            )
        assert caught.value.reason is SandboxReason.TIMEOUT
    finally:
        os.close(status_read)


def test_kill_and_unpopulated_helpers_fail_closed_without_leaking_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    media_sandbox_module._kill_job(missing)
    (tmp_path / "cgroup.events").write_text("populated 1\n")
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._wait_unpopulated(tmp_path, timeout_seconds=0)
    assert caught.value.reason is SandboxReason.CLEANUP_FAILED


@pytest.mark.parametrize(
    "content",
    ("", "-1\n", "name\n", "name x\n", "name 1\nname 2\n"),
)
def test_cgroup_metric_parsers_reject_empty_negative_malformed_or_duplicate_values(
    content: str,
    tmp_path: Path,
) -> None:
    target = tmp_path / "metric"
    target.write_text(content)
    if content in {"", "-1\n"}:
        with pytest.raises(ValueError):
            media_sandbox_module._read_nonnegative_integer(target)
    with pytest.raises(ValueError):
        media_sandbox_module._read_key_values(target)


def test_metric_collection_maps_any_malformed_cgroup_file(tmp_path: Path) -> None:
    for name in (
        "memory.peak",
        "pids.peak",
        "memory.events.local",
        "pids.events.local",
        "cpu.stat",
    ):
        (tmp_path / name).write_text("invalid\n")
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._read_metrics(tmp_path)
    assert caught.value.reason is SandboxReason.CLEANUP_FAILED


@pytest.mark.parametrize(
    ("exit_code", "metrics", "reason"),
    (
        (
            0,
            dataclasses.replace(_test_metrics(), memory_events=(("oom", 1),)),
            SandboxReason.RESOURCE_MEMORY,
        ),
        (
            0,
            dataclasses.replace(_test_metrics(), pids_events=(("max", 1),)),
            SandboxReason.RESOURCE_PIDS,
        ),
        (-signal.SIGXCPU, _test_metrics(), SandboxReason.RESOURCE_CPU),
        (-signal.SIGSYS, _test_metrics(), SandboxReason.SECCOMP_VIOLATION),
        (77, _test_metrics(), SandboxReason.LANDLOCK_SETUP_FAILED),
        (78, _test_metrics(), SandboxReason.SECCOMP_SETUP_FAILED),
        (64, _test_metrics(), SandboxReason.LAUNCHER_START_FAILED),
    ),
)
def test_exit_and_cgroup_evidence_have_distinct_resource_reasons(
    exit_code: int,
    metrics: media_sandbox_module.SandboxMetrics,
    reason: SandboxReason,
) -> None:
    with pytest.raises(SandboxError) as caught:
        media_sandbox_module._raise_resource_failure(exit_code, metrics)
    assert caught.value.reason is reason


def test_descriptor_cleanup_tolerates_already_closed_and_unknown_fds(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"x")
    descriptor = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    descriptors = {descriptor}
    os.close(descriptor)
    media_sandbox_module._close_descriptor(descriptor, descriptors)
    media_sandbox_module._close_descriptor(descriptor, descriptors)
    assert descriptors == set()


@pytest.mark.parametrize(
    ("exit_code", "expected_reason"),
    (
        (0, None),
        (-signal.SIGXCPU, SandboxReason.RESOURCE_CPU),
    ),
)
def test_run_returns_a_bounded_capture_or_preserves_resource_metrics(
    exit_code: int,
    expected_reason: SandboxReason | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = object.__new__(NativeCgroupSandbox)
    sandbox._policy = SandboxPolicy()  # type: ignore[attr-defined]
    sandbox._launcher = object()  # type: ignore[attr-defined]
    sandbox._ffmpeg = object()  # type: ignore[attr-defined]
    sandbox._launcher_source = object()  # type: ignore[attr-defined]
    sandbox._implementation = object()  # type: ignore[attr-defined]
    sandbox._delegated_cgroup_root = tmp_path  # type: ignore[attr-defined]
    job = tmp_path / "job"
    job.mkdir()

    class FinishedProcess:
        pid = 1234

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float) -> int:
            del timeout
            return exit_code

    monkeypatch.setattr(media_sandbox_module, "_validate_delegated_topology", lambda root: None)
    monkeypatch.setattr(media_sandbox_module, "_create_job", lambda jobs, policy: job)
    monkeypatch.setattr(
        media_sandbox_module,
        "_open_bound_file",
        lambda expected: os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC),
    )
    monkeypatch.setattr(
        media_sandbox_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FinishedProcess(),
    )
    monkeypatch.setattr(media_sandbox_module, "_await_ready", lambda *args: None)
    monkeypatch.setattr(media_sandbox_module, "_attach_process", lambda *args: None)
    monkeypatch.setattr(media_sandbox_module.os, "write", lambda fd, value: len(value))
    monkeypatch.setattr(
        media_sandbox_module,
        "_capture_process",
        lambda *args, **kwargs: (exit_code, b"stdout", b"stderr"),
    )
    monkeypatch.setattr(media_sandbox_module, "_wait_unpopulated", lambda *args, **kwargs: None)
    monkeypatch.setattr(media_sandbox_module, "_read_metrics", lambda target: _test_metrics())
    monkeypatch.setattr(media_sandbox_module, "_require_bound_path_unchanged", lambda bound: None)

    media = tmp_path / "media"
    media.write_bytes(b"media")
    descriptor = os.open(media, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if expected_reason is None:
            capture = sandbox.run(descriptor, ())
            assert capture == SandboxCapture(
                exit_code=0,
                stdout=b"stdout",
                stderr=b"stderr",
                metrics=_test_metrics(),
            )
        else:
            with pytest.raises(SandboxError) as caught:
                sandbox.run(descriptor, ())
            assert caught.value.reason is expected_reason
            assert caught.value.metrics == _test_metrics()
    finally:
        os.close(descriptor)

    assert not job.exists()


def test_ready_handshake_accepts_the_exact_live_protocol_byte() -> None:
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    try:
        os.write(write_fd, b"R")
        media_sandbox_module._await_ready(
            _PollProcess(None),  # type: ignore[arg-type]
            read_fd,
            time.monotonic() + 1,
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_cgroup_attachment_accepts_confirmed_membership(tmp_path: Path) -> None:
    (tmp_path / "cgroup.procs").write_text("")
    (tmp_path / "cgroup.events").write_text("populated 1\n")

    media_sandbox_module._attach_process(tmp_path, 123)

    assert (tmp_path / "cgroup.procs").read_text() == "123\n"


@pytest.mark.parametrize(
    ("max_output_bytes", "expected"),
    (
        (3, (7, b"abc", b"")),
        (2, SandboxReason.OUTPUT_LIMIT),
    ),
)
def test_capture_enforces_the_exact_combined_output_boundary(
    max_output_bytes: int,
    expected: tuple[int, bytes, bytes] | SandboxReason,
) -> None:
    stdout_read, stdout_write = os.pipe2(os.O_CLOEXEC)
    stderr_read, stderr_write = os.pipe2(os.O_CLOEXEC)
    status_read, status_write = os.pipe2(os.O_CLOEXEC)
    os.write(stdout_write, b"abc")
    for descriptor in (stdout_write, stderr_write, status_write):
        os.close(descriptor)

    class CompletedProcess:
        stdout = os.fdopen(stdout_read, "rb", buffering=0)
        stderr = os.fdopen(stderr_read, "rb", buffering=0)

        def wait(self, timeout: float) -> int:
            del timeout
            return 7

    try:
        if isinstance(expected, SandboxReason):
            with pytest.raises(SandboxError) as caught:
                media_sandbox_module._capture_process(
                    CompletedProcess(),  # type: ignore[arg-type]
                    status_read=status_read,
                    deadline=time.monotonic() + 1,
                    max_output_bytes=max_output_bytes,
                )
            assert caught.value.reason is expected
        else:
            assert (
                media_sandbox_module._capture_process(
                    CompletedProcess(),  # type: ignore[arg-type]
                    status_read=status_read,
                    deadline=time.monotonic() + 1,
                    max_output_bytes=max_output_bytes,
                )
                == expected
            )
    finally:
        os.close(status_read)


def test_unpopulated_wait_observes_a_transition_before_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = iter(
        (
            (("populated", 1),),
            (("populated", 0),),
        )
    )
    monkeypatch.setattr(media_sandbox_module, "_read_key_values", lambda path: next(observations))
    monkeypatch.setattr(media_sandbox_module.time, "sleep", lambda seconds: None)

    media_sandbox_module._wait_unpopulated(Path("unused"), timeout_seconds=1)


def test_metric_collection_returns_all_valid_cgroup_values(tmp_path: Path) -> None:
    (tmp_path / "memory.peak").write_text("4096\n")
    (tmp_path / "pids.peak").write_text("2\n")
    (tmp_path / "memory.events.local").write_text("oom 0\noom_kill 0\n")
    (tmp_path / "pids.events.local").write_text("max 0\n")
    (tmp_path / "cpu.stat").write_text("usage_usec 17\n")

    assert media_sandbox_module._read_metrics(tmp_path) == media_sandbox_module.SandboxMetrics(
        memory_peak_bytes=4096,
        memory_events=(("oom", 0), ("oom_kill", 0)),
        pids_peak=2,
        pids_events=(("max", 0),),
        cpu_stats=(("usage_usec", 17),),
    )


def test_current_cgroup_parser_accepts_a_unified_absolute_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read = Path.read_text

    def unified_record(path: Path, **kwargs: object) -> str:
        if path == Path("/proc/self/cgroup"):
            return "0::/supervisor/worker\n"
        return original_read(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", unified_record)

    assert media_sandbox_module._current_cgroup() == Path("/sys/fs/cgroup/supervisor/worker")


def test_delegated_topology_uses_the_current_cgroup_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supervisor, _ = _fake_delegated_topology(tmp_path)
    current = supervisor / "worker"
    current.mkdir()
    monkeypatch.setattr(media_sandbox_module, "_current_cgroup", lambda: current)
    monkeypatch.setattr(media_sandbox_module.os, "access", lambda *args: True)

    media_sandbox_module._validate_delegated_topology(tmp_path)
