"""Immutable, path-free runtime asset snapshots for physical replay.

The module hides source-root validation, catalog/scene binding, symlink-safe
single-read CAS admission, deterministic manifests, read-only materialization,
and whole-tree pre/post verification behind two small public seams:
``RuntimeAssetStore`` for the harness and ``verify_runtime_asset_snapshot`` for
the isolated worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from scene_gen.catalog import AssetCatalog, CatalogEntry, CatalogModel
from scene_gen.schema import ResolvedSceneSpec

from .artifacts import LocalArtifactStore
from .schemas import ArtifactRef

RUNTIME_ASSET_SNAPSHOT_SCHEMA = "harness.runtime_asset_snapshot.v1"
RUNTIME_ASSET_MAX_ASSETS = 256
RUNTIME_ASSET_MAX_DIRECTORIES = 100_000
RUNTIME_ASSET_MAX_FILES = 100_000
RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024 * 1024
RUNTIME_ASSET_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
RUNTIME_ASSET_PREFLIGHT_EXIT_CODE = 86
RUNTIME_ASSET_DRIFT_EXIT_CODE = 87
RUNTIME_ASSET_SNAPSHOT_PROTOCOL = {
    "manifest_schema": RUNTIME_ASSET_SNAPSHOT_SCHEMA,
    "tree_strategy": "selected_asset_complete_tree.v1",
    "transport": "cas_materialized_read_only_tree",
    "required_with_event_fd": True,
    "verification": "whole_tree_before_first_event_and_after_runtime_close",
    "failure_exit_codes": {
        "preflight": RUNTIME_ASSET_PREFLIGHT_EXIT_CODE,
        "postflight_drift": RUNTIME_ASSET_DRIFT_EXIT_CODE,
    },
    "limits": {
        "max_assets": RUNTIME_ASSET_MAX_ASSETS,
        "max_directories": RUNTIME_ASSET_MAX_DIRECTORIES,
        "max_files": RUNTIME_ASSET_MAX_FILES,
        "max_single_file_bytes": RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES,
        "max_total_bytes": RUNTIME_ASSET_MAX_TOTAL_BYTES,
    },
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 33_554_432
_MAX_LOADER_DOCUMENT_BYTES = 64 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_LOADER_DOCUMENT_SUFFIXES = frozenset({".dae", ".glb", ".gltf", ".mtl", ".obj", ".urdf"})
_LOADER_LEAF_SUFFIXES = frozenset(
    {
        ".bin",
        ".bmp",
        ".dds",
        ".exr",
        ".hdr",
        ".jpeg",
        ".jpg",
        ".json",
        ".ktx",
        ".ktx2",
        ".off",
        ".pcd",
        ".ply",
        ".png",
        ".stl",
        ".tga",
        ".webp",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "resolved_scene_sha256",
        "asset_catalog_sha256",
        "assets",
    }
)
_ASSET_FIELDS = frozenset(
    {
        "asset_id",
        "selected_model_ids",
        "tree_sha256",
        "file_count",
        "bytes",
        "directories",
        "required_files",
        "files",
    }
)
_FILE_FIELDS = frozenset({"path", "sha256", "bytes"})


class RuntimeAssetSnapshotError(RuntimeError):
    """Selected loader inputs are unavailable, unsafe, or hash-inconsistent."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message if message is not None else reason)


class RuntimeAssetWorkerError(RuntimeAssetSnapshotError):
    """Phase-tagged trust failure mapped to a reserved worker exit status."""

    def __init__(self, phase: str, error: RuntimeAssetSnapshotError) -> None:
        if phase not in {"preflight", "postflight"}:
            raise ValueError("runtime asset worker phase is invalid")
        self.phase = phase
        self.exit_code = (
            RUNTIME_ASSET_PREFLIGHT_EXIT_CODE
            if phase == "preflight"
            else RUNTIME_ASSET_DRIFT_EXIT_CODE
        )
        super().__init__(error.reason, str(error))


@dataclass(frozen=True, slots=True)
class RuntimeAssetSnapshot:
    """CAS identity for one selected-asset closure and its admitted members."""

    manifest: ArtifactRef
    members: tuple[ArtifactRef, ...]


@dataclass(frozen=True, slots=True)
class MaterializedRuntimeAssets:
    """Attempt-local, read-only loader inputs reconstructed solely from CAS."""

    root: Path
    manifest_path: Path
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeAssets:
    """Whole-tree verification result consumed directly by the worker loader."""

    manifest_sha256: str
    object_roots: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class _SelectedAsset:
    entry: CatalogEntry
    model_ids: tuple[int, ...]
    required_files: frozenset[str]
    allowed_root: Path
    relative_to_allowed: Path


@dataclass(slots=True)
class _SnapshotBudget:
    directories: int = 0
    files: int = 0
    total_bytes: int = 0

    def admit_directory(self) -> None:
        self.directories += 1
        if self.directories > RUNTIME_ASSET_MAX_DIRECTORIES:
            raise RuntimeAssetSnapshotError("directory_count_limit")

    def admit_file(self, size: int) -> None:
        if size > RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES:
            raise RuntimeAssetSnapshotError("single_file_bytes_limit")
        self.files += 1
        if self.files > RUNTIME_ASSET_MAX_FILES:
            raise RuntimeAssetSnapshotError("file_count_limit")
        self.total_bytes += size
        if self.total_bytes > RUNTIME_ASSET_MAX_TOTAL_BYTES:
            raise RuntimeAssetSnapshotError("total_bytes_limit")


