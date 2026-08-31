"""Parameter-aware dependency closure for ``text2env.replay``.

The resolver derives every invocation dependency from immutable policy identities
and the current public input.  Runtime assets are snapshotted into CAS during
resolution, but the snapshot object is never retained in resolver state: the handler
must independently reproduce the same digest and compare it with RunContext.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.schema import ResolvedSceneSpec, SceneSpec

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .package_store import PackageStore, PackageStoreError
from .registry import DependencyResolutionError
from .runtime_assets import RuntimeAssetSnapshotError, RuntimeAssetStore
from .schemas import DependencyRef, EnvironmentPackage, Text2EnvReplayInput

TEXT2ENV_REPLAY_SKILL_REF = "text2env.replay@1.0.0"
REPLAY_CAPABILITY_DEPENDENCY = "text2env.replay.capability"
REPLAY_EXECUTOR_DEPENDENCY = "text2env.replay.executor"
REPLAY_HANDLER_CONFIG_DEPENDENCY = "text2env.replay.handler_config"
REPLAY_MEDIA_VERIFIER_DEPENDENCY = "text2env.replay.media_verifier"
REPLAY_RUNTIME_ASSET_DEPENDENCY = "text2env.replay.runtime_assets"
REPLAY_DEPENDENCY_NAMES = frozenset(
    {
        REPLAY_CAPABILITY_DEPENDENCY,
        REPLAY_EXECUTOR_DEPENDENCY,
        REPLAY_HANDLER_CONFIG_DEPENDENCY,
        REPLAY_MEDIA_VERIFIER_DEPENDENCY,
        REPLAY_RUNTIME_ASSET_DEPENDENCY,
    }
)


@dataclass(frozen=True, slots=True)
class Text2EnvReplayDependencyResolver:
    """Resolve the exact five-dependency closure for one replay input."""

    artifact_store: LocalArtifactStore
    package_store: PackageStore
    allowed_asset_roots: tuple[Path, ...]
    runtime_executor_identity: Any
    media_verifier_identity: Any
    handler_config_sha256: str
    expected_capability_sha256: str
    work_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_store, LocalArtifactStore):
            raise TypeError("artifact_store must be a LocalArtifactStore")
        if not isinstance(self.package_store, PackageStore):
            raise TypeError("package_store must be a PackageStore")
        if self.package_store.artifact_store is not self.artifact_store:
            raise ValueError("package_store and artifact_store must share one CAS")
        _dependency_sha256(self.handler_config_sha256, label="handler_config_sha256")
        _dependency_sha256(
            self.expected_capability_sha256,
            label="expected_capability_sha256",
        )
        canonical_identity_sha256(self.runtime_executor_identity, label="runtime executor")
        canonical_identity_sha256(self.media_verifier_identity, label="media verifier")
        _trusted_work_root(self.work_root)

    def resolve(
        self,
        skill_ref: str,
        effective_parameters: Text2EnvReplayInput,
    ) -> tuple[DependencyRef, ...]:
        if skill_ref != TEXT2ENV_REPLAY_SKILL_REF:
            raise DependencyResolutionError("replay resolver received another skill_ref")
        if not isinstance(effective_parameters, Text2EnvReplayInput):
            raise DependencyResolutionError("replay resolver requires Text2EnvReplayInput")
        scratch_root = _trusted_work_root(self.work_root)
        temporary = Path(tempfile.mkdtemp(prefix="resolve-", dir=scratch_root))
        try:
            resolved, catalog = _materialize_bound_inputs(
                package=effective_parameters.environment_package,
                package_store=self.package_store,
                artifact_store=self.artifact_store,
                destination=temporary / "package",
            )
            try:
                snapshot = RuntimeAssetStore(self.artifact_store).snapshot(
                    resolved=resolved,
                    catalog=catalog,
                    allowed_roots=self.allowed_asset_roots,
                )
            except RuntimeAssetSnapshotError as error:
                raise DependencyResolutionError(
                    "replay runtime asset dependency is unavailable: " + error.reason
                ) from error
            return replay_dependency_set(
                runtime_executor_identity=self.runtime_executor_identity,
                handler_config_sha256=self.handler_config_sha256,
                expected_capability_sha256=self.expected_capability_sha256,
                runtime_asset_snapshot_sha256=snapshot.manifest.sha256,
                media_verifier_identity=self.media_verifier_identity,
            )
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def replay_dependency_set(
    *,
    runtime_executor_identity: Any,
    handler_config_sha256: str,
    expected_capability_sha256: str,
    runtime_asset_snapshot_sha256: str,
    media_verifier_identity: Any,
) -> tuple[DependencyRef, ...]:
    """Build the only accepted replay dependency set, sorted by stable name."""

    values = (
        DependencyRef(
            name=REPLAY_CAPABILITY_DEPENDENCY,
            version="1",
            sha256=_dependency_sha256(
                expected_capability_sha256,
                label="expected_capability_sha256",
            ),
        ),
        DependencyRef(
            name=REPLAY_EXECUTOR_DEPENDENCY,
            version="1",
            sha256=canonical_identity_sha256(
                runtime_executor_identity,
                label="runtime executor",
            ),
        ),
        DependencyRef(
            name=REPLAY_HANDLER_CONFIG_DEPENDENCY,
            version="1",
            sha256=_dependency_sha256(
                handler_config_sha256,
                label="handler_config_sha256",
            ),
        ),
        DependencyRef(
            name=REPLAY_MEDIA_VERIFIER_DEPENDENCY,
            version="1",
            sha256=canonical_identity_sha256(
                media_verifier_identity,
                label="media verifier",
            ),
        ),
        DependencyRef(
            name=REPLAY_RUNTIME_ASSET_DEPENDENCY,
            version="1",
            sha256=_dependency_sha256(
                runtime_asset_snapshot_sha256,
                label="runtime_asset_snapshot_sha256",
            ),
        ),
    )
    return tuple(sorted(values, key=lambda item: item.name))


def require_replay_dependencies(
    declared: tuple[DependencyRef, ...],
    expected: tuple[DependencyRef, ...],
) -> tuple[DependencyRef, ...]:
    """Return the exact declared set or fail without accepting extras/wildcards."""

    if not isinstance(declared, tuple) or not isinstance(expected, tuple):
        raise DependencyResolutionError("replay dependencies must be tuples")
    declared_names = tuple(item.name for item in declared if isinstance(item, DependencyRef))
    expected_names = tuple(item.name for item in expected if isinstance(item, DependencyRef))
    if len(declared_names) != len(declared) or len(expected_names) != len(expected):
        raise DependencyResolutionError("replay dependency records are invalid")
    if len(set(declared_names)) != len(declared_names):
        raise DependencyResolutionError("replay dependencies contain duplicate names")
    if len(set(expected_names)) != len(expected_names):
        raise DependencyResolutionError("expected replay dependency set is invalid")
    if set(declared_names) != REPLAY_DEPENDENCY_NAMES:
        raise DependencyResolutionError("replay dependency names are incomplete or unexpected")
    if set(expected_names) != REPLAY_DEPENDENCY_NAMES:
        raise DependencyResolutionError("expected replay dependency set is invalid")
    declared_by_name = {item.name: item for item in declared}
    expected_by_name = {item.name: item for item in expected}
    mismatched = tuple(
        name
        for name in sorted(REPLAY_DEPENDENCY_NAMES)
        if declared_by_name[name] != expected_by_name[name]
    )
    if mismatched:
        raise DependencyResolutionError(
            "replay dependency identities disagree: " + ",".join(mismatched)
        )
    return tuple(declared_by_name[name] for name in sorted(declared_by_name))


def canonical_identity_sha256(identity: Any, *, label: str) -> str:
    """Validate a public canonical-bytes/sha256 identity without internal coupling."""

    try:
        payload = identity.canonical_bytes
        declared = identity.sha256
    except AttributeError as error:
        raise ValueError(f"{label} identity is unavailable") from error
    if not isinstance(payload, bytes):
        raise ValueError(f"{label} canonical identity must be bytes")
    actual = hashlib.sha256(payload).hexdigest()
    if not isinstance(declared, str) or declared != actual:
        raise ValueError(f"{label} canonical identity digest is inconsistent")
    try:
        document = json.loads(
            payload,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} canonical identity is invalid JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} canonical identity must be one JSON object")
    return declared


def _materialize_bound_inputs(
    *,
    package: EnvironmentPackage,
    package_store: PackageStore,
    artifact_store: LocalArtifactStore,
    destination: Path,
) -> tuple[ResolvedSceneSpec, AssetCatalog]:
    try:
        root = package_store.materialize(package.package_manifest, destination)
    except PackageStoreError as error:
        raise DependencyResolutionError(
            "replay package dependency is unavailable: " + error.reason
        ) from error
    try:
        scene = SceneSpec.model_validate_json(
            (root / "scene_spec.json").read_text(encoding="utf-8")
        )
        resolved = ResolvedSceneSpec.model_validate_json(
            (root / "resolved_scene.json").read_text(encoding="utf-8")
        )
        manifest = _strict_json_object(root / "package_manifest.json")
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
        raise DependencyResolutionError("replay package content is invalid") from error
    try:
        catalog = load_catalog(artifact_store.resolve(package.asset_catalog).path)
    except (ArtifactResolutionError, OSError, ValueError, ValidationError) as error:
        reason = getattr(error, "reason", type(error).__name__)
        raise DependencyResolutionError(
            "replay catalog dependency is unavailable: " + str(reason)
        ) from error
    problems = _binding_problems(
        package,
        scene=scene,
        resolved=resolved,
        catalog=catalog,
        manifest=manifest,
    )
    if problems:
        raise DependencyResolutionError(
            "replay package/catalog binding failed: " + ",".join(problems)
        )
    return resolved, catalog


def _binding_problems(
    package: EnvironmentPackage,
    *,
    scene: SceneSpec,
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog,
    manifest: dict[str, Any],
) -> tuple[str, ...]:
    expected = {
        "package.scene": (package.scene_spec_sha256, scene.digest()),
        "package.resolved": (package.resolved_scene_sha256, resolved.digest()),
        "package.id": (package.package_id, resolved.digest()),
        "package.catalog": (package.asset_catalog.sha256, catalog.digest()),
        "resolved.scene": (resolved.source_scene_spec_sha256, scene.digest()),
        "resolved.catalog": (resolved.asset_catalog_sha256, catalog.digest()),
        "manifest.scene": (manifest.get("source_scene_spec_sha256"), scene.digest()),
        "manifest.resolved": (manifest.get("resolved_scene_sha256"), resolved.digest()),
        "manifest.catalog": (manifest.get("asset_catalog_sha256"), catalog.digest()),
        "package.seed": (package.seed, scene.seed),
        "resolved.seed": (resolved.seed, scene.seed),
        "manifest.seed": (manifest.get("seed"), scene.seed),
        "resolved.scene_id": (resolved.scene_id, scene.scene_id),
        "manifest.scene_id": (manifest.get("scene_id"), scene.scene_id),
    }
    return tuple(name for name, (actual, wanted) in expected.items() if actual != wanted)


def _trusted_work_root(value: Path) -> Path:
    if not isinstance(value, Path):
        raise TypeError("work_root must be a Path")
    root = value.expanduser().absolute()
    if root.exists() or root.is_symlink():
        mode = root.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError("dependency work_root must be a real directory")
    else:
        root.mkdir(parents=True)
    if root.resolve(strict=True) != root:
        raise ValueError("dependency work_root traverses a symlink")
    return root


def _strict_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON document contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON document contains non-standard constant: {value}")


def _dependency_sha256(value: str, *, label: str) -> str:
    try:
        return DependencyRef(name="validation", version="1", sha256=value).sha256
    except ValidationError as error:
        raise ValueError(f"{label} must be a SHA-256 digest") from error
