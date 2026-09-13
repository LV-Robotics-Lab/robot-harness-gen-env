"""Fixed source ordering over partial entity results, not another workflow controller."""

import json
import os
import time
from pathlib import Path

from .artifacts import artifact_closure
from .assets import AssetRegistry
from .compile import ResolvedAssetSet
from .contracts import SceneIR
from .local_color_advisory import classify_color_repair
from .resolver import ResolutionResult
from .store import process_identity


class SourceRouter:
    def __init__(self, store, *, local=None, web=None, reconstruction=None):
        self.store = store
        self.local, self.web, self.reconstruction = local, web, reconstruction

    def resolve(self, scene_ir, *, allowed_sources, allow_cousin, output_root, timeout=600):
        return self._route(
            scene_ir,
            allowed_sources=allowed_sources,
            allow_cousin=allow_cousin,
            output_root=output_root,
            timeout=timeout,
        )

    def resume_after_local_repairs(
        self,
        original_resolution_ref,
        repair_result_refs,
        *,
        workflow_id,
        allowed_sources,
        allow_cousin,
        output_root,
        timeout=600,
    ):
        """Consume committed children, then visit only previously unexecuted providers."""
        from .local_color_execution import verify_color_repair_result

        started = time.monotonic()
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("invalid source constraints")
        original = ResolutionResult.model_validate_json(
            self.store.read_artifact(original_resolution_ref)
        )
        scene_ir = original.resolved.scene_ir
        snapshot = self.store.status(workflow_id)
        current = snapshot.operations[-1] if snapshot.operations else None
        owner = process_identity(os.getpid())
        if (
            snapshot.status != "active"
            or owner is None
            or snapshot.owner != owner
            or current is None
            or current.capability != "asset.resolve"
            or current.status != "running"
            or snapshot.asset_resolution != original_resolution_ref
            or scene_ir != (snapshot.scene_ir or snapshot.pending_scene_ir)
            or tuple(allowed_sources) != snapshot.request.allowed_sources
            or allow_cousin != snapshot.request.constraints.allow_cousin
        ):
            raise ValueError("color_continuation_operation_mismatch")
        origins = [
            (i, op)
            for i, op in enumerate(snapshot.operations[:-1])
            if op.capability == "asset.resolve"
            and op.status == "blocked"
            and op.result
            and original_resolution_ref in op.result.outputs
            and original.receipt in op.result.outputs
        ]
        if len(origins) != 1 or any(
            op.capability == "asset.resolve" for op in snapshot.operations[origins[0][0] + 1 : -1]
        ):
            raise ValueError("color_continuation_original_not_committed_or_consumed")
        artifact_closure(self.store, (original_resolution_ref, original.receipt))
        payload = json.loads(self.store.read_artifact(original.receipt))
        scene = SceneIR.model_validate_json(self.store.read_artifact(scene_ir))
        entities = {e.id: e for e in scene.entities if e.role == "foreground"}
        candidates = {p.entity_id: p for p in original.pending_color_repairs}
        original_assets = {a.entity_id: a for a in original.resolved.assets}
        searched = payload.get("searched_entities", {})
        sources = payload.get("sources", [])
        next_source = next((s for s in ("web", "reconstruction") if s in allowed_sources), None)
        if (
            original.status != "blocked"
            or not candidates
            or len(candidates) != len(original.pending_color_repairs)
            or len(original_assets) != len(original.resolved.assets)
            or set(candidates) & set(original_assets)
            or not set(candidates) | set(original_assets) <= set(entities)
            or payload.get("resolved") != original.resolved.model_dump(mode="json")
            or payload.get("pending_color_repairs")
            != [p.model_dump(mode="json") for p in original.pending_color_repairs]
            or payload.get("next_source") != next_source
            or original.next_source != next_source
            or not isinstance(searched, dict)
            or set(searched) != {"local"}
            or len(sources) != 1
            or sources[0].get("source") != "local"
            or not isinstance(searched["local"], list)
            or any(not isinstance(e, str) for e in searched["local"])
            or len(set(searched["local"])) != len(searched["local"])
            or not set(candidates) <= set(searched["local"]) <= set(entities)
        ):
            raise ValueError("color_continuation_original_binding_mismatch")
        registry = AssetRegistry(self.store)
        for candidate in candidates.values():
            if (
                candidate.scene_ir != scene_ir
                or classify_color_repair(
                    self.store,
                    scene_ir,
                    entities[candidate.entity_id],
                    registry.inspect(candidate.parent_version),
                    candidate.preview_proof,
                    candidate.assessment,
                )
                != candidate
            ):
                raise ValueError("color_continuation_candidate_mismatch")
        for asset in original_assets.values():
            if (
                asset.acquisition_source != "local"
                or asset.selection != "exact"
                or registry.inspect(asset.version_sha256).category
                != entities[asset.entity_id].category
            ):
                raise ValueError("color_continuation_partial_mismatch")
        audit = {
            "schema_version": "x2env.local_color_continuation.v1",
            "original_resolution": original_resolution_ref.model_dump(),
            "repair_results": [ref.model_dump() for ref in repair_result_refs],
            "workflow_id": workflow_id,
            "operation_id": current.operation_id,
        }
        checked = {}
        error = None
        try:
            if len(set(repair_result_refs)) != len(repair_result_refs):
                raise ValueError("duplicate_color_execution")
            for ref in repair_result_refs:
                candidate, result = verify_color_repair_result(
                    self.store, ref, workflow_id=workflow_id
                )
                if (
                    candidate != candidates.get(candidate.entity_id)
                    or candidate.entity_id in checked
                ):
                    raise ValueError("foreign_or_duplicate_color_execution")
                if not any(
                    op.capability == "asset.revise"
                    and op.status == "succeeded"
                    and op.result
                    and ref in op.result.outputs
                    for op in snapshot.operations[origins[0][0] + 1 : -1]
                ):
                    raise ValueError("color_execution_precedes_original")
                checked[candidate.entity_id] = result.resolved
        except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
            audit["validation_error"] = str(exc)
            error = "invalid_color_continuation"
        remaining = tuple(p for p in original.pending_color_repairs if p.entity_id not in checked)
        if remaining and error is None:
            error = "local_color_repair_pending"
        if self.store.status(workflow_id) != snapshot:
            raise ValueError("color_continuation_head_changed")
        budget = int(timeout - (time.monotonic() - started))
        if budget < 1:
            error = "resolver_timeout"
        return self._route(
            scene_ir,
            allowed_sources=allowed_sources,
            allow_cousin=allow_cousin,
            output_root=output_root,
            timeout=max(1, budget),
            _sources=()
            if error
            else tuple(s for s in ("web", "reconstruction") if s in allowed_sources),
            _assets=(*original.resolved.assets, *checked.values()),
            _records=sources,
            _pending=remaining,
            _searched=searched,
            _error=error,
            _audit=audit,
            _next_source=original.next_source if error else None,
        )

    def _route(
        self,
        scene_ir,
        *,
        allowed_sources,
        allow_cousin,
        output_root,
        timeout=600,
        _sources=("local", "web", "reconstruction"),
        _assets=(),
        _records=(),
        _pending=(),
        _searched=None,
        _error=None,
        _audit=None,
        _next_source=None,
    ):
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
        assets, records = {a.entity_id: a for a in _assets}, list(_records)
        error = _error
        pending, searched_entities, next_source = _pending, dict(_searched or {}), _next_source
        registry = AssetRegistry(self.store)
        for source in _sources:
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
            has_pending = False
            try:
                if _audit:
                    searched_entities[source] = list(missing)
                result = resolver.resolve(
                    scene_ir,
                    allowed_sources=allowed_sources,
                    allow_cousin=allow_cousin,
                    output_root=root / source,
                    timeout=remaining,
                    entity_ids=missing,
                )
                has_pending = bool(result.pending_color_repairs)
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
                if result.pending_color_repairs:
                    candidates = result.pending_color_repairs
                    payload = json.loads(self.store.read_artifact(result.receipt))
                    searched = payload.get("searched_entities")
                    if (
                        source != "local"
                        or result.status != "blocked"
                        or payload.get("pending_color_repairs")
                        != [p.model_dump(mode="json") for p in candidates]
                        or not isinstance(searched, list)
                        or any(not isinstance(e, str) for e in searched)
                        or len(set(searched)) != len(searched)
                        or not set(searched) <= set(missing)
                        or len({p.entity_id for p in candidates}) != len(candidates)
                    ):
                        raise ValueError("unbound_pending_color_repair")
                    for candidate in candidates:
                        if (
                            candidate.scene_ir != scene_ir
                            or candidate.entity_id not in searched
                            or candidate.entity_id in {a.entity_id for a in checked}
                            or classify_color_repair(
                                self.store,
                                scene_ir,
                                required[candidate.entity_id],
                                registry.inspect(candidate.parent_version),
                                candidate.preview_proof,
                                candidate.assessment,
                            )
                            != candidate
                        ):
                            raise ValueError("unbound_pending_color_repair")
                if time.monotonic() - start >= timeout:
                    raise ValueError("resolver_timeout")
                assets.update({a.entity_id: a for a in checked})
                error = result.error_code
                if result.pending_color_repairs:
                    pending = result.pending_color_repairs
                    searched_entities[source] = searched
                    next_source = next(
                        (s for s in ("web", "reconstruction") if s in allowed_sources), None
                    )
                    break
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                error = record["error_code"] = str(exc)
                if has_pending:
                    break
        missing = tuple(e for e in required if e not in assets)
        status = "blocked" if missing else "succeeded"
        error = (error or "sources_exhausted") if missing else None
        if _error == "invalid_color_continuation":
            status, error = "failed", _error
        elif _error == "resolver_timeout":
            status, error = "blocked", _error
        resolved = ResolvedAssetSet(
            scene_ir=scene_ir, assets=tuple(assets[e] for e in required if e in assets)
        )
        data = json.dumps(
            {
                **(_audit or {}),
                "status": status,
                "error_code": error,
                "resolved": resolved.model_dump(mode="json"),
                "sources": records,
                "unresolved_entities": missing,
                "pending_color_repairs": [p.model_dump(mode="json") for p in pending],
                "searched_entities": searched_entities,
                "next_source": next_source,
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
            required_resources=(error,) if error and not pending else (),
            pending_color_repairs=pending,
            next_source=next_source,
        )
