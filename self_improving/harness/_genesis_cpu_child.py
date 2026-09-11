"""Private fixed CPU worker, launched only after its complete deployment is verified."""

import sys
from contextlib import contextmanager


@contextmanager
def bind_cpu_device_metadata(genesis, *, cpu_device, facts):
    """Supply observed CPU metadata during initialization and always restore Genesis."""
    original = genesis.get_device

    def get_device(backend, device_idx=None):
        if backend != genesis.cpu or device_idx not in (None, 0):
            raise ValueError("fixed metadata is only valid for the CPU backend")
        return cpu_device, facts["cpu_name"], facts["memory_bytes"] / 1024**3, backend

    genesis.get_device = get_device
    try:
        yield
    finally:
        genesis.get_device = original


def verify_cpu_runtime_constraints(roots):
    """Require actual network and runtime-write denials before importing Genesis."""
    import os
    import socket

    try:
        channel = socket.socket()
    except PermissionError:
        pass
    else:
        channel.close()
        raise RuntimeError("network restriction is absent")
    try:
        descriptor = os.open(
            roots["backend"] / "self_improving/harness/_genesis_cpu_child.py", os.O_WRONLY
        )
    except PermissionError:
        pass
    else:
        os.close(descriptor)
        raise RuntimeError("runtime write restriction is absent")


