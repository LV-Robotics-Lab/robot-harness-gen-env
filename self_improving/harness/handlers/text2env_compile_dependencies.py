"""Parameter-aware dependency receipts for ``text2env.compile`` invocations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from scene_gen.catalog import AssetCatalog, load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.schema import SceneSpecError
from scene_gen.solver import SceneSolveError, solve_scene

from ..artifacts import ArtifactResolutionError, LocalArtifactStore
from ..registry import DependencyResolutionError
from ..schemas import DependencyRef, Text2EnvCompileInput
from ..schemas.base import HarnessModel
from .text2env_compile import Text2EnvCompileHandler, _validate_catalog_trust


@dataclass(frozen=True)
class Text2EnvCompileDependencyResolver:
    """Bind source, trust config, selected assets, and mutable library state."""

    artifact_store: LocalArtifactStore
    handler: Text2EnvCompileHandler
    scene_gen_root: Path
    ledger_contract_root: Path

    def resolve(
        self,
        skill_ref: str,
        effective_parameters: HarnessModel,
    ) -> tuple[DependencyRef, ...]:
        if skill_ref != "text2env.compile@1.0.0":
            return ()
        if not isinstance(effective_parameters, Text2EnvCompileInput):
            raise DependencyResolutionError(
                "text2env.compile dependencies require Text2EnvCompileInput"
            )
        try:
            catalog_path = self.artifact_store.resolve(
                effective_parameters.asset_catalog
            ).path
            catalog = load_catalog(catalog_path)
            _validate_catalog_trust(
                catalog,
                allowed_roots=self.handler.allowed_asset_roots,
            )
            selected_assets = _selected_asset_digest(
                effective_parameters,
                catalog,
            )
            dependencies = (
                DependencyRef(
                    name="asset-library-state",
                    version="1",
                    sha256=_tree_digest(
                        self.handler.asset_library_root.expanduser().resolve()
                        / "generated",
                        allow_missing=True,
                    ),
                ),
                DependencyRef(
                    name="catalog-selected-assets",
                    version="1",
                    sha256=selected_assets,
                ),
                DependencyRef(
                    name="ledger-contract",
                    version="1",
                    sha256=_tree_digest(
                        self.ledger_contract_root,
                        allow_missing=False,
                    ),
                ),
                DependencyRef(
                    name="scene-gen",
                    version="0.1.0",
                    sha256=_tree_digest(self.scene_gen_root, allow_missing=False),
                ),
                DependencyRef(
                    name="text2env-compile-config",
                    version="1",
                    sha256=_config_digest(self.handler),
                ),
            )
        except (
            ArtifactResolutionError,
            OSError,
            ValueError,
        ) as error:
            raise DependencyResolutionError(
                f"text2env.compile dependency resolution failed: {error}"
            ) from error
        return tuple(sorted(dependencies, key=lambda item: item.name))


def _selected_asset_digest(
    parameters: Text2EnvCompileInput,
    catalog: AssetCatalog,
) -> str:
    try:
        spec = parse_rule_based(parameters.request, seed=parameters.seed)
        resolved = solve_scene(spec, catalog)
    except (SceneSpecError, SceneSolveError, ValidationError):
        return _canonical_digest({"selected_assets": []})
    selected_ids = sorted({item.asset_id for item in resolved.objects})
    entries = {entry.asset_id: entry for entry in catalog.entries}
    snapshots = [
        {
            "asset_id": asset_id,
            "tree_sha256": _tree_digest(
                Path(entries[asset_id].asset_path),
                allow_missing=False,
            ),
        }
        for asset_id in selected_ids
    ]
    return _canonical_digest({"selected_assets": snapshots})


def _config_digest(handler: Text2EnvCompileHandler) -> str:
    return _canonical_digest(
        {
            "admission_date": handler.admission_date.isoformat(),
            "allowed_asset_roots": sorted(
                str(path.expanduser().resolve()) for path in handler.allowed_asset_roots
            ),
            "asset_library_root": str(handler.asset_library_root.expanduser().resolve()),
            "generated_staging_root": str(
                handler.generated_staging_root.expanduser().resolve()
            ),
        }
    )


def _tree_digest(root: Path, *, allow_missing: bool) -> str:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.exists():
        if allow_missing:
            return _canonical_digest({"exists": False, "files": []})
        raise DependencyResolutionError(f"dependency root is missing: {resolved_root}")
    if not resolved_root.is_dir():
        raise DependencyResolutionError(f"dependency root is not a directory: {resolved_root}")
    records: list[dict[str, Any]] = []
    for candidate in sorted(resolved_root.rglob("*"), key=lambda path: path.as_posix()):
        relative = candidate.relative_to(resolved_root)
        if "__pycache__" in relative.parts or candidate.suffix in {".pyc", ".pyo"}:
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise DependencyResolutionError(f"dependency symlink escapes root: {candidate}")
        if not resolved.is_file():
            continue
        records.append(
            {
                "path": relative.as_posix(),
                "bytes": resolved.stat().st_size,
                "sha256": _file_sha256(resolved),
            }
        )
    return _canonical_digest({"exists": True, "files": records})


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
