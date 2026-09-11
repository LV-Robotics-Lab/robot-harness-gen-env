"""Independent restored-pose capture worker with inherited scene confinement."""

import json
import sys
from pathlib import Path


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
    from self_improving.harness._genesis_scene_child import restrict_scene_process

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
        result["schema_version"] = "harness.genesis_fresh_capture_raw.v1"
        result["request"] = job["request_ref"]
        result["capture_event"] = json.loads(
            (Path(job["output_dir"]) / "capture.json").read_bytes()
        )
        return result

    from self_improving.harness.genesis_scene_fresh_capture_backend import capture_restored_scene
    from self_improving.harness.schemas.genesis_scene_replay import GenesisSceneSpecV1

    spec = GenesisSceneSpecV1.model_validate(job["scene_spec"])
    capture_restored_scene(
        spec.model_dump(mode="json"),
        job["final_state"],
        Path(job["urdf_path"]),
        Path(job["output_dir"]),
        runtime_observer=observe,
        cpu_facts=facts,
    )
    os.close(maps_fd)


if __name__ == "__main__":
    main()
