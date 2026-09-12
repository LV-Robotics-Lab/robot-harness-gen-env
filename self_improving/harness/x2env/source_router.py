"""Fixed source ordering over partial entity results, not another workflow controller."""

import json
import time
from pathlib import Path

from .artifacts import artifact_closure
from .assets import AssetRegistry
from .compile import ResolvedAssetSet
from .contracts import SceneIR
from .resolver import ResolutionResult


class SourceRouter:
    def __init__(self, store, *, local=None, web=None, reconstruction=None):
        self.store = store
        self.local, self.web, self.reconstruction = local, web, reconstruction

    def resolve(self, scene_ir, *, allowed_sources, allow_cousin, output_root, timeout=600):
        if (
            type(timeout) is not int
            or not 1 <= timeout <= 600
            or type(allow_cousin) is not bool
            or not allowed_sources
            or len(set(allowed_sources)) != len(allowed_sources)
            or any(s not in {"local", "web", "reconstruction"} for s in allowed_sources)
        ):
            raise ValueError("invalid source constraints")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("router root must be absolute new nonsymbolic directory")
        root.mkdir(parents=True, exist_ok=False)
        start = time.monotonic()
        scene = SceneIR.model_validate_json(self.store.read_artifact(scene_ir))
        required = {e.id: e for e in scene.entities if e.role == "foreground"}
        assets, records = {}, []
        error = None
        registry = AssetRegistry(self.store)
        for source in ("local", "web", "reconstruction"):
            missing = tuple(e for e in required if e not in assets)
            if not missing:
                break
            if source not in allowed_sources:
                continue
            remaining = int(timeout - (time.monotonic() - start))
            if remaining < 1:
                error = "resolver_timeout"
                break
            resolver = getattr(self, source)
            record = {"source": source, "entity_ids": missing}
            records.append(record)
            if resolver is None:
                error = record["error_code"] = "source_adapter_not_connected"
                continue
            try:
                result = resolver.resolve(
                    scene_ir,
                    allowed_sources=allowed_sources,
                    allow_cousin=allow_cousin,
                    output_root=root / source,
                    timeout=remaining,
                    entity_ids=missing,
                )
                result = ResolutionResult.model_validate_json(result.model_dump_json())
                artifact_closure(self.store, (result.receipt,))
                record["result"] = result.model_dump(mode="json")
                if result.resolved.scene_ir != scene_ir:
                    raise ValueError("source_changed_scene")
                if len({a.entity_id for a in result.resolved.assets}) != len(
                    result.resolved.assets
                ):
                    raise ValueError("duplicate_source_entity")
                checked = []
                for asset in result.resolved.assets:
                    if (
                        asset.entity_id not in missing
                        or asset.acquisition_source != source
                        or asset.selection != "exact"
                    ):
                        raise ValueError("unbound_source_asset")
                    version = registry.inspect(asset.version_sha256)
                    if version.category != required[asset.entity_id].category:
                        raise ValueError("source_category_mismatch")
                    checked.append(asset)
                if time.monotonic() - start >= timeout:
                    raise ValueError("resolver_timeout")
                assets.update({a.entity_id: a for a in checked})
                error = result.error_code
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                error = record["error_code"] = str(exc)
        missing = tuple(e for e in required if e not in assets)
        status = "blocked" if missing else "succeeded"
        error = (error or "sources_exhausted") if missing else None
        resolved = ResolvedAssetSet(
            scene_ir=scene_ir, assets=tuple(assets[e] for e in required if e in assets)
        )
        data = json.dumps(
            {
                "status": status,
                "error_code": error,
                "resolved": resolved.model_dump(mode="json"),
                "sources": records,
                "unresolved_entities": missing,
                "selection_order": "canonical_plan_section_5",
                "cousin_status": "not_implemented",
                "wall_seconds": time.monotonic() - start,
            },
            sort_keys=True,
        ).encode()
        (root / "receipt.json").write_bytes(data)
        return ResolutionResult(
            status=status,
            resolved=resolved,
            receipt=self.store.write_artifact(data, "application/json"),
            error_code=error,
            required_resources=(error,) if error else (),
        )
