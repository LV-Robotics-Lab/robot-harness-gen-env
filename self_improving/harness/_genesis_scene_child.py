"""Fixed renderer-enabled scene worker; outputs diagnostics, not execution authority."""

import json
import sys
from pathlib import Path


def restrict_scene_process(roots, run):
    """Apply inherited read-only runtime and no-network rules, without host GL exceptions.

    This is the renderer v1 policy. Its syscall rules derive from the existing
    CPU asset child, but its filesystem allowlist includes only declared roots.
    """
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
        raise RuntimeError("required scene Landlock ABI is unavailable")
    rules = Rules((1 << 16) - 1, 3, 3)
    descriptor = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if descriptor < 0:
        raise RuntimeError("scene filesystem policy unavailable")
    permissions = [(path, 13) for path in roots.values()]
    permissions += [(run, (1 << 16) - 1), ("/dev/urandom", 4), ("/dev/null", 6)]
    try:
        for path, access in permissions:
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(access, path_fd)
                if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0):
                    raise RuntimeError("scene filesystem rule failed")
            finally:
                os.close(path_fd)
        if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, descriptor, 0):
            raise RuntimeError("scene filesystem restriction failed")
    finally:
        os.close(descriptor)
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
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise RuntimeError("scene network restriction failed")
    return abi


def main():
    import hashlib
    import os
    import platform
    import socket
    import sysconfig

    job = json.loads(Path(sys.argv[1]).read_text())
    roots = {key: Path(value) for key, value in job["roots"].items()}
    sys.path[:] = [
        str(roots["stdlib"]),
        str(roots["stdlib"] / "lib-dynload"),
        str(roots["distributions"] / "lib/python3.12/site-packages"),
        str(roots["backend"]),
        str(roots["genesis"]),
    ]
    maps_fd = os.open("/proc/self/maps", os.O_RDONLY | os.O_CLOEXEC)
    cpu = Path("/proc/cpuinfo").read_text()
    memory = Path("/proc/meminfo").read_text()
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
    }
    from self_improving.harness.genesis_runtime_launcher import genesis_runtime_platform_identity

    identity = {
        "implementation": sys.implementation.name,
        "version": platform.python_version(),
        "abi": sysconfig.get_config_var("SOABI"),
        "platform": genesis_runtime_platform_identity(),
    }
    if any(job["expected_process_identity"][key] != value for key, value in identity.items()):
        raise RuntimeError("scene interpreter or kernel identity differs")
    if dict(os.environ) != job["startup_environment"]:
        raise RuntimeError("scene startup environment differs")
    abi = restrict_scene_process(roots, Path(job["output_dir"]).parent)
    try:
        socket.socket()
    except PermissionError:
        pass
    else:
        raise RuntimeError("scene network restriction absent")
    try:
        fd = os.open(__file__, os.O_WRONLY)
    except PermissionError:
        pass
    else:
        os.close(fd)
        raise RuntimeError("scene runtime write restriction absent")

    def observe():
        os.lseek(maps_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(maps_fd), "rb") as source:
            mappings = source.read(8 * 1024 * 1024 + 1)
        if len(mappings) > 8 * 1024 * 1024:
            raise RuntimeError("scene process mappings exceed limit")
        native = sorted(
            {
                line.split()[-1]
                for line in mappings.decode().splitlines()
                if len(line.split()) >= 6 and line.split()[-1].startswith("/")
            }
        )
        modules = sorted(
            {
                str(Path(module.__file__).absolute())
                for name, module in tuple(sys.modules.items())
                if name not in ("torch.classes", "torch.ops")
                and getattr(module, "__file__", None)
                and getattr(getattr(module, "__spec__", None), "origin", None) != "frozen"
            }
        )

        def members(paths):
            return [
                {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                for path in paths
            ]

        import torch

        result = {
            "schema_version": "harness.genesis_scene_process_observation.v1",
            "process_identity": identity,
            "deployment_roots": job["roots"],
            "startup_environment": job["startup_environment"],
            "environment": dict(os.environ),
            "landlock_abi": abi,
            "network_denied": True,
            "runtime_write_denied": True,
            "torch_threads": [torch.get_num_threads(), torch.get_num_interop_threads()],
            "modules": members(modules),
            "native_files": members(native),
        }
        if job.get("runtime_version", 1) == 2:
            media = json.loads((Path(job["output_dir"]) / "media.json").read_bytes())
            result["schema_version"] = "harness.genesis_scene_raw_observation.v2"
            result["capture_events"] = [
                {
                    "step": frame["step"],
                    "time_s": frame["time_s"],
                    "captured_at": frame["captured_at"],
                    "pixels_sha256": frame["rgb_sha256"],
                    "width": frame["width"],
                    "height": frame["height"],
                    "image_path": frame["path"],
                }
                for frame in media["frames"]
            ]
        return result

    from self_improving.harness.genesis_scene_cpu_backend import (
        run_genesis_scene_cpu,
        run_genesis_scene_cpu_v2,
    )
    from self_improving.harness.schemas.genesis_scene_replay import GenesisSceneSpecV1

    spec = GenesisSceneSpecV1.model_validate(job["scene_spec"])
    runner = (
        run_genesis_scene_cpu_v2 if job.get("runtime_version", 1) == 2 else run_genesis_scene_cpu
    )
    runner(
        spec.model_dump(mode="json"),
        Path(job["urdf_path"]),
        Path(job["output_dir"]),
        profile=job["profile"],
        runtime_observer=observe,
        cpu_facts=facts,
    )
    os.close(maps_fd)


if __name__ == "__main__":
    main()
