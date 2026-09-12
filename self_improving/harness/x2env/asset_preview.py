"""Version-bound isolated candidate preview; never a final scene or physical qualification."""

import hashlib
import json
import mimetypes
import os
import subprocess
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image

from .assets import AssetRegistry, AssetStore, AssetVersion
from .contracts import ArtifactRef, Model, Sha256
from .genesis_runtime import RuntimeEntity, RuntimeMember, RuntimeScene, run_scene


class AssetPreviewProof(Model):
    status: Literal["passed", "failed"]
    version_sha256: Sha256
    image: ArtifactRef | None
    receipt: ArtifactRef
    error_code: str | None = None


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


class AssetPreviewRenderer:
    def __init__(
        self, store: AssetStore, runtime_roots, root: Path, seed: int, *, runner=run_scene
    ):
        self.store, self.runtime_roots, self.root, self.seed = (
            store,
            runtime_roots,
            Path(root),
            seed,
        )
        self.runner = runner

    def render(self, version: AssetVersion) -> AssetPreviewProof:
        start = time.monotonic()
        attempt = self.root / str(uuid.uuid4())
        attempt.mkdir(parents=True, exist_ok=False)
        package, output = attempt / "package", attempt / "runtime"
        outputs, image, error, status = {}, None, None, "failed"
        receipt = {
            "scope": "asset_preview_scope",
            "version_sha256": version.version_sha256,
            "physical_profile": "not_run",
            "qualified": False,
            "seed": self.seed,
            "runtime_roots": {k: str(v) for k, v in self.runtime_roots.items()},
            "package": {},
            "outputs": outputs,
        }
        workspace = Path(__file__).resolve().parents[3]
        identity = {}
        for name, args in (
            ("head", ["rev-parse", "HEAD"]),
            ("status", ["status", "--porcelain"]),
            ("diff", ["diff", "HEAD", "--binary"]),
        ):
            result = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True)
            identity[name + "_sha256"] = hashlib.sha256(result.stdout).hexdigest()
            if name == "head":
                identity["head"] = result.stdout.decode().strip()
        identity["executed_source_sha256"] = {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("asset_preview.py", "genesis_runtime.py", "genesis_child.py")
        }
        receipt["identity"] = identity
        try:
            verified = AssetRegistry(self.store).inspect(version.version_sha256)
            if verified != version:
                raise ValueError("asset_version_mismatch")
            if {m.path for m in version.files} != {
                "asset.urdf",
                "visual.glb",
                "collision.obj",
                "physics.json",
            } or version.entrypoint != "asset.urdf":
                raise ValueError("unsupported_preview_asset_closure")
            package.mkdir()
            for member in version.files:
                (package / member.path).write_bytes(self.store.read_artifact(member.artifact))
                receipt["package"][member.path] = member.artifact.model_dump(mode="json")
            scope = self.store.write_artifact(
                _json({"scope": "asset_preview_scope", "version_sha256": version.version_sha256}),
                "application/json",
            )
            scene = RuntimeScene(
                seed=self.seed,
                scene_ir_sha256=scope.sha256,
                entities=(
                    RuntimeEntity(
                        id="candidate",
                        kind="rigid",
                        category=version.category,
                        position_m=(0.0, 0.0, 0.001),
                        orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                        urdf_path="asset.urdf",
                        physics_path="physics.json",
                        version_sha256=version.version_sha256,
                    ),
                ),
                members=tuple(
                    RuntimeMember(
                        path=m.path, sha256=m.artifact.sha256, size_bytes=m.artifact.size_bytes
                    )
                    for m in version.files
                ),
            )
            scene_ref = self.store.write_artifact(
                scene.model_dump_json().encode(), "application/json"
            )
            receipt["runtime_scene"] = scene_ref.model_dump(mode="json")
            # The child enforces an allowlist of this copied package, outputs and declared runtime.
            # Deny the workspace: build-time code/assets are not runtime inputs.
            result = self.runner(
                scene,
                package_root=package,
                runtime_roots=self.runtime_roots,
                output_dir=output,
                profile="load_step_smoke",
                denied_roots=(workspace,),
                timeout_seconds=600,
            )
            receipt["runtime_result"] = result
            if result.get("status") != "passed" or not result.get("simulator_executed"):
                raise ValueError(result.get("error_code", "preview_runtime_failed"))
            frame = result["media"]["frames"][-1]
            path = output / frame["path"]
            if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
                raise ValueError("unsafe_preview_frame")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != frame["png_sha256"]:
                raise ValueError("preview_frame_hash_mismatch")
            with Image.open(BytesIO(data)) as opened:
                if opened.format != "PNG":
                    raise ValueError("preview_not_png")
                opened.verify()
            image = self.store.write_artifact(data, "image/png")
            status = "passed"
        except (ValueError, OSError, KeyError, IndexError) as exc:
            error = str(exc)
        # Capture explicit products, not caches, environment trees or the original CAS.
        if output.exists():
            product_paths = []
            for directory, subdirs, names in os.walk(output, followlinks=False):
                subdirs[:] = [name for name in subdirs if name not in {"cache", "tmp", "home"}]
                product_paths.extend(Path(directory) / name for name in names)
            for path in sorted(product_paths):
                relative = path.relative_to(output)
                if relative.parts[0] in {"cache", "tmp", "home"} or not path.is_file():
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
                    error, status, image = "unsafe_preview_output", "failed", None
                    continue
                ref = self.store.write_artifact(
                    path.read_bytes(),
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
                outputs[relative.as_posix()] = ref.model_dump(mode="json")
        receipt.update(status=status, error_code=error, wall_seconds=time.monotonic() - start)
        ref = self.store.write_artifact(_json(receipt), "application/json")
        (attempt / "receipt.json").write_bytes(_json(receipt))
        return AssetPreviewProof(
            status=status,
            version_sha256=version.version_sha256,
            image=image,
            receipt=ref,
            error_code=error,
        )