class RuntimeAssetStore:
    """Snapshot selected loader trees into CAS and reconstruct immutable inputs."""

    def __init__(self, artifact_store: LocalArtifactStore) -> None:
        if not isinstance(artifact_store, LocalArtifactStore):
            raise TypeError("artifact_store must be a LocalArtifactStore")
        self.artifact_store = artifact_store

    def snapshot(
        self,
        *,
        resolved: ResolvedSceneSpec,
        catalog: AssetCatalog,
        allowed_roots: tuple[Path, ...],
    ) -> RuntimeAssetSnapshot:
        if not isinstance(resolved, ResolvedSceneSpec):
            raise TypeError("resolved must be a ResolvedSceneSpec")
        if not isinstance(catalog, AssetCatalog):
            raise TypeError("catalog must be an AssetCatalog")
        if resolved.asset_catalog_sha256 != catalog.digest():
            raise RuntimeAssetSnapshotError(
                "asset_catalog_mismatch",
                "resolved scene and selected asset catalog have different identities",
            )
        roots = _trusted_roots(allowed_roots)
        selected = _bind_selected_assets(resolved, catalog, roots)
        if len(selected) > RUNTIME_ASSET_MAX_ASSETS:
            raise RuntimeAssetSnapshotError("asset_count_limit")
        budget = _SnapshotBudget()
        manifest_assets: list[dict[str, Any]] = []
        members: list[ArtifactRef] = []
        for asset in selected:
            directory_fd = _open_directory_beneath(
                asset.allowed_root,
                asset.relative_to_allowed,
                label=f"asset {asset.entry.asset_id}",
            )
            try:
                files, asset_members, directories = _snapshot_tree_to_cas(
                    directory_fd,
                    artifact_store=self.artifact_store,
                    label=f"asset {asset.entry.asset_id}",
                    budget=budget,
                )
            finally:
                os.close(directory_fd)
            if not files:
                raise RuntimeAssetSnapshotError(
                    "asset_tree_empty",
                    f"asset tree is empty: {asset.entry.asset_id}",
                )
            missing = sorted(asset.required_files - {item["path"] for item in files})
            if missing:
                raise RuntimeAssetSnapshotError(
                    "catalog_file_missing",
                    "selected catalog source files are missing from "
                    f"{asset.entry.asset_id}: {missing}",
                )
            _validate_loader_reference_closure(
                asset,
                files=files,
                artifact_store=self.artifact_store,
            )
            _validate_selected_model_directories(asset, directories)
            ordered_directories = sorted(directories)
            record = {
                "asset_id": asset.entry.asset_id,
                "selected_model_ids": list(asset.model_ids),
                "tree_sha256": _tree_sha256(ordered_directories, files),
                "file_count": len(files),
                "bytes": sum(item["bytes"] for item in files),
                "directories": ordered_directories,
                "required_files": sorted(asset.required_files),
                "files": files,
            }
            manifest_assets.append(record)
            members.extend(asset_members)
        manifest = {
            "schema_version": RUNTIME_ASSET_SNAPSHOT_SCHEMA,
            "resolved_scene_sha256": resolved.digest(),
            "asset_catalog_sha256": catalog.digest(),
            "assets": manifest_assets,
        }
        manifest_payload = canonical_runtime_asset_manifest_bytes(manifest)
        _parse_manifest(manifest_payload)
        manifest_ref = _put_bytes_in_cas(
            self.artifact_store,
            manifest_payload,
            name="runtime_asset_snapshot",
            media_type="application/json",
            schema_version=RUNTIME_ASSET_SNAPSHOT_SCHEMA,
        )
        return RuntimeAssetSnapshot(
            manifest=manifest_ref,
            members=_unique_artifacts(tuple(members)),
        )

    def materialize(
        self,
        manifest: ArtifactRef,
        destination: Path,
    ) -> MaterializedRuntimeAssets:
        if not isinstance(manifest, ArtifactRef):
            raise TypeError("manifest must be an ArtifactRef")
        if manifest.schema_version != RUNTIME_ASSET_SNAPSHOT_SCHEMA:
            raise RuntimeAssetSnapshotError(
                "manifest_schema_mismatch",
                f"runtime asset manifest schema must be {RUNTIME_ASSET_SNAPSHOT_SCHEMA}",
            )
        payload = _read_cas_ref(
            self.artifact_store,
            manifest,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        document = _parse_manifest(payload)
        target = Path(destination).expanduser().absolute()
        if target.exists() or target.is_symlink():
            raise RuntimeAssetSnapshotError(
                "destination_exists",
                f"runtime asset materialization destination already exists: {target}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            objects_root = staging / "objects"
            objects_root.mkdir()
            for asset in document["assets"]:
                asset_root = objects_root / asset["asset_id"]
                asset_root.mkdir()
                for directory in asset["directories"][1:]:
                    relative = _safe_relative(directory, label="runtime asset directory")
                    asset_root.joinpath(*relative.parts).mkdir()
                for record in asset["files"]:
                    relative = _safe_relative(record["path"], label="runtime asset file")
                    output = asset_root.joinpath(*relative.parts)
                    member = ArtifactRef(
                        name=f"runtime_asset_{record['sha256'][:16]}",
                        uri=f"artifact://sha256/{record['sha256']}",
                        media_type="application/octet-stream",
                        sha256=record["sha256"],
                        bytes=record["bytes"],
                        schema_version=None,
                    )
                    _copy_cas_ref(self.artifact_store, member, output)
            manifest_path = staging / "runtime_asset_snapshot.json"
            manifest_path.write_bytes(payload)
            _verify_root_against_manifest(objects_root, document)
            _make_tree_read_only(objects_root)
            manifest_path.chmod(0o444)
            staging.chmod(0o555)
            os.replace(staging, target)
            return MaterializedRuntimeAssets(
                root=target / "objects",
                manifest_path=target / "runtime_asset_snapshot.json",
                manifest_sha256=manifest.sha256,
            )
        except BaseException:
            _make_tree_owner_writable(staging)
            shutil.rmtree(staging, ignore_errors=True)
            raise


def canonical_runtime_asset_manifest_bytes(value: Any) -> bytes:
    """Encode the path-free manifest deterministically."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def verify_runtime_asset_snapshot(
    *,
    root: Path,
    manifest_path: Path,
    expected_sha256: str,
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog | None = None,
) -> VerifiedRuntimeAssets:
    """Verify an exact staged tree and derive the only loader-root mapping."""

    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise RuntimeAssetSnapshotError("expected_digest_invalid")
    if not isinstance(resolved, ResolvedSceneSpec):
        raise TypeError("resolved must be a ResolvedSceneSpec")
    if catalog is not None and not isinstance(catalog, AssetCatalog):
        raise TypeError("catalog must be an AssetCatalog or None")
    payload = _read_regular_file(
        Path(manifest_path),
        label="runtime asset manifest",
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeAssetSnapshotError(
            "manifest_digest_mismatch",
            "runtime asset manifest does not match its expected digest",
        )
    document = _parse_manifest(payload)
    if document["resolved_scene_sha256"] != resolved.digest():
        raise RuntimeAssetSnapshotError(
            "resolved_scene_mismatch",
            "runtime asset manifest is bound to another resolved scene",
        )
    if document["asset_catalog_sha256"] != resolved.asset_catalog_sha256:
        raise RuntimeAssetSnapshotError(
            "asset_catalog_mismatch",
            "runtime asset manifest and resolved scene name different asset catalogs",
        )
    if catalog is not None and document["asset_catalog_sha256"] != catalog.digest():
        raise RuntimeAssetSnapshotError(
            "asset_catalog_mismatch",
            "runtime asset manifest is bound to another asset catalog",
        )
    expected_selection = _resolved_selection(resolved)
    manifest_selection = {
        asset["asset_id"]: tuple(asset["selected_model_ids"]) for asset in document["assets"]
    }
    if manifest_selection != expected_selection:
        raise RuntimeAssetSnapshotError(
            "selected_assets_mismatch",
            "runtime asset manifest does not exactly identify the selected assets/models",
        )
    if catalog is not None:
        expected_loader_roots = _catalog_loader_roots(resolved, catalog)
        actual_loader_roots = {
            asset["asset_id"]: tuple(asset["required_files"]) for asset in document["assets"]
        }
        if actual_loader_roots != expected_loader_roots:
            raise RuntimeAssetSnapshotError(
                "loader_roots_mismatch",
                "runtime asset manifest does not identify the catalog loader roots",
            )
    normalized_root = _real_directory(Path(root), label="runtime asset root")
    _verify_root_against_manifest(normalized_root, document)
    _validate_materialized_loader_closures(normalized_root, document)
    object_roots = MappingProxyType(
        _verified_object_roots(
            normalized_root,
            resolved=resolved,
            catalog=catalog,
        )
    )
    return VerifiedRuntimeAssets(
        manifest_sha256=actual_sha256,
        object_roots=object_roots,
    )


def _trusted_roots(values: tuple[Path, ...]) -> tuple[Path, ...]:
    if not isinstance(values, tuple) or not values:
        raise RuntimeAssetSnapshotError("allowed_roots_empty")
    result: list[Path] = []
    for value in values:
        if not isinstance(value, Path):
            raise RuntimeAssetSnapshotError("allowed_root_invalid")
        root = value.expanduser().absolute()
        try:
            root_stat = root.lstat()
        except OSError as error:
            raise RuntimeAssetSnapshotError(
                "allowed_root_unavailable",
                f"allowed asset root is unavailable: {root}",
            ) from error
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise RuntimeAssetSnapshotError(
                "allowed_root_unsafe",
                f"allowed asset root must be a real directory: {root}",
            )
        if root in result:
            raise RuntimeAssetSnapshotError("allowed_root_duplicate")
        result.append(root)
    return tuple(sorted(result, key=lambda item: (-len(item.parts), item.as_posix())))


def _bind_selected_assets(
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog,
    allowed_roots: tuple[Path, ...],
) -> tuple[_SelectedAsset, ...]:
    entries: dict[str, CatalogEntry] = {}
    for entry in catalog.entries:
        if entry.asset_id in entries:
            raise RuntimeAssetSnapshotError(
                "catalog_entry_duplicate",
                f"asset catalog duplicates {entry.asset_id}",
            )
        entries[entry.asset_id] = entry
    grouped: dict[str, list[Any]] = {}
    for item in resolved.objects:
        grouped.setdefault(item.asset_id, []).append(item)
    selected: list[_SelectedAsset] = []
    for asset_id in sorted(grouped):
        entry = entries.get(asset_id)
        if entry is None:
            raise RuntimeAssetSnapshotError(
                "catalog_entry_missing",
                f"selected asset is absent from the catalog: {asset_id}",
            )
        if not entry.available:
            raise RuntimeAssetSnapshotError(
                "catalog_entry_unavailable",
                f"selected catalog asset is unavailable: {asset_id}",
            )
        entry_root = _absolute_catalog_path(entry.asset_path, label="catalog asset_path")
        if entry_root.name != asset_id:
            raise RuntimeAssetSnapshotError(
                "asset_root_name_mismatch",
                f"catalog asset_path basename does not equal asset_id: {asset_id}",
            )
        allowed = next(
            (
                root
                for root in allowed_roots
                if entry_root == root or entry_root.is_relative_to(root)
            ),
            None,
        )
        if allowed is None:
            raise RuntimeAssetSnapshotError(
                "asset_root_escape",
                f"selected asset is outside every allowed root: {asset_id}",
            )
        models: dict[int, CatalogModel] = {}
        for model in entry.models:
            if model.model_id in models:
                raise RuntimeAssetSnapshotError(
                    "catalog_model_duplicate",
                    f"catalog duplicates {asset_id}/model{model.model_id}",
                )
            models[model.model_id] = model
        required_files: set[str] = set()
        selected_model_ids: set[int] = set()
        for item in grouped[asset_id]:
            if item.load_type != entry.load_type:
                raise RuntimeAssetSnapshotError(
                    "load_type_mismatch",
                    f"resolved/catalog load_type mismatch for {asset_id}",
                )
            model = models.get(item.model_id)
            if model is None:
                raise RuntimeAssetSnapshotError(
                    "catalog_model_missing",
                    f"selected catalog model is missing: {asset_id}/model{item.model_id}",
                )
            if not model.usable:
                raise RuntimeAssetSnapshotError(
                    "catalog_model_unusable",
                    f"selected catalog model is unusable: {asset_id}/model{item.model_id}",
                )
            model_root = _absolute_catalog_path(
                model.model_path,
                label=f"catalog {asset_id}/model{item.model_id} model_path",
            )
            _relative_inside(entry_root, model_root, label="catalog model_path")
            expected_sources = {
                _absolute_catalog_path(value, label="catalog model source")
                for value in (
                    model.metadata_path,
                    model.visual_path,
                    model.collision_path,
                    model.urdf_path,
                )
                if value is not None
            }
            if item.generation_metadata_path is not None:
                expected_sources.add(
                    _absolute_catalog_path(
                        item.generation_metadata_path,
                        label="resolved generation metadata",
                    )
                )
            actual_sources = tuple(
                _absolute_catalog_path(value, label="resolved source_files")
                for value in item.source_files
            )
            if (
                len(set(actual_sources)) != len(actual_sources)
                or set(actual_sources) != expected_sources
            ):
                raise RuntimeAssetSnapshotError(
                    "source_files_mismatch",
                    "resolved source_files do not exactly match catalog model sources "
                    f"for {asset_id}",
                )
            if not actual_sources:
                raise RuntimeAssetSnapshotError(
                    "source_files_empty",
                    f"selected asset declares no loader source files: {asset_id}",
                )
            for source in actual_sources:
                relative = _relative_inside(entry_root, source, label="selected source file")
                required_files.add(relative.as_posix())
            selected_model_ids.add(item.model_id)
        selected.append(
            _SelectedAsset(
                entry=entry,
                model_ids=tuple(sorted(selected_model_ids)),
                required_files=frozenset(required_files),
                allowed_root=allowed,
                relative_to_allowed=entry_root.relative_to(allowed),
            )
        )
    return tuple(selected)


def _catalog_loader_roots(
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog,
) -> dict[str, tuple[str, ...]]:
    entries: dict[str, CatalogEntry] = {}
    for entry in catalog.entries:
        if entry.asset_id in entries:
            raise RuntimeAssetSnapshotError("catalog_entry_duplicate")
        entries[entry.asset_id] = entry
    grouped: dict[str, set[str]] = {}
    for item in resolved.objects:
        entry = entries.get(item.asset_id)
        if entry is None:
            raise RuntimeAssetSnapshotError("catalog_entry_missing")
        model = next((value for value in entry.models if value.model_id == item.model_id), None)
        if model is None:
            raise RuntimeAssetSnapshotError("catalog_model_missing")
        entry_root = _absolute_catalog_path(entry.asset_path, label="catalog asset_path")
        expected_sources = {
            _absolute_catalog_path(value, label="catalog model source")
            for value in (
                model.metadata_path,
                model.visual_path,
                model.collision_path,
                model.urdf_path,
            )
            if value is not None
        }
        if item.generation_metadata_path is not None:
            expected_sources.add(
                _absolute_catalog_path(
                    item.generation_metadata_path,
                    label="resolved generation metadata",
                )
            )
        actual_sources = tuple(
            _absolute_catalog_path(value, label="resolved source_files")
            for value in item.source_files
        )
        if (
            len(actual_sources) != len(set(actual_sources))
            or set(actual_sources) != expected_sources
        ):
            raise RuntimeAssetSnapshotError("source_files_mismatch")
        for source in actual_sources:
            relative = _relative_inside(entry_root, source, label="selected source file")
            grouped.setdefault(item.asset_id, set()).add(relative.as_posix())
    return {
        asset_id: tuple(sorted(required_files))
        for asset_id, required_files in sorted(grouped.items())
    }


def _validate_selected_model_directories(
    asset: _SelectedAsset,
    directories: frozenset[str],
) -> None:
    entry_root = _absolute_catalog_path(asset.entry.asset_path, label="catalog asset_path")
    by_id = {model.model_id: model for model in asset.entry.models}
    for model_id in asset.model_ids:
        model = by_id[model_id]
        model_path = _absolute_catalog_path(
            model.model_path,
            label=f"catalog {asset.entry.asset_id}/model{model_id} model_path",
        )
        relative = _relative_inside(entry_root, model_path, label="catalog model_path")
        logical = relative.as_posix() if relative.parts else "."
        if logical not in directories:
            raise RuntimeAssetSnapshotError(
                "catalog_model_root_missing",
                f"catalog model_path is not a directory in the selected tree: {logical}",
            )
        if asset.entry.load_type == "urdf":
            urdf_path = _absolute_catalog_path(model.urdf_path, label="catalog mobility URDF")
            urdf_relative = _relative_inside(
                entry_root,
                urdf_path,
                label="catalog mobility URDF",
            )
            if urdf_relative != relative / "mobility.urdf":
                raise RuntimeAssetSnapshotError("runtime_urdf_unsupported")


def _validate_loader_reference_closure(
    asset: _SelectedAsset,
    *,
    files: list[dict[str, Any]],
    artifact_store: LocalArtifactStore,
) -> None:
    """Prove every supported external loader reference stays in the captured tree."""

    records = {record["path"]: record for record in files}

    def read_payload(record: dict[str, Any]) -> bytes:
        ref = ArtifactRef(
            name=f"runtime_asset_{record['sha256'][:16]}",
            uri=f"artifact://sha256/{record['sha256']}",
            media_type="application/octet-stream",
            sha256=record["sha256"],
            bytes=record["bytes"],
            schema_version=None,
        )
        return _read_cas_ref(
            artifact_store,
            ref,
            max_bytes=min(_MAX_LOADER_DOCUMENT_BYTES, record["bytes"]),
        )

    _validate_loader_reference_graph(
        asset_id=asset.entry.asset_id,
        required_files=asset.required_files,
        records=records,
        read_payload=read_payload,
    )


def _validate_loader_reference_graph(
    *,
    asset_id: str,
    required_files: frozenset[str] | tuple[str, ...],
    records: dict[str, dict[str, Any]],
    read_payload: Callable[[dict[str, Any]], bytes],
) -> None:
    pending = sorted(required_files)
    visited: set[str] = set()
    while pending:
        logical = pending.pop(0)
        if logical in visited:
            continue
        visited.add(logical)
        record = records.get(logical)
        if record is None:
            raise RuntimeAssetSnapshotError(
                "loader_reference_missing",
                f"loader closure member is absent: {asset_id}/{logical}",
            )
        suffix = PurePosixPath(logical).suffix.lower()
        if suffix in _LOADER_LEAF_SUFFIXES:
            continue
        if suffix not in _LOADER_DOCUMENT_SUFFIXES:
            raise RuntimeAssetSnapshotError(
                "loader_document_unsupported",
                f"loader dependency format is not closed by the snapshot protocol: {logical}",
            )
        payload = read_payload(record)
        for raw_reference in _loader_references(suffix, payload):
            if suffix in {".glb", ".gltf"} and raw_reference.startswith("data:"):
                continue
            dependency = _normalize_loader_reference(logical, raw_reference)
            if dependency not in records:
                raise RuntimeAssetSnapshotError(
                    "loader_reference_missing",
                    "loader reference is absent from the immutable snapshot: "
                    f"{asset_id}/{dependency}",
                )
            if dependency not in visited:
                pending.append(dependency)
        pending.sort()


def _validate_materialized_loader_closures(
    root: Path,
    document: dict[str, Any],
) -> None:
    for asset in document["assets"]:
        records = {record["path"]: record for record in asset["files"]}
        asset_root = root / asset["asset_id"]

        def read_payload(record: dict[str, Any]) -> bytes:
            return _read_regular_file(
                asset_root.joinpath(*PurePosixPath(record["path"]).parts),
                label="materialized loader document",
                max_bytes=min(_MAX_LOADER_DOCUMENT_BYTES, record["bytes"]),
            )

        _validate_loader_reference_graph(
            asset_id=asset["asset_id"],
            required_files=tuple(asset["required_files"]),
            records=records,
            read_payload=read_payload,
        )


def _loader_references(suffix: str, payload: bytes) -> tuple[str, ...]:
    try:
        if suffix == ".glb":
            value, has_binary_chunk = _glb_document(payload)
            return _gltf_references(value, allow_implicit_buffer=has_binary_chunk)
        text = payload.decode("utf-8", errors="strict")
        if suffix == ".urdf":
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("external XML declarations are forbidden")
            root = ET.fromstring(text)
            references = []
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag in {"mesh", "texture"} and "filename" in element.attrib:
                    references.append(element.attrib["filename"])
            return tuple(references)
        if suffix == ".obj":
            references = []
            for line in text.splitlines():
                tokens = shlex.split(line, comments=True, posix=True)
                if tokens and tokens[0].lower() == "mtllib":
                    if len(tokens) < 2:
                        raise ValueError("empty mtllib")
                    references.extend(tokens[1:])
            return tuple(references)
        if suffix == ".mtl":
            texture_commands = {
                "bump",
                "decal",
                "disp",
                "map_bump",
                "map_d",
                "map_ka",
                "map_kd",
                "map_ks",
                "map_ns",
                "norm",
                "refl",
            }
            references = []
            for line in text.splitlines():
                tokens = shlex.split(line, comments=True, posix=True)
                if tokens and tokens[0].lower() in texture_commands:
                    if len(tokens) < 2:
                        raise ValueError("empty material texture reference")
                    references.append(tokens[-1])
            return tuple(references)
        if suffix == ".dae":
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("external XML declarations are forbidden")
            root = ET.fromstring(text)
            references = []
            for element in root.iter():
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "image":
                    for child in element.iter():
                        if child.tag.rsplit("}", 1)[-1] == "init_from":
                            if child.text is None or not child.text.strip():
                                raise ValueError("empty COLLADA image reference")
                            references.append(child.text.strip())
                raw_url = element.attrib.get("url")
                if raw_url and not raw_url.startswith("#"):
                    external_path = raw_url.split("#", 1)[0]
                    references.append(external_path)
            return tuple(references)
        value = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        return _gltf_references(value, allow_implicit_buffer=False)
    except (UnicodeError, ET.ParseError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeAssetSnapshotError("loader_document_invalid") from error


def loader_document_references(logical_path: str, payload: bytes) -> tuple[str, ...]:
    """Enumerate references from one supported loader document."""

    suffix = PurePosixPath(logical_path).suffix.lower()
    if suffix not in _LOADER_DOCUMENT_SUFFIXES:
        return ()
    return _loader_references(suffix, payload)


def _glb_document(payload: bytes) -> tuple[object, bool]:
    if len(payload) < 20 or payload[:4] != b"glTF":
        raise ValueError("GLB header is invalid")
    if int.from_bytes(payload[4:8], "little") != 2:
        raise ValueError("GLB version is unsupported")
    if int.from_bytes(payload[8:12], "little") != len(payload):
        raise ValueError("GLB declared length is invalid")
    offset = 12
    json_payload: bytes | None = None
    has_binary_chunk = False
    chunk_index = 0
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("GLB chunk header is truncated")
        chunk_length = int.from_bytes(payload[offset : offset + 4], "little")
        chunk_type = payload[offset + 4 : offset + 8]
        offset += 8
        chunk_end = offset + chunk_length
        if chunk_length % 4 or chunk_end > len(payload):
            raise ValueError("GLB chunk length is invalid")
        chunk = payload[offset:chunk_end]
        offset = chunk_end
        if chunk_index == 0 and chunk_type != b"JSON":
            raise ValueError("GLB first chunk is not JSON")
        if chunk_type == b"JSON":
            if json_payload is not None:
                raise ValueError("GLB has duplicate JSON chunks")
            json_payload = chunk.rstrip(b" \t\r\n\x00")
        elif chunk_type == b"BIN\x00":
            if has_binary_chunk:
                raise ValueError("GLB has duplicate binary chunks")
            has_binary_chunk = True
        chunk_index += 1
    if json_payload is None or not json_payload:
        raise ValueError("GLB JSON chunk is missing")
    value = json.loads(
        json_payload.decode("utf-8", errors="strict"),
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )
    return value, has_binary_chunk


def _gltf_references(value: object, *, allow_implicit_buffer: bool) -> tuple[str, ...]:
    if not isinstance(value, dict):
        raise ValueError("gltf root is not an object")
    references = []
    implicit_buffers = 0
    for collection_name in ("buffers", "images"):
        collection = value.get(collection_name, [])
        if not isinstance(collection, list):
            raise ValueError("gltf reference collection is invalid")
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError("gltf reference is invalid")
            uri = item.get("uri")
            if uri is not None:
                if not isinstance(uri, str) or not uri:
                    raise ValueError("gltf URI is invalid")
                references.append(uri)
                continue
            if collection_name == "buffers":
                implicit_buffers += 1
                if not allow_implicit_buffer or implicit_buffers > 1:
                    raise ValueError("gltf buffer has no bound payload")
            elif type(item.get("bufferView")) is not int or item["bufferView"] < 0:
                raise ValueError("gltf image has no bound payload")
    return tuple(references)


def _normalize_loader_reference(source: str, reference: str) -> str:
    if (
        not isinstance(reference, str)
        or not reference
        or "\x00" in reference
        or "\\" in reference
        or "%" in reference
    ):
        raise RuntimeAssetSnapshotError("loader_reference_unsafe")
    parsed = urlparse(reference)
    windows = PureWindowsPath(reference)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or PurePosixPath(reference).is_absolute()
        or windows.is_absolute()
        or windows.drive
    ):
        raise RuntimeAssetSnapshotError("loader_reference_unsafe")
    parts = list(PurePosixPath(source).parent.parts)
    for part in PurePosixPath(reference).parts:
        if part == "..":
            if not parts:
                raise RuntimeAssetSnapshotError("loader_reference_unsafe")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise RuntimeAssetSnapshotError("loader_reference_unsafe")
    return PurePosixPath(*parts).as_posix()


def normalize_loader_reference(source: str, reference: str) -> str:
    """Normalize a loader reference without allowing URI or filesystem escape syntax."""

    return _normalize_loader_reference(source, reference)


def _verified_object_roots(
    root: Path,
    *,
    resolved: ResolvedSceneSpec,
    catalog: AssetCatalog | None,
) -> dict[str, Path]:
    entries = {entry.asset_id: entry for entry in catalog.entries} if catalog else {}
    result: dict[str, Path] = {}
    for item in resolved.objects:
        asset_root = root / item.asset_id
        if item.load_type != "urdf":
            result[item.object_id] = asset_root
            continue
        if catalog is None:
            raise RuntimeAssetSnapshotError(
                "asset_catalog_required",
                "URDF replay requires the exact catalog to resolve its selected model root",
            )
        entry = entries.get(item.asset_id)
        if entry is None:
            raise RuntimeAssetSnapshotError("catalog_entry_missing")
        model = next((value for value in entry.models if value.model_id == item.model_id), None)
        if model is None:
            raise RuntimeAssetSnapshotError("catalog_model_missing")
        entry_root = _absolute_catalog_path(entry.asset_path, label="catalog asset_path")
        model_root = _absolute_catalog_path(model.model_path, label="catalog model_path")
        relative = _relative_inside(entry_root, model_root, label="catalog model_path")
        urdf_path = _absolute_catalog_path(model.urdf_path, label="catalog mobility URDF")
        urdf_relative = _relative_inside(entry_root, urdf_path, label="catalog mobility URDF")
        expected_urdf = relative / "mobility.urdf"
        if urdf_relative != expected_urdf:
            raise RuntimeAssetSnapshotError(
                "runtime_urdf_unsupported",
                "RoboTwin replay requires the selected model root to contain mobility.urdf",
            )
        staged_model_root = asset_root.joinpath(*relative.parts)
        try:
            model_stat = staged_model_root.lstat()
        except OSError as error:
            raise RuntimeAssetSnapshotError("catalog_model_root_missing") from error
        if stat.S_ISLNK(model_stat.st_mode) or not stat.S_ISDIR(model_stat.st_mode):
            raise RuntimeAssetSnapshotError("catalog_model_root_missing")
        try:
            urdf_stat = (staged_model_root / "mobility.urdf").lstat()
        except OSError as error:
            raise RuntimeAssetSnapshotError("runtime_urdf_unsupported") from error
        if stat.S_ISLNK(urdf_stat.st_mode) or not stat.S_ISREG(urdf_stat.st_mode):
            raise RuntimeAssetSnapshotError("runtime_urdf_unsupported")
        result[item.object_id] = staged_model_root
    return result


def _absolute_catalog_path(value: str | None, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeAssetSnapshotError("catalog_path_missing", f"{label} is missing")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeAssetSnapshotError("catalog_path_unsafe", f"{label} is not canonical")
    return path


def _relative_inside(root: Path, path: Path, *, label: str) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as error:
        raise RuntimeAssetSnapshotError(
            "catalog_path_escape",
            f"{label} is outside the selected asset root",
        ) from error


def _open_directory_beneath(root: Path, relative: Path, *, label: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    absolute_root = root.expanduser().absolute()
    try:
        current = os.open(absolute_root.anchor, flags)
    except OSError as error:
        raise RuntimeAssetSnapshotError(
            "directory_unavailable", f"{label} is unavailable"
        ) from error
    try:
        root_parts = absolute_root.relative_to(absolute_root.anchor).parts
        for part in (*root_parts, *relative.parts):
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise RuntimeAssetSnapshotError(
                    "directory_unavailable",
                    f"{label} is missing, unreadable, or contains a symlink",
                ) from error
            os.close(current)
            current = child
        directory_stat = os.fstat(current)
        if directory_stat.st_mode & 0o500 != 0o500:
            raise RuntimeAssetSnapshotError("directory_permission", f"{label} is not readable")
        return current
    except BaseException:
        os.close(current)
        raise


def _snapshot_tree_to_cas(
    root_fd: int,
    *,
    artifact_store: LocalArtifactStore,
    label: str,
    budget: _SnapshotBudget,
) -> tuple[list[dict[str, Any]], tuple[ArtifactRef, ...], frozenset[str]]:
    files: list[dict[str, Any]] = []
    members: list[ArtifactRef] = []
    directories: set[str] = {"."}

    def visit(directory_fd: int, prefix: PurePosixPath) -> None:
        budget.admit_directory()
        before = os.fstat(directory_fd)
        if before.st_mode & 0o500 != 0o500:
            raise RuntimeAssetSnapshotError("directory_permission", f"{label} is unreadable")
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise RuntimeAssetSnapshotError(
                "directory_unreadable", f"{label} is unreadable"
            ) from error
        for name in names:
            logical = prefix / name
            logical_text = logical.as_posix()
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                raise RuntimeAssetSnapshotError(
                    "tree_race", f"{label} changed during snapshot"
                ) from error
            if stat.S_ISLNK(entry_stat.st_mode):
                raise RuntimeAssetSnapshotError(
                    "tree_symlink",
                    f"{label} contains a symlink: {logical_text}",
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                try:
                    child_fd = os.open(name, flags, dir_fd=directory_fd)
                except OSError as error:
                    raise RuntimeAssetSnapshotError(
                        "tree_race",
                        f"{label} directory changed during snapshot: {logical_text}",
                    ) from error
                directories.add(logical_text)
                try:
                    visit(child_fd, logical)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise RuntimeAssetSnapshotError(
                    "tree_nonregular",
                    f"{label} contains a non-regular entry: {logical_text}",
                )
            flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError as error:
                raise RuntimeAssetSnapshotError(
                    "file_unreadable",
                    f"{label} file is unreadable: {logical_text}",
                ) from error
            try:
                member = _copy_source_fd_to_cas(
                    artifact_store,
                    file_fd,
                    label=f"{label}/{logical_text}",
                    budget=budget,
                )
            finally:
                os.close(file_fd)
            files.append(
                {
                    "path": logical_text,
                    "sha256": member.sha256,
                    "bytes": member.bytes,
                }
            )
            members.append(member)
        after = os.fstat(directory_fd)
        if _directory_identity(before) != _directory_identity(after):
            raise RuntimeAssetSnapshotError("tree_race", f"{label} changed during snapshot")

    visit(root_fd, PurePosixPath())
    files.sort(key=lambda item: item["path"])
    return files, tuple(members), frozenset(directories)


def _copy_source_fd_to_cas(
    store: LocalArtifactStore,
    source_fd: int,
    *,
    label: str,
    budget: _SnapshotBudget,
) -> ArtifactRef:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeAssetSnapshotError("source_nonregular", f"{label} is not regular")
    if before.st_mode & 0o444 == 0:
        raise RuntimeAssetSnapshotError("source_permission", f"{label} is not readable")
    budget.admit_file(before.st_size)
    digest = hashlib.sha256()
    size = 0
    with _open_cas_directory(store, ".incoming") as incoming_fd:
        temporary_fd, temporary_name = _create_cas_temporary(
            incoming_fd,
            prefix=".runtime-asset.",
        )
        try:
            with os.fdopen(os.dup(temporary_fd), "wb") as target:
                while True:
                    chunk = os.read(source_fd, _READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    if size > before.st_size:
                        raise RuntimeAssetSnapshotError(
                            "source_race", f"{label} grew while entering CAS"
                        )
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(source_fd)
            if _file_identity(before) != _file_identity(after) or size != before.st_size:
                raise RuntimeAssetSnapshotError(
                    "source_race", f"{label} changed while entering CAS"
                )
            sha256 = digest.hexdigest()
            with _open_cas_directory(store, "sha256", sha256[:2]) as shard_fd:
                existing = _read_existing_cas_object(
                    shard_fd,
                    sha256,
                    expected_bytes=size,
                )
                if existing is not None:
                    if hashlib.sha256(existing).hexdigest() != sha256:
                        raise RuntimeAssetSnapshotError("cas_object_corrupt")
                else:
                    _install_cas_temporary(
                        incoming_fd=incoming_fd,
                        temporary_fd=temporary_fd,
                        temporary_name=temporary_name,
                        shard_fd=shard_fd,
                        digest=sha256,
                    )
            return ArtifactRef(
                name=f"runtime_asset_{sha256[:16]}",
                uri=f"artifact://sha256/{sha256}",
                media_type="application/octet-stream",
                sha256=sha256,
                bytes=size,
                schema_version=None,
            )
        finally:
            os.close(temporary_fd)
            _unlink_cas_temporary(incoming_fd, temporary_name)


def _put_bytes_in_cas(
    store: LocalArtifactStore,
    payload: bytes,
    *,
    name: str,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    sha256 = hashlib.sha256(payload).hexdigest()
    with _open_cas_directory(store, "sha256", sha256[:2]) as shard_fd:
        existing = _read_existing_cas_object(
            shard_fd,
            sha256,
            expected_bytes=len(payload),
        )
        if existing is not None:
            if existing != payload:
                raise RuntimeAssetSnapshotError("cas_object_corrupt")
        else:
            with _open_cas_directory(store, ".incoming") as incoming_fd:
                temporary_fd, temporary_name = _create_cas_temporary(
                    incoming_fd,
                    prefix=".runtime-manifest.",
                )
                try:
                    with os.fdopen(os.dup(temporary_fd), "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    _install_cas_temporary(
                        incoming_fd=incoming_fd,
                        temporary_fd=temporary_fd,
                        temporary_name=temporary_name,
                        shard_fd=shard_fd,
                        digest=sha256,
                    )
                finally:
                    os.close(temporary_fd)
                    _unlink_cas_temporary(incoming_fd, temporary_name)
    return ArtifactRef(
        name=name,
        uri=f"artifact://sha256/{sha256}",
        media_type=media_type,
        sha256=sha256,
        bytes=len(payload),
        schema_version=schema_version,
    )


def _cas_root(store: LocalArtifactStore) -> Path:
    root = store.root.expanduser().absolute()
    try:
        root.mkdir(parents=True)
    except FileExistsError:
        pass
    except OSError as error:
        raise RuntimeAssetSnapshotError("cas_root_unsafe") from error
    try:
        root_stat = root.lstat()
    except OSError as error:
        raise RuntimeAssetSnapshotError("cas_root_unsafe") from error
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise RuntimeAssetSnapshotError("cas_root_unsafe")
    return root


@contextmanager
def _open_cas_directory(store: LocalArtifactStore, *parts: str):
    """Hold one verified CAS directory descriptor for an entire operation."""

    root = _cas_root(store)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = _open_directory_beneath(root, Path(), label="CAS root")
    current = root_fd
    try:
        for part in parts:
            if _safe_basename(part) != part:
                raise RuntimeAssetSnapshotError("cas_layout_unsafe")
            try:
                os.mkdir(part, mode=0o755, dir_fd=current)
            except FileExistsError:
                pass
            except OSError as error:
                raise RuntimeAssetSnapshotError("cas_layout_unsafe") from error
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise RuntimeAssetSnapshotError("cas_layout_unsafe") from error
            os.close(current)
            current = child
        yield current
    finally:
        os.close(current)


def _create_cas_temporary(directory_fd: int, *, prefix: str) -> tuple[int, str]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(128):
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
        except OSError as error:
            raise RuntimeAssetSnapshotError("cas_layout_unsafe") from error
    raise RuntimeAssetSnapshotError("cas_layout_unsafe")


def _install_cas_temporary(
    *,
    incoming_fd: int,
    temporary_fd: int,
    temporary_name: str,
    shard_fd: int,
    digest: str,
) -> None:
    try:
        os.fchmod(temporary_fd, 0o444)
        os.replace(
            temporary_name,
            digest,
            src_dir_fd=incoming_fd,
            dst_dir_fd=shard_fd,
        )
        installed = os.stat(digest, dir_fd=shard_fd, follow_symlinks=False)
        if _file_identity(os.fstat(temporary_fd)) != _file_identity(installed):
            raise RuntimeAssetSnapshotError("cas_write_race")
        os.fsync(shard_fd)
        os.fsync(incoming_fd)
    except OSError as error:
        raise RuntimeAssetSnapshotError("cas_write_failed") from error


def _unlink_cas_temporary(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RuntimeAssetSnapshotError("cas_cleanup_failed") from error


def _open_cas_object(directory_fd: int, name: str, *, missing_reason: str) -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise RuntimeAssetSnapshotError(missing_reason) from error


def _read_existing_cas_object(
    directory_fd: int,
    name: str,
    *,
    expected_bytes: int,
) -> bytes | None:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeAssetSnapshotError("cas_object_corrupt") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_bytes:
            raise RuntimeAssetSnapshotError("cas_object_corrupt")
        payload = bytearray()
        while True:
            remaining = expected_bytes - len(payload)
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > expected_bytes:
                raise RuntimeAssetSnapshotError("cas_object_corrupt")
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after) or len(payload) != expected_bytes:
            raise RuntimeAssetSnapshotError("cas_object_corrupt")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _cas_ref_digest(ref: ArtifactRef) -> str:
    parsed = urlparse(ref.uri)
    if parsed.scheme != "artifact" or parsed.netloc != "sha256":
        raise RuntimeAssetSnapshotError("artifact_uri_unsupported")
    digest = parsed.path.removeprefix("/")
    if digest != ref.sha256 or _SHA256.fullmatch(digest) is None:
        raise RuntimeAssetSnapshotError("artifact_uri_digest_mismatch")
    return digest


def _read_open_cas_ref(descriptor: int, ref: ArtifactRef) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeAssetSnapshotError("file_nonregular", "CAS object is not regular")
    if before.st_size != ref.bytes:
        raise RuntimeAssetSnapshotError("artifact_bytes_mismatch")
    payload = bytearray()
    while True:
        remaining = ref.bytes - len(payload)
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > ref.bytes:
            raise RuntimeAssetSnapshotError("artifact_bytes_mismatch")
    after = os.fstat(descriptor)
    if _file_identity(before) != _file_identity(after):
        raise RuntimeAssetSnapshotError("file_race", "CAS object changed while reading")
    result = bytes(payload)
    if len(result) != ref.bytes:
        raise RuntimeAssetSnapshotError("artifact_bytes_mismatch")
    if hashlib.sha256(result).hexdigest() != ref.sha256:
        raise RuntimeAssetSnapshotError("artifact_digest_mismatch")
    return result


def _read_cas_ref(
    store: LocalArtifactStore,
    ref: ArtifactRef,
    *,
    max_bytes: int | None = None,
) -> bytes:
    digest = _cas_ref_digest(ref)
    if max_bytes is not None and ref.bytes > max_bytes:
        raise RuntimeAssetSnapshotError("file_too_large", "CAS object is too large")
    with _open_cas_directory(store, "sha256", digest[:2]) as shard_fd:
        descriptor = _open_cas_object(shard_fd, digest, missing_reason="file_unavailable")
        try:
            payload = _read_open_cas_ref(descriptor, ref)
        finally:
            os.close(descriptor)
    return payload


def _copy_cas_ref(store: LocalArtifactStore, ref: ArtifactRef, destination: Path) -> None:
    digest = _cas_ref_digest(ref)
    with _open_cas_directory(store, "sha256", digest[:2]) as shard_fd:
        source_fd = _open_cas_object(shard_fd, digest, missing_reason="artifact_unavailable")
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAssetSnapshotError("artifact_nonregular")
        if before.st_size != ref.bytes:
            raise RuntimeAssetSnapshotError("artifact_identity_mismatch")
        with destination.open("xb") as target:
            while True:
                remaining = ref.bytes - size
                chunk = os.read(source_fd, min(_READ_CHUNK_BYTES, remaining + 1))
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if size > ref.bytes:
                    raise RuntimeAssetSnapshotError("artifact_identity_mismatch")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
    if _file_identity(before) != _file_identity(after):
        raise RuntimeAssetSnapshotError("artifact_race")
    if size != ref.bytes or digest.hexdigest() != ref.sha256:
        raise RuntimeAssetSnapshotError("artifact_identity_mismatch")


def _read_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int | None = None,
) -> bytes:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeAssetSnapshotError(
            "file_unavailable",
            f"{label} is missing, unreadable, or a symlink",
        ) from error
    payload = bytearray()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeAssetSnapshotError("file_nonregular", f"{label} is not regular")
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            payload.extend(chunk)
            if max_bytes is not None and len(payload) > max_bytes:
                raise RuntimeAssetSnapshotError("file_too_large", f"{label} is too large")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _file_identity(before) != _file_identity(after):
        raise RuntimeAssetSnapshotError("file_race", f"{label} changed while reading")
    return bytes(payload)


def _parse_manifest(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeAssetSnapshotError(
            "manifest_invalid", "runtime asset manifest is invalid JSON"
        ) from error
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        raise RuntimeAssetSnapshotError("manifest_shape_invalid")
    if value["schema_version"] != RUNTIME_ASSET_SNAPSHOT_SCHEMA:
        raise RuntimeAssetSnapshotError("manifest_schema_unsupported")
    for field in ("resolved_scene_sha256", "asset_catalog_sha256"):
        if not isinstance(value[field], str) or _SHA256.fullmatch(value[field]) is None:
            raise RuntimeAssetSnapshotError("manifest_digest_invalid")
    assets = value["assets"]
    if not isinstance(assets, list) or not assets:
        raise RuntimeAssetSnapshotError("manifest_assets_invalid")
    if len(assets) > RUNTIME_ASSET_MAX_ASSETS:
        raise RuntimeAssetSnapshotError("asset_count_limit")
    total_directories = 0
    total_files = 0
    total_bytes = 0
    previous_asset: str | None = None
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != _ASSET_FIELDS:
            raise RuntimeAssetSnapshotError("manifest_asset_shape_invalid")
        asset_id = asset["asset_id"]
        if not isinstance(asset_id, str) or not asset_id or _safe_basename(asset_id) != asset_id:
            raise RuntimeAssetSnapshotError("manifest_asset_id_invalid")
        if previous_asset is not None and asset_id <= previous_asset:
            raise RuntimeAssetSnapshotError("manifest_assets_not_unique_sorted")
        previous_asset = asset_id
        model_ids = asset["selected_model_ids"]
        if (
            not isinstance(model_ids, list)
            or not model_ids
            or any(type(item) is not int or item < 0 for item in model_ids)
            or model_ids != sorted(set(model_ids))
        ):
            raise RuntimeAssetSnapshotError("manifest_model_ids_invalid")
        directories = asset["directories"]
        if (
            not isinstance(directories, list)
            or not directories
            or directories[0] != "."
            or directories != sorted(set(directories))
        ):
            raise RuntimeAssetSnapshotError("manifest_directories_invalid")
        total_directories += len(directories)
        if total_directories > RUNTIME_ASSET_MAX_DIRECTORIES:
            raise RuntimeAssetSnapshotError("directory_count_limit")
        directory_set = {"."}
        for directory in directories[1:]:
            relative_directory = _safe_relative(
                directory,
                label="runtime asset manifest directory",
            )
            logical_directory = relative_directory.as_posix()
            if logical_directory != directory:
                raise RuntimeAssetSnapshotError("manifest_directory_invalid")
            if relative_directory.parent.as_posix() not in directory_set:
                raise RuntimeAssetSnapshotError("manifest_directories_not_closed")
            directory_set.add(logical_directory)
        files = asset["files"]
        if not isinstance(files, list) or not files:
            raise RuntimeAssetSnapshotError("manifest_files_invalid")
        total_files += len(files)
        if total_files > RUNTIME_ASSET_MAX_FILES:
            raise RuntimeAssetSnapshotError("file_count_limit")
        previous_path: str | None = None
        for record in files:
            if not isinstance(record, dict) or set(record) != _FILE_FIELDS:
                raise RuntimeAssetSnapshotError("manifest_file_shape_invalid")
            relative = _safe_relative(record["path"], label="runtime asset manifest path")
            logical = relative.as_posix()
            if logical != record["path"]:
                raise RuntimeAssetSnapshotError("manifest_path_invalid")
            if previous_path is not None and logical <= previous_path:
                raise RuntimeAssetSnapshotError("manifest_files_not_unique_sorted")
            previous_path = logical
            if logical in directory_set:
                raise RuntimeAssetSnapshotError("manifest_tree_path_collision")
            if relative.parent.as_posix() not in directory_set:
                raise RuntimeAssetSnapshotError("manifest_file_parent_missing")
            if not isinstance(record["sha256"], str) or _SHA256.fullmatch(record["sha256"]) is None:
                raise RuntimeAssetSnapshotError("manifest_file_digest_invalid")
            if type(record["bytes"]) is not int or record["bytes"] < 0:
                raise RuntimeAssetSnapshotError("manifest_file_bytes_invalid")
            if record["bytes"] > RUNTIME_ASSET_MAX_SINGLE_FILE_BYTES:
                raise RuntimeAssetSnapshotError("single_file_bytes_limit")
            total_bytes += record["bytes"]
            if total_bytes > RUNTIME_ASSET_MAX_TOTAL_BYTES:
                raise RuntimeAssetSnapshotError("total_bytes_limit")
        if type(asset["file_count"]) is not int or asset["file_count"] != len(files):
            raise RuntimeAssetSnapshotError("manifest_file_count_invalid")
        required_files = asset["required_files"]
        if (
            not isinstance(required_files, list)
            or not required_files
            or required_files != sorted(set(required_files))
            or any(value not in {record["path"] for record in files} for value in required_files)
        ):
            raise RuntimeAssetSnapshotError("manifest_required_files_invalid")
        if type(asset["bytes"]) is not int or asset["bytes"] != sum(
            item["bytes"] for item in files
        ):
            raise RuntimeAssetSnapshotError("manifest_asset_bytes_invalid")
        if not isinstance(asset["tree_sha256"], str) or asset["tree_sha256"] != _tree_sha256(
            directories,
            files,
        ):
            raise RuntimeAssetSnapshotError("manifest_tree_digest_invalid")
    if payload != canonical_runtime_asset_manifest_bytes(value):
        raise RuntimeAssetSnapshotError("manifest_not_canonical")
    return value


def _verify_root_against_manifest(root: Path, document: dict[str, Any]) -> None:
    root_fd = _open_directory_beneath(root, Path(), label="runtime asset root")
    try:
        try:
            actual_names = sorted(os.listdir(root_fd))
        except OSError as error:
            raise RuntimeAssetSnapshotError("runtime_asset_root_unreadable") from error
        expected_names = [asset["asset_id"] for asset in document["assets"]]
        if actual_names != expected_names:
            raise RuntimeAssetSnapshotError(
                "runtime_asset_root_unexpected",
                "runtime asset root contains missing or unexpected entries",
            )
        for asset in document["assets"]:
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                asset_fd = os.open(asset["asset_id"], flags, dir_fd=root_fd)
            except OSError as error:
                raise RuntimeAssetSnapshotError(
                    "runtime_asset_tree_unsafe",
                    "runtime asset root contains a missing entry or symlink",
                ) from error
            try:
                _verify_asset_tree(asset_fd, asset)
            except OSError as error:
                raise RuntimeAssetSnapshotError("runtime_asset_tree_race") from error
            finally:
                os.close(asset_fd)
    finally:
        os.close(root_fd)


def _verify_asset_tree(root_fd: int, asset: dict[str, Any]) -> None:
    expected_directories = set(asset["directories"])
    expected_files = {record["path"]: record for record in asset["files"]}
    children: dict[str, set[str]] = {directory: set() for directory in expected_directories}
    for directory in expected_directories - {"."}:
        relative = PurePosixPath(directory)
        children[relative.parent.as_posix()].add(relative.name)
    for logical in expected_files:
        relative = PurePosixPath(logical)
        children[relative.parent.as_posix()].add(relative.name)

    def visit(directory_fd: int, prefix: PurePosixPath) -> None:
        before = os.fstat(directory_fd)
        names = sorted(os.listdir(directory_fd))
        logical_directory = prefix.as_posix() if prefix.parts else "."
        if names != sorted(children[logical_directory]):
            raise RuntimeAssetSnapshotError(
                "runtime_asset_tree_drift",
                f"runtime asset tree differs from its manifest: {asset['asset_id']}",
            )
        for name in names:
            logical = prefix / name
            logical_text = logical.as_posix()
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise RuntimeAssetSnapshotError(
                    "runtime_asset_tree_symlink",
                    f"runtime asset tree contains a symlink: {asset['asset_id']}/{logical_text}",
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                if logical_text not in expected_directories:
                    raise _runtime_tree_drift(asset["asset_id"])
                flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                child_fd = os.open(name, flags, dir_fd=directory_fd)
                try:
                    visit(child_fd, logical)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise RuntimeAssetSnapshotError("runtime_asset_tree_nonregular")
            expected = expected_files.get(logical_text)
            if expected is None or entry_stat.st_size != expected["bytes"]:
                raise _runtime_tree_drift(asset["asset_id"])
            flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                payload_sha256, size = _hash_open_file(
                    file_fd,
                    label=f"{asset['asset_id']}/{logical_text}",
                    expected_size=expected["bytes"],
                )
            finally:
                os.close(file_fd)
            if payload_sha256 != expected["sha256"] or size != expected["bytes"]:
                raise _runtime_tree_drift(asset["asset_id"])
        after = os.fstat(directory_fd)
        if _directory_identity(before) != _directory_identity(after):
            raise RuntimeAssetSnapshotError("runtime_asset_tree_race")

    visit(root_fd, PurePosixPath())


def _hash_open_file(descriptor: int, *, label: str, expected_size: int) -> tuple[str, int]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeAssetSnapshotError("runtime_asset_file_nonregular")
    if before.st_size != expected_size:
        raise RuntimeAssetSnapshotError(
            "runtime_asset_tree_drift",
            f"runtime asset tree differs from its manifest: {label}",
        )
    digest = hashlib.sha256()
    size = 0
    while True:
        remaining = expected_size - size
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        if size > expected_size:
            raise RuntimeAssetSnapshotError(
                "runtime_asset_tree_drift",
                f"runtime asset tree differs from its manifest: {label}",
            )
    after = os.fstat(descriptor)
    if _file_identity(before) != _file_identity(after) or size != before.st_size:
        raise RuntimeAssetSnapshotError(
            "runtime_asset_file_race",
            f"runtime asset file changed while hashing: {label}",
        )
    return digest.hexdigest(), size


def _runtime_tree_drift(asset_id: str) -> RuntimeAssetSnapshotError:
    return RuntimeAssetSnapshotError(
        "runtime_asset_tree_drift",
        f"runtime asset tree differs from its manifest: {asset_id}",
    )


def _resolved_selection(resolved: ResolvedSceneSpec) -> dict[str, tuple[int, ...]]:
    grouped: dict[str, set[int]] = {}
    for item in resolved.objects:
        grouped.setdefault(item.asset_id, set()).add(item.model_id)
    return {asset_id: tuple(sorted(model_ids)) for asset_id, model_ids in sorted(grouped.items())}


def _real_directory(path: Path, *, label: str) -> Path:
    normalized = path.expanduser().absolute()
    try:
        path_stat = normalized.lstat()
    except OSError as error:
        raise RuntimeAssetSnapshotError(
            "directory_unavailable", f"{label} is unavailable"
        ) from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeAssetSnapshotError("directory_unsafe", f"{label} must be a real directory")
    return normalized


def _safe_relative(value: Any, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeAssetSnapshotError("manifest_path_invalid", f"{label} is invalid")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise RuntimeAssetSnapshotError("manifest_path_escape", f"{label} escapes its root")
    return posix


def _safe_basename(value: str) -> str:
    if "\\" in value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        return ""
    return path.name


def _tree_sha256(directories: list[str], files: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        canonical_runtime_asset_manifest_bytes(
            {
                "directories": directories,
                "files": files,
            }
        )
    ).hexdigest()


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _unique_artifacts(values: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
    result: list[ArtifactRef] = []
    seen: set[tuple[str, int]] = set()
    for value in values:
        identity = (value.sha256, value.bytes)
        if identity not in seen:
            seen.add(identity)
            result.append(value)
    return tuple(result)


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _make_tree_owner_writable(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for path in root.rglob("*"):
        if not path.is_symlink():
            try:
                path.chmod(0o700 if path.is_dir() else 0o600)
            except OSError:
                pass
    try:
        root.chmod(0o700)
    except OSError:
        pass
