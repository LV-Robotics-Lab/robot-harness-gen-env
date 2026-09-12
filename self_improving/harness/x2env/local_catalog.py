"""Bounded canonical Registry projection consumed by the original Yuxin local engine.

This is not Gujie's/Yuxin's historical asset catalog and does not import raw URDFs.
The public Registry presently indexes exact category only; no aliases are invented.
The projection is a byte-preserving copy for the provider's native ledger checks.
"""

import json
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from .adapters.yuxin import ProviderCandidate, YuxinProviderAdapter
from .artifacts import artifact_closure
from .assets import AssetVersion
from .contracts import ArtifactRef, Model


class LocalCatalogSearch(Model):
    status: Literal["succeeded", "failed", "blocked"]
    versions: tuple[AssetVersion, ...] = ()
    candidates: tuple[ProviderCandidate, ...] = ()
    receipt: ArtifactRef
    error_code: str | None = None


class RegistryLocalCatalog:
    def __init__(self, store, registry, *, max_versions=128, max_bytes=256 * 1024 * 1024):
        if (
            type(max_versions) is not int
            or not 1 <= max_versions <= 2048
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= 256 * 1024 * 1024
        ):
            raise ValueError("invalid catalog budget")
        self.store, self.registry = store, registry
        self.max_versions, self.max_bytes = max_versions, max_bytes

    def search(self, entity, *, output_root, limit=8, timeout=600):
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(timeout) is not int
            or not 1 <= timeout <= 600
        ):
            raise ValueError("invalid catalog search constraints")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("unsafe local projection directory")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()

        def check_time():
            if time.monotonic() - started >= timeout:
                raise ValueError("local_catalog_timeout")

        record = {
            "scope": "exact_category_registry_projection",
            "entity_id": entity.id,
            "category": entity.category,
            "raw_directory_import": "requires_normalization_and_registration",
            "physical_evaluated": False,
            "mapping": [],
        }
        status, error, versions, candidates = "failed", None, (), ()
        try:
            raw_versions = self.store.asset_versions(entity.category)
            if len(raw_versions) > self.max_versions:
                raise ValueError("local_catalog_version_limit")
            pending = tuple(AssetVersion.model_validate_json(raw) for raw in raw_versions)
            total = sum(
                member.artifact.size_bytes for version in pending for member in version.files
            )
            if total > self.max_bytes:
                raise ValueError("local_catalog_byte_limit")
            # Enforce the complete evidence budget before Registry.inspect or
            # materialization, not merely the mesh byte budget.
            roots = tuple(
                ref
                for version in pending
                for ref in (
                    *(member.artifact for member in version.files),
                    version.normalization_report,
                    version.license.evidence,
                    version.source.evidence,
                    version.receipt,
                )
            )
            artifact_closure(self.store, roots, max_bytes=self.max_bytes, max_refs=2048)
            record["projected_bytes"] = total
            entries, licenses, index = [], {}, {}
            for pending_version in pending:
                check_time()
                version = self.registry.inspect(pending_version.version_sha256)
                asset_root = root / "assets" / version.version_sha256
                members = []
                for member in version.files:
                    logical = PurePosixPath(member.path)
                    if logical.is_absolute() or ".." in logical.parts or "\\" in member.path:
                        raise ValueError("unsafe_asset_member")
                    path = asset_root / member.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(self.store.read_artifact(member.artifact))
                    members.append(
                        {
                            "uri": str(path),
                            "sha256": member.artifact.sha256,
                            "bytes": member.artifact.size_bytes,
                        }
                    )
                primary = next(
                    m for m in members if m["uri"] == str(asset_root / version.entrypoint)
                )
                key = version.version_sha256
                entries.append(
                    {
                        "asset_id": key,
                        "category": version.category,
                        "semantic_name": version.asset_id,
                        "aliases": [],
                        "asset_path": str(asset_root),
                        "models": [{"model_id": key, "usable": True, "urdf_path": primary["uri"]}],
                    }
                )
                ledger = {
                    "projection_kind": "canonical_registry_projection",
                    "version": version.model_dump(mode="json"),
                    "models": [
                        {
                            "model_id": key,
                            "source": {
                                "source_url": version.license.source_url,
                                "license": {
                                    "status": "declared",
                                    "spdx": version.license.spdx,
                                    "attribution": version.license.attribution,
                                },
                                "evidence": version.source.evidence.model_dump(mode="json"),
                            },
                            "representations": [
                                {
                                    "format": "urdf",
                                    "uri": primary["uri"],
                                    "sha256": primary["sha256"],
                                    "files": members,
                                }
                            ],
                        }
                    ],
                }
                ledger_path = root / f"{key}.ledger.json"
                ledger_path.write_text(json.dumps(ledger))
                licenses[key] = str(ledger_path)
                index[key] = version
                record["mapping"].append(
                    {
                        "catalog_asset_id": key,
                        "original_asset_id": version.asset_id,
                        "version_sha256": key,
                        "source": version.source.model_dump(mode="json"),
                    }
                )
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"entries": entries}))
            license_path = root / "license-index.json"
            license_path.write_text(json.dumps(licenses))
            check_time()
            adapter = YuxinProviderAdapter(
                {"providers": {"robotwin_local": {"enabled": True, "catalog": str(catalog)}}},
                self.store,
                license_path,
            )
            search = adapter.search(entity, "local", limit)
            record["search"] = search.model_dump(mode="json")
            artifact_closure(self.store, (search.receipt,), max_bytes=self.max_bytes)
            selected = []
            if search.status == "succeeded":
                for candidate in search.candidates:
                    engine = json.loads(self.store.read_artifact(candidate.engine_record))
                    key = engine["metadata"]["asset_id"]
                    if key not in index or candidate.candidate_id != f"catalog:{key}":
                        raise ValueError("unbound_local_candidate")
                    version = self.registry.inspect(key)
                    if version != index[key] or candidate.license.spdx != version.license.spdx:
                        raise ValueError("local_version_changed")
                    selected.append(version)
                versions, candidates = tuple(selected), search.candidates
            status, error = search.status, search.error_code
            check_time()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            status, error, versions, candidates = "failed", str(exc), (), ()
        record.update(status=status, error_code=error, wall_seconds=time.monotonic() - started)
        raw = json.dumps(record, sort_keys=True).encode()
        (root / "receipt.json").write_bytes(raw)
        return LocalCatalogSearch(
            status=status,
            error_code=error,
            versions=versions,
            candidates=candidates,
            receipt=self.store.write_artifact(raw, "application/json"),
        )
