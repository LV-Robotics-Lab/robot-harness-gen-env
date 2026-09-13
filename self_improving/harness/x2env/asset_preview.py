"""Version-bound isolated candidate preview; never a final scene or physical qualification."""

import hashlib
import json
import mimetypes
import os
import time
import uuid
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Literal

from PIL import Image

from .assets import AssetRegistry, AssetStore, AssetVersion
from .contracts import ArtifactRef, Model, Sha256
from .genesis_runtime import RuntimeEntity, RuntimeMember, RuntimeScene, _verify, run_scene
from .source_identity import SourceIdentityPolicy, capture_source_identity


class AssetPreviewProof(Model):
    status: Literal["passed", "failed"]
    version_sha256: Sha256
    image: ArtifactRef | None
    receipt: ArtifactRef
    error_code: str | None = None


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def _preview_members(version, store):
    """Only normalized self-contained visual GLB and collision OBJ dependencies."""
    members = {item.path: item.artifact for item in version.files}
    if version.entrypoint != "asset.urdf" or not {"asset.urdf", "physics.json"} <= members.keys():
        raise ValueError("unsupported_preview_asset_closure")
    document = ET.fromstring(store.read_artifact(members["asset.urdf"]))
    if document.findall("joint") or len(document.findall("link")) != 1:
        raise ValueError("unsupported_preview_articulation")
    required = {"asset.urdf", "physics.json"}
    for kind, suffix in (("visual", ".glb"), ("collision", ".obj")):
        shapes = document.findall(f"link/{kind}")
        if not shapes:
            raise ValueError("unsupported_preview_asset_closure")
        for shape in shapes:
            geometries = shape.findall("geometry")
            if (
                len(geometries) != 1
                or len(geometries[0]) != 1
                or geometries[0][0].tag != "mesh"
            ):
                raise ValueError("unsupported_preview_asset_closure")
            mesh = geometries[0][0]
            name = mesh.get("filename", "") if mesh is not None else ""
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or str(path) != name
                or "\\" in name
                or path.suffix != suffix
                or name not in members
            ):
                raise ValueError("unsupported_preview_asset_closure")
            required.add(name)
    if members.keys() != required:
        raise ValueError("unsupported_preview_asset_closure")


class AssetPreviewRenderer:
    def __init__(
        self,
        store: AssetStore,
        runtime_roots,
        root: Path,
        seed: int,
        *,
        runner=run_scene,
        denied_roots=(),
        source_identity_policy: SourceIdentityPolicy | None = None,
    ):
        self.store, self.runtime_roots, self.root, self.seed = (
            store,
            runtime_roots,
            Path(root),
            seed,
        )
        self.runner = runner
        self.denied_roots = tuple(denied_roots)
        self.source_identity_policy = source_identity_policy

    def render(self, version: AssetVersion, *, timeout: int = 600) -> AssetPreviewProof:
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("invalid preview timeout")
        if not self.root.is_absolute() or any(
            p.is_symlink() for p in (self.root, *self.root.parents)
        ):
            raise ValueError("unsafe_preview_root")
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
        try:
            receipt["identity"] = capture_source_identity(
                Path(__file__).parent, policy=self.source_identity_policy
            ).model_dump(mode="json")
            verified = AssetRegistry(self.store).inspect(version.version_sha256)
            if verified != version:
                raise ValueError("asset_version_mismatch")
            _preview_members(version, self.store)
            package.mkdir()
            for member in version.files:
                path = package / member.path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.store.read_artifact(member.artifact))
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
            _verify(scene, package)
            # The child enforces an allowlist of this copied package, outputs and declared runtime.
            # Denials come from deployment, never from a guessed checkout/site-packages root.
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise ValueError("preview_timeout")
            result = self.runner(
                scene,
                package_root=package,
                runtime_roots=self.runtime_roots,
                output_dir=output,
                profile="load_step_smoke",
                denied_roots=self.denied_roots,
                timeout_seconds=remaining,
            )
            receipt["runtime_result_ref"] = self.store.write_artifact(
                _json(result) + b"\n", "application/json"
            ).model_dump(mode="json")
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
