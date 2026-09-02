"""Promote one verified System 2 replay package closure between local CAS roots.

The module copies only the replay package closure: the canonical environment
package record, its effective catalog, its manifest, and the manifest's exact
members.  Request provenance is intentionally not copied because its own
compile-evidence closure is outside this seam.  Catalog contents may still
refer to external asset roots, so this is not runtime-asset portability.
Promotion prepares no replay, validation, physical-success, or publication
decision.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scene_gen.catalog import AssetCatalog
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.package_store import (
    PackageStore,
    PackageStoreError,
    PublishedPackage,
)
from self_improving.harness.schemas import ArtifactRef, EnvironmentPackage, Text2EnvReplayInput
from self_improving.system2_compile_turn import System2CompileTurnResult
from self_improving.system2_replay_handoff import (
    PreparedSystem2Replay,
    System2ReplayHandoff,
    System2ReplayHandoffError,
)


def _canonical_json_bytes(value: object) -> bytes:
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


@dataclass(frozen=True, slots=True)
class PromotedSystem2ReplayInput:
    """Replay input whose package closure is verified in the destination CAS."""

    replay_input: Text2EnvReplayInput
    environment_package_ref: ArtifactRef
    destination_artifact_root: Path


class System2ReplayInputPromotionError(RuntimeError):
    """The replay package closure could not be promoted safely."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"System 2 replay-input promotion stopped: {reason}")


