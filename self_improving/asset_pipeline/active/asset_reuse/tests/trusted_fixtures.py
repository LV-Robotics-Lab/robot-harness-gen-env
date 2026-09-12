import json
import sys
import types
from pathlib import Path


def qualified_runtime_capability(tmp_path, issuer, ledger, ledger_writes):
    """Create a file-backed synthetic envs/SAPIEN tree for trust-unit tests."""

    policy = ledger.qualified_verification_issuer(issuer)["runtime"]
    runtime_root = Path(tmp_path) / "synthetic_runtime"
    sapien_file = runtime_root / "sapien" / "__init__.py"
    native_file = runtime_root / "sapien" / "_native.so"
    envs_file = runtime_root / "envs" / "__init__.py"
    utils_file = runtime_root / "envs" / "utils.py"
    for path, payload in (
        (sapien_file, b"# synthetic SAPIEN package\n"),
        (native_file, b"synthetic-native-binary"),
        (envs_file, b"# synthetic RoboTwin envs package\n"),
        (utils_file, b"# synthetic RoboTwin loader\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    sapien = types.ModuleType("sapien")
    sapien.__file__ = str(sapien_file)
    native = types.ModuleType("sapien._native")
    native.__file__ = str(native_file)
    envs = types.ModuleType("envs")
    envs.__file__ = str(envs_file)
    runtime_utils = types.ModuleType("envs.utils")
    runtime_utils.__file__ = str(utils_file)
    synthetic_modules = {
        "sapien": sapien,
        "sapien._native": native,
        "envs": envs,
        "envs.utils": runtime_utils,
    }
    missing = object()
    previous_modules = {name: sys.modules.get(name, missing) for name in synthetic_modules}
    loader_module = runtime_utils if policy["loader_module_root"] == "envs" else sapien
    try:
        sys.modules.update(synthetic_modules)
        return ledger_writes.capture_runtime_capability(
            loader_modules=[loader_module],
            sapien_module=sapien,
            entrypoint=policy["entrypoint"],
            config=json.loads(json.dumps(policy["config"])),
        )
    finally:
        for name, previous in previous_modules.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
