"""One bounded dual-profile execution; workflow transitions belong to Harness."""

import json
import mimetypes
import time
from pathlib import Path
from typing import Literal

from .contracts import ArtifactRef, Model
from .genesis_runtime import run_scene


class ReplayFile(Model):
    path: str
    artifact: ArtifactRef


class ReplayProfile(Model):
    profile: Literal["baseline", "half_dt"]
    root: str
    status: Literal["passed", "failed", "cancelled"]
    files: tuple[ReplayFile, ...]


class ReplayResult(Model):
    status: Literal["succeeded", "failed", "cancelled"]
    profiles: tuple[ReplayProfile, ...]
    receipt: ArtifactRef
    error_code: str | None
    physical_evaluated: Literal[False] = False


class GenesisReplayExecutor:
    def __init__(self, store, *, runtime_roots, denied_roots=()):
        self.store, self.runtime_roots, self.denied_roots = store, runtime_roots, denied_roots

    def replay(self, scene, *, package_root, output_root, timeout=600):
        if type(timeout) is not int or not 1 <= timeout <= 1770:
            raise ValueError("invalid replay deadline")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("replay root must be absolute and non-symbolic")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        profiles, error, status = [], None, "succeeded"
        for profile in ("baseline", "half_dt"):
            remaining = int(timeout - (time.monotonic() - started))
            if remaining < 1:
                status, error = "cancelled", "timed_out"
                break
            output = root / profile
            try:
                result = run_scene(
                    scene,
                    package_root=package_root,
                    runtime_roots=self.runtime_roots,
                    output_dir=output,
                    profile=profile,
                    denied_roots=self.denied_roots,
                    timeout_seconds=remaining,
                )
            except (OSError, ValueError) as failure:
                result = {
                    "status": "failed",
                    "simulator_executed": False,
                    "error_code": "runtime_execution_error",
                    "detail": str(failure)[:2000],
                }
                output.mkdir(parents=True, exist_ok=True)
                (output / "adapter-error.json").write_text(json.dumps(result, sort_keys=True))
            names = {
                "job.json",
                "invocation.json",
                "process.json",
                "result.json",
                "child-result.json",
                "genesis_child.py",
                "loaded.json",
                "trace.ndjson",
                "media.json",
                "simulation.mkv",
                "preview.mp4",
                "stdout.log",
                "stderr.log",
                "adapter-error.json",
            }
            files = []
            if output.is_dir():
                paths = [output / name for name in sorted(names)]
                paths += sorted((output / "frames").glob("*.png"))
                for path in paths:
                    if path.is_symlink():
                        raise ValueError("symlink in replay evidence")
                    if path.is_file():
                        ref = self.store.write_artifact(
                            path.read_bytes(),
                            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        )
                        files.append(
                            ReplayFile(path=path.relative_to(output).as_posix(), artifact=ref)
                        )
            passed = result.get("status") == "passed" and result.get("simulator_executed") is True
            cancelled = result.get("status") == "cancelled"
            profiles.append(
                ReplayProfile(
                    profile=profile,
                    root=str(output),
                    status="passed" if passed else "cancelled" if cancelled else "failed",
                    files=tuple(files),
                )
            )
            if not passed:
                status, error = (
                    ("cancelled", "timed_out")
                    if cancelled
                    else ("failed", result.get("error_code", "runtime_execution_failed"))
                )
                break
        receipt_data = json.dumps(
            {
                "status": status,
                "error_code": error,
                "profiles": [p.model_dump(mode="json") for p in profiles],
                "scene": scene.model_dump(mode="json"),
                "wall_seconds": time.monotonic() - started,
                "physical_evaluated": False,
            },
            sort_keys=True,
        ).encode()
        (root / "receipt.json").write_bytes(receipt_data)
        receipt = self.store.write_artifact(receipt_data, "application/json")
        return ReplayResult(
            status=status, profiles=tuple(profiles), receipt=receipt, error_code=error
        )