def main():
    import json
    import os
    from pathlib import Path

    invocation_path = Path(sys.argv[1])
    invocation = json.loads(invocation_path.read_bytes())
    roots = {key: Path(value) for key, value in invocation["roots"].items()}
    run = invocation_path.parent
    sys.path[:] = [
        str(roots["stdlib"]),
        str(roots["stdlib"] / "lib-dynload"),
        str(roots["distributions"] / "lib/python3.12/site-packages"),
        str(roots["backend"]),
        str(roots["genesis"]),
    ]
    maps_fd = os.open("/proc/self/maps", os.O_RDONLY | os.O_CLOEXEC)
    with open("/proc/cpuinfo") as source:
        cpu = source.read()
    with open("/proc/meminfo") as source:
        memory = source.read()
    facts = {
        "cpu_name": next(
            line.partition(":")[2].strip()
            for line in cpu.splitlines()
            if line.startswith("model name")
        ),
        "memory_bytes": int(
            next(line.split()[1] for line in memory.splitlines() if line.startswith("MemTotal:"))
        )
        * 1024,
        "kernel_release": os.uname().release,
        "kernel_machine": os.uname().machine,
        "process_maps_preopened": True,
    }
    abi = _restrict(roots, run)
    verify_cpu_runtime_constraints(roots)
    import hashlib
    import platform
    import sysconfig

    from self_improving.harness.genesis_runtime_launcher import genesis_runtime_platform_identity

    prefix = {
        "implementation": sys.implementation.name,
        "version": platform.python_version(),
        "abi": sysconfig.get_config_var("SOABI"),
        "platform": genesis_runtime_platform_identity(),
    }
    if any(invocation["expected_process_identity"][key] != value for key, value in prefix.items()):
        raise RuntimeError("runtime interpreter or kernel identity differs before Genesis import")
    if dict(os.environ) != invocation["startup_environment"]:
        raise RuntimeError("runtime startup environment differs before Genesis import")

    import torch

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_probe import GenesisAssetProbeProducer
    from self_improving.harness.genesis_cpu_backend import (
        GENESIS_CPU_PROCESS_ENV,
        GenesisCpuAssetProbeBackend,
        GenesisCpuBackendUnavailableError,
    )
    from self_improving.harness.schemas.common import ArtifactRef

    class ArchiveBackend(GenesisCpuAssetProbeBackend):
        def _prepare_runtime(self):
            if self._source_root != roots["genesis"]:
                raise GenesisCpuBackendUnavailableError("archive deployment differs")
            if any(os.environ.get(key) != value for key, value in GENESIS_CPU_PROCESS_ENV.items()):
                raise GenesisCpuBackendUnavailableError("fixed CPU environment differs")

        def load(self, request):
            genesis = self._import_genesis()
            original = genesis.init

            def initialize(*args, **kwargs):
                with bind_cpu_device_metadata(genesis, cpu_device=torch.device("cpu"), facts=facts):
                    return original(*args, **kwargs)

            genesis.init = initialize
            try:
                return super().load(request)
            finally:
                genesis.init = original

    backend = ArchiveBackend(roots["genesis"], invocation["implementation_sha256"])
    store = LocalArtifactStore(run / "cas")
    produced = GenesisAssetProbeProducer(store, backend).produce(
        representation_ref=ArtifactRef.model_validate(invocation["representation"]),
        runtime_lock_ref=ArtifactRef.model_validate(invocation["runtime_lock"]),
    )
    refs = {
        name: getattr(produced, field).model_dump(mode="json")
        for name, field in (
            ("probe_input", "probe_input_ref"),
            ("load_observation", "load_observation_ref"),
            ("load_evidence", "load_evidence_ref"),
            ("trace_rows", "trace_rows_ref"),
            ("contact_trace", "contact_trace_ref"),
            ("probe_report", "probe_report_ref"),
        )
    }
    (run / "partial-probe.json").write_text(json.dumps(refs, sort_keys=True, separators=(",", ":")))
    with os.fdopen(maps_fd, "rb") as source:
        maps_bytes = source.read(8 * 1024 * 1024 + 1)
    if len(maps_bytes) > 8 * 1024 * 1024:
        raise RuntimeError("process mappings exceed the fixed observation limit")
    mapped = sorted(
        {
            line.split()[-1]
            for line in maps_bytes.decode().splitlines()
            if len(line.split()) >= 6 and line.split()[-1].startswith("/")
        }
    )
    for name, expected in (
        ("torch.classes", ("torch._classes", "_Classes")),
        ("torch.ops", ("torch._ops", "_Ops")),
    ):
        module = sys.modules[name]
        if (type(module).__module__, type(module).__name__) != expected:
            raise RuntimeError("generated namespace implementation differs")
    module_inventory = [
        {
            "name": name,
            "file": str(getattr(module, "__file__", "")),
            "origin": str(getattr(getattr(module, "__spec__", None), "origin", None)),
        }
        for name, module in tuple(sys.modules.items())
    ]
    (run / "module-inventory.json").write_text(json.dumps(module_inventory))
    modules = sorted(
        {
            str(Path(module.__file__).absolute())
            for module in tuple(sys.modules.values())
            if getattr(module, "__file__", None)
            and getattr(getattr(module, "__spec__", None), "origin", None) != "frozen"
            and getattr(module, "__name__", None) not in ("torch.classes", "torch.ops")
        }
    )
    environment = {key: value.replace(str(run), "${RUN}") for key, value in os.environ.items()}
    for role, root in sorted(roots.items(), key=lambda item: len(str(item[1])), reverse=True):
        environment = {
            key: value.replace(str(root), "${" + role.upper() + "}")
            for key, value in environment.items()
        }
    environment = {
        key: value.replace(invocation["user_home"], "${USER_HOME}")
        for key, value in environment.items()
    }
    output = {
        "process_identity": {
            "implementation": sys.implementation.name,
            "version": platform.python_version(),
            "abi": sysconfig.get_config_var("SOABI"),
            "platform": genesis_runtime_platform_identity(),
            "environment": sorted(environment.items()),
        },
        "torch_threads": [torch.get_num_threads(), torch.get_num_interop_threads()],
        "landlock_abi": abi,
        "kernel_facts": facts,
        "generated_namespaces": ["torch.classes", "torch.ops"],
        "frozen_modules": sorted(
            row["name"] for row in module_inventory if row["origin"] == "frozen"
        ),
        "builtin_modules": sorted(
            row["name"] for row in module_inventory if row["origin"] == "built-in"
        ),
        "other_non_file_modules": sorted(
            row["name"]
            for row in module_inventory
            if not row["file"] and row["origin"] not in ("frozen", "built-in")
        ),
        "modules": [
            {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for path in modules
        ],
        "native_files": [
            {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            for path in mapped
        ],
        "probe_refs": refs,
    }
    (run / "result.json").write_text(json.dumps(output, sort_keys=True, separators=(",", ":")))


def _restrict(roots, run):
    import ctypes
    import os

    libc = ctypes.CDLL(None, use_errno=True)

    class Rules(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scope", ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("fd", ctypes.c_int)]

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    abi = libc.syscall(444, 0, 0, 1)
    if abi < 6:
        raise RuntimeError("required Landlock ABI is unavailable")
    rules = Rules((1 << 16) - 1, 3, 3)
    fd = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if fd < 0:
        raise RuntimeError("runtime filesystem policy unavailable")
    permissions = [(path, 13) for path in roots.values()] + [(run, (1 << 16) - 1)]
    permissions += [("/dev/urandom", 4), ("/dev/null", 6)]
    permissions += [
        ("/usr/lib/x86_64-linux-gnu/" + name, 4)
        for name in ("libGLX.so.0.0.0", "libOpenGL.so.0.0.0")
    ]
    try:
        for path, access in permissions:
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(access, path_fd)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) != 0:
                    raise RuntimeError("runtime filesystem rule failed")
            finally:
                os.close(path_fd)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, fd, 0) != 0:
            raise RuntimeError("runtime filesystem restriction failed")
    finally:
        os.close(fd)
    filters = [
        Filter(0x20, 0, 0, 4),
        Filter(0x15, 1, 0, 0xC000003E),
        Filter(0x06, 0, 0, 0x80000000),
        Filter(0x20, 0, 0, 0),
        Filter(0x45, 0, 1, 0x40000000),
        Filter(0x06, 0, 0, 0x80000000),
    ]
    for number in (41, 42, 43, 49, 50, 53, 288, 425):
        filters.extend((Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, 0x50001)))
    filters.append(Filter(0x06, 0, 0, 0x7FFF0000))
    array = (Filter * len(filters))(*filters)
    program = Program(len(filters), array)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("runtime network restriction failed")
    return abi


if __name__ == "__main__":
    main()