class System2ReplayInputPromoter:
    """Deep module for verified, additive promotion between distinct local CAS roots."""

    def __init__(
        self,
        *,
        source_artifact_store: LocalArtifactStore,
        replay_artifact_store: LocalArtifactStore,
        scratch_root: Path,
    ) -> None:
        if type(source_artifact_store) is not LocalArtifactStore:
            raise TypeError("source_artifact_store must be an exact LocalArtifactStore")
        if type(replay_artifact_store) is not LocalArtifactStore:
            raise TypeError("replay_artifact_store must be an exact LocalArtifactStore")
        if not isinstance(scratch_root, Path):
            raise TypeError("scratch_root must be a Path")
        self._source_handle = source_artifact_store
        self._replay_handle = replay_artifact_store
        self._source_root = source_artifact_store.root
        self._replay_root = replay_artifact_store.root
        self._source = LocalArtifactStore(self._source_root)
        self._replay = LocalArtifactStore(self._replay_root)
        self._scratch_root = scratch_root.expanduser().absolute()
        self._validate_artifact_roots()
        self._validate_scratch_root()

    def promote(
        self,
        *,
        compile_result: System2CompileTurnResult,
        prepared: PreparedSystem2Replay,
    ) -> PromotedSystem2ReplayInput:
        """Verify at source, copy the exact package closure, and verify at destination."""

        self._validate_artifact_roots()
        self._validate_scratch_root()
        if type(prepared) is not PreparedSystem2Replay:
            raise System2ReplayInputPromotionError(reason="prepared_invalid")

        try:
            with tempfile.TemporaryDirectory(
                prefix=".system2-replay-input-promotion-",
                dir=self._scratch_root,
            ) as temporary:
                work_root = Path(temporary)
                verified = self._verify_source_authority(
                    compile_result=compile_result,
                    prepared=prepared,
                    scratch_root=work_root / "handoff",
                )
                self._validate_artifact_roots()
                staged = self._stage_source_closure(verified, work_root=work_root)
                self._validate_artifact_roots()
                promoted = self._write_destination_closure(staged)
                self._verify_destination_closure(promoted, staged, work_root=work_root)
                self._validate_artifact_roots()
                return PromotedSystem2ReplayInput(
                    replay_input=promoted,
                    environment_package_ref=verified.environment_package_ref,
                    destination_artifact_root=self._replay_root,
                )
        except System2ReplayInputPromotionError:
            raise
        except Exception as error:
            raise System2ReplayInputPromotionError(reason="promotion_failed") from error

    def _verify_source_authority(
        self,
        *,
        compile_result: System2CompileTurnResult,
        prepared: PreparedSystem2Replay,
        scratch_root: Path,
    ) -> PreparedSystem2Replay:
        try:
            verified = System2ReplayHandoff(
                artifact_store=self._source,
                scratch_root=scratch_root,
            ).prepare(
                compile_result=compile_result,
                runtime_config=prepared.replay_input.runtime_config,
            )
        except (AttributeError, System2ReplayHandoffError) as error:
            raise System2ReplayInputPromotionError(reason="source_authority_invalid") from error
        if verified != prepared:
            raise System2ReplayInputPromotionError(reason="prepared_mismatch")
        return verified

    def _stage_source_closure(
        self,
        prepared: PreparedSystem2Replay,
        *,
        work_root: Path,
    ) -> "_StagedReplayClosure":
        package = prepared.replay_input.environment_package
        source_package_store = PackageStore(self._source)
        materialized_root = work_root / "source-package"
        try:
            source_package_store.materialize(package.package_manifest, materialized_root)
            published = source_package_store.publish(materialized_root)
            environment_bytes = self._source.resolve(
                prepared.environment_package_ref
            ).path.read_bytes()
            catalog_bytes = self._source.resolve(package.asset_catalog).path.read_bytes()
        except (OSError, ValueError, PackageStoreError) as error:
            raise System2ReplayInputPromotionError(reason="source_package_invalid") from error
        if published.manifest != package.package_manifest:
            raise System2ReplayInputPromotionError(reason="source_package_invalid")
        expected_environment = _canonical_json_bytes(package.model_dump(mode="json"))
        if environment_bytes != expected_environment:
            raise System2ReplayInputPromotionError(reason="source_package_invalid")
        try:
            catalog = AssetCatalog.model_validate_json(catalog_bytes)
        except ValueError as error:
            raise System2ReplayInputPromotionError(reason="source_package_invalid") from error
        if catalog.digest() != package.asset_catalog.sha256:
            raise System2ReplayInputPromotionError(reason="source_package_invalid")

        environment_path = work_root / "environment-package.json"
        catalog_path = work_root / "asset-catalog.json"
        environment_path.write_bytes(environment_bytes)
        catalog_path.write_bytes(catalog_bytes)
        return _StagedReplayClosure(
            prepared=prepared,
            package_root=materialized_root,
            published_package=published,
            environment_path=environment_path,
            environment_bytes=environment_bytes,
            catalog_path=catalog_path,
            catalog_bytes=catalog_bytes,
        )

    def _write_destination_closure(
        self,
        staged: "_StagedReplayClosure",
    ) -> Text2EnvReplayInput:
        package = staged.prepared.replay_input.environment_package
        try:
            self._validate_artifact_roots()
            published = PackageStore(self._replay).publish(staged.package_root)
            self._validate_artifact_roots()
            if (
                published != staged.published_package
                or published.manifest != package.package_manifest
            ):
                raise System2ReplayInputPromotionError(reason="destination_ref_mismatch")
            catalog_ref = self._replay.put_file(
                staged.catalog_path,
                name=package.asset_catalog.name,
                media_type=package.asset_catalog.media_type,
                schema_version=package.asset_catalog.schema_version,
            )
            self._validate_artifact_roots()
            if catalog_ref != package.asset_catalog:
                raise System2ReplayInputPromotionError(reason="destination_ref_mismatch")
            environment_ref = self._replay.put_file(
                staged.environment_path,
                name=staged.prepared.environment_package_ref.name,
                media_type=staged.prepared.environment_package_ref.media_type,
                schema_version=staged.prepared.environment_package_ref.schema_version,
            )
            self._validate_artifact_roots()
            if environment_ref != staged.prepared.environment_package_ref:
                raise System2ReplayInputPromotionError(reason="destination_ref_mismatch")
        except System2ReplayInputPromotionError:
            raise
        except (OSError, ValueError, PackageStoreError) as error:
            raise System2ReplayInputPromotionError(reason="destination_write_failed") from error
        return staged.prepared.replay_input

    def _verify_destination_closure(
        self,
        replay_input: Text2EnvReplayInput,
        staged: "_StagedReplayClosure",
        *,
        work_root: Path,
    ) -> None:
        package = replay_input.environment_package
        try:
            environment_bytes = self._replay.resolve(
                staged.prepared.environment_package_ref
            ).path.read_bytes()
            catalog_bytes = self._replay.resolve(package.asset_catalog).path.read_bytes()
            destination_package = PackageStore(self._replay).materialize(
                package.package_manifest,
                work_root / "destination-package",
            )
            typed_package = EnvironmentPackage.model_validate_json(environment_bytes)
            typed_catalog = AssetCatalog.model_validate_json(catalog_bytes)
            typed_replay_input = Text2EnvReplayInput(
                environment_package=typed_package,
                runtime_config=replay_input.runtime_config,
            )
        except (OSError, ValueError, PackageStoreError) as error:
            raise System2ReplayInputPromotionError(
                reason="destination_verification_failed"
            ) from error
        if (
            environment_bytes != staged.environment_bytes
            or catalog_bytes != staged.catalog_bytes
            or typed_catalog.digest() != package.asset_catalog.sha256
            or typed_replay_input != replay_input
            or not (destination_package / "package_manifest.json").is_file()
        ):
            raise System2ReplayInputPromotionError(reason="destination_verification_failed")

    def _validate_scratch_root(self) -> None:
        root = self._scratch_root
        if (
            _has_symlink_component(root)
            or _paths_overlap(root, self._source_root)
            or _paths_overlap(root, self._replay_root)
        ):
            raise System2ReplayInputPromotionError(reason="scratch_root_unsafe")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise System2ReplayInputPromotionError(reason="scratch_root_unsafe") from error
        if not root.is_dir() or root.resolve(strict=True) != root:
            raise System2ReplayInputPromotionError(reason="scratch_root_unsafe")

    def _validate_artifact_roots(self) -> None:
        try:
            source_root = self._source_handle.root.expanduser().resolve(strict=False)
            replay_root = self._replay_handle.root.expanduser().resolve(strict=False)
            frozen_source_root = self._source.root.expanduser().resolve(strict=False)
            frozen_replay_root = self._replay.root.expanduser().resolve(strict=False)
        except (AttributeError, OSError, RuntimeError) as error:
            raise System2ReplayInputPromotionError(reason="artifact_roots_changed") from error
        if _paths_overlap(source_root, replay_root) or _paths_overlap(
            frozen_source_root,
            frozen_replay_root,
        ):
            raise System2ReplayInputPromotionError(reason="artifact_roots_overlap")
        if (
            source_root != self._source_root
            or replay_root != self._replay_root
            or frozen_source_root != self._source_root
            or frozen_replay_root != self._replay_root
        ):
            raise System2ReplayInputPromotionError(reason="artifact_roots_changed")


@dataclass(frozen=True, slots=True)
class _StagedReplayClosure:
    prepared: PreparedSystem2Replay
    package_root: Path
    published_package: PublishedPackage
    environment_path: Path
    environment_bytes: bytes
    catalog_path: Path
    catalog_bytes: bytes


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _has_symlink_component(path: Path) -> bool:
    current = path
    while current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return current.is_symlink()


__all__ = [
    "PromotedSystem2ReplayInput",
    "System2ReplayInputPromoter",
    "System2ReplayInputPromotionError",
]
