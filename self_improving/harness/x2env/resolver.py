"""Bounded local-only resolution; acquisition origin and present reuse remain distinct.

The preview port is deployment-trusted and must render the supplied immutable
version. Binding its returned bytes does not independently prove rendering occurred.
Web/reconstruction and cousin selection are explicit unavailable continuation seams.
"""

import json
import math
import time
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image

from .artifacts import artifact_closure
from .asset_advisory import AssetVisualAssessment, VisualCandidate
from .asset_preview import AssetPreviewProof
from .compile import ResolvedAsset, ResolvedAssetSet
from .contracts import ArtifactRef, Model, SceneIR, Source
from .local_color_advisory import ColorRepairCandidate, classify_color_repair


class ResolutionResult(Model):
    status: Literal["succeeded", "failed", "blocked"]
    resolved: ResolvedAssetSet
    receipt: ArtifactRef
    error_code: str | None = None
    next_source: Source | None = None
    required_resources: tuple[str, ...] = ()
    pending_color_repairs: tuple[ColorRepairCandidate, ...] = ()


class LocalAssetResolver:
    def __init__(self, store, registry, backend, preview):
        self.store, self.registry, self.backend, self.preview = store, registry, backend, preview

    def resolve(
        self,
        scene_ir: ArtifactRef,
        *,
        allowed_sources: tuple[Source, ...],
        allow_cousin: bool,
        output_root: Path,
        timeout: int = 600,
        entity_ids: tuple[str, ...] | None = None,
    ):
        if (
            not allowed_sources
            or len(set(allowed_sources)) != len(allowed_sources)
            or any(source not in {"local", "web", "reconstruction"} for source in allowed_sources)
            or type(allow_cousin) is not bool
            or type(timeout) is not int
            or not 1 <= timeout <= 600
        ):
            raise ValueError("invalid resolver constraints or budget")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("resolver output must be an absolute non-symbolic new directory")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        deadline = started + timeout
        try:
            scene = SceneIR.model_validate_json(self.store.read_artifact(scene_ir))
        except (ValueError, OSError):
            raw = json.dumps(
                {
                    "status": "failed",
                    "error_code": "invalid_scene_evidence",
                    "scene_ir": scene_ir.model_dump(),
                }
            ).encode()
            (root / "receipt.json").write_bytes(raw)
            return ResolutionResult(
                status="failed",
                resolved=ResolvedAssetSet(scene_ir=scene_ir, assets=()),
                receipt=self.store.write_artifact(raw, "application/json"),
                error_code="invalid_scene_evidence",
            )
        records, assets, unresolved = [], [], []
        catalog_searches = []
        searched_entities, pending = [], []
        error, resources = None, ()
        selected = {e.id for e in scene.entities if e.role == "foreground"}
        if entity_ids is not None:
            if len(set(entity_ids)) != len(entity_ids) or not set(entity_ids) <= selected:
                raise ValueError("invalid resolver entity filter")
            selected = set(entity_ids)
        for entity in scene.entities:
            if entity.role != "foreground" or entity.id not in selected:
                continue
            if "local" not in allowed_sources:
                unresolved.append(entity.id)
                continue
            try:
                from .local_catalog import RegistryLocalCatalog

                budget = int(deadline - time.monotonic())
                if budget < 1:
                    error = "resolver_timeout"
                    unresolved.append(entity.id)
                    continue
                searched_entities.append(entity.id)
                search = RegistryLocalCatalog(self.store, self.registry).search(
                    entity, output_root=root / "catalogs" / entity.id, limit=8, timeout=budget
                )
                catalog_searches.append(search.model_dump(mode="json"))
                versions = search.versions
            except (ValueError, OSError, KeyError):
                records.append({"entity_id": entity.id, "error_code": "invalid_registry_evidence"})
                unresolved.append(entity.id)
                continue
            accepted = False
            for version in versions[:8]:
                entry = {
                    "entity_id": entity.id,
                    "version_sha256": version.version_sha256,
                    "acquisition_source": "local",
                    "origin": version.source.model_dump(),
                    "selection": "exact",
                }
                records.append(entry)
                remaining = int(deadline - time.monotonic())
                if remaining < 1:
                    error = entry["error_code"] = "resolver_timeout"
                    break
                try:
                    version = self.registry.inspect(version.version_sha256)
                    if version.category != entity.category:
                        entry["error_code"] = "category_mismatch"
                        continue
                    report = json.loads(self.store.read_artifact(version.normalization_report))
                    dimensions = report.get("dimensions_m")
                    if (
                        not isinstance(dimensions, list)
                        or len(dimensions) != 3
                        or any(
                            type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                            for v in dimensions
                        )
                    ):
                        entry["error_code"] = "invalid_geometry_dimensions"
                        continue
                    if entity.dimensions is not None and any(
                        wanted is not None and not math.isclose(wanted, actual, abs_tol=1e-6)
                        for wanted, actual in zip(entity.dimensions, dimensions, strict=True)
                    ):
                        entry["error_code"] = "dimensions_mismatch"
                        continue
                    if self.preview is None:
                        entry["error_code"] = "missing_preview"
                        continue
                    preview_budget = int(deadline - time.monotonic())
                    if preview_budget < 1:
                        error = entry["error_code"] = "resolver_timeout"
                        break
                    proof = self.preview(version, timeout=preview_budget)
                    if not isinstance(proof, AssetPreviewProof):
                        entry["error_code"] = "missing_preview"
                        continue
                    proof = AssetPreviewProof.model_validate_json(proof.model_dump_json())
                    entry["preview_proof"] = proof.model_dump(mode="json")
                    artifact_closure(self.store, (proof.receipt,))
                    receipt = json.loads(self.store.read_artifact(proof.receipt))
                    if (
                        proof.status != "passed"
                        or proof.image is None
                        or proof.version_sha256 != version.version_sha256
                        or receipt.get("scope") != "asset_preview_scope"
                        or receipt.get("status") != "passed"
                        or receipt.get("version_sha256") != version.version_sha256
                        or proof.image.model_dump(mode="json")
                        not in receipt.get("outputs", {}).values()
                    ):
                        entry["error_code"] = "unbound_preview_evidence"
                        continue
                    preview = proof.image
                    data = self.store.read_artifact(preview)
                    with Image.open(BytesIO(data)) as image:
                        image.verify()
                    entry["preview"] = preview.model_dump()
                    entry["preview_origin_trust"] = "deployment_renderer_port"
                    if self.backend is None:
                        error = entry["error_code"] = "blocked_external_resource"
                        resources = ("managed_codex_asset_assessment",)
                        break
                    remaining = int(deadline - time.monotonic())
                    if remaining < 1:
                        error = entry["error_code"] = "resolver_timeout"
                        break
                    advisory = self.backend.assess_asset_candidates(
                        (
                            VisualCandidate(
                                candidate_id=version.version_sha256,
                                name=version.asset_id,
                                category=entity.category,
                                want_color=entity.color,
                                want_material=entity.material,
                                preview=preview,
                            ),
                        ),
                        output_root=root / f"{entity.id}-{version.version_sha256}",
                        timeout=remaining,
                    )
                    advisory = AssetVisualAssessment.model_validate_json(advisory.model_dump_json())
                    for ref in (
                        advisory.receipt,
                        *advisory.evidence,
                        *(v.detail for v in advisory.verdicts),
                    ):
                        self.store.read_artifact(ref)
                    entry["advisory"] = advisory.model_dump(mode="json")
                    if time.monotonic() >= deadline:
                        error = entry["error_code"] = "resolver_timeout"
                        break
                    if advisory.status != "completed" or advisory.error_code is not None:
                        entry["error_code"] = advisory.error_code or "visual_assessment_failed"
                        if advisory.status == "blocked":
                            error, resources = (
                                entry["error_code"],
                                ("managed_codex_asset_assessment",),
                            )
                            break
                        continue
                    if (
                        len(advisory.verdicts) != 1
                        or advisory.verdicts[0].candidate_id != version.version_sha256
                        or advisory.verdicts[0].preview != preview
                    ):
                        entry["error_code"] = "unbound_visual_verdict"
                        continue
                    if advisory.verdicts[0].verdict != "match":
                        entry["error_code"] = "visual_mismatch"
                        if entity.color and advisory.verdicts[0].verdict == "mismatch":
                            candidate = classify_color_repair(
                                self.store, scene_ir, entity, version, proof, advisory
                            )
                            if candidate is not None:
                                pending.append(candidate)
                                entry["status"] = "pending_color_repair"
                                break
                        continue
                    assets.append(
                        ResolvedAsset(
                            entity_id=entity.id,
                            version_sha256=version.version_sha256,
                            acquisition_source="local",
                            selection="exact",
                        )
                    )
                    entry["status"] = "accepted"
                    accepted = True
                    break
                except (ValueError, OSError, KeyError, TypeError, RuntimeError):
                    entry["error_code"] = "invalid_candidate_evidence"
            if not accepted:
                unresolved.append(entity.id)
            if error:
                unresolved.extend(
                    e.id
                    for e in scene.entities
                    if e.role == "foreground"
                    and e.id in selected
                    and e.id not in unresolved
                    and e.id not in {a.entity_id for a in assets}
                )
                break
        next_source = None
        if unresolved:
            if error is None:
                next_source = next(
                    (source for source in allowed_sources if source != "local"), None
                )
                error = "source_adapter_not_connected" if next_source else "local_assets_unresolved"
                resources = (f"{next_source}_resolver",) if next_source else ()
            status = "blocked"
        else:
            status = "succeeded"
        if pending:
            error = (
                error
                if error
                and error not in {"source_adapter_not_connected", "local_assets_unresolved"}
                else "local_color_repair_pending"
            )
            resources = () if error == "local_color_repair_pending" else resources
        resolved = ResolvedAssetSet(scene_ir=scene_ir, assets=tuple(assets))
        payload = {
            "status": status,
            "error_code": error,
            "scene_ir": scene_ir.model_dump(),
            "allowed_sources": allowed_sources,
            "allow_cousin": allow_cousin,
            "cousin_status": "not_implemented",
            "resolved": resolved.model_dump(mode="json"),
            "unresolved_entities": unresolved,
            "candidates": records,
            "catalog_searches": catalog_searches,
            "searched_entities": searched_entities,
            "pending_color_repairs": [p.model_dump(mode="json") for p in pending],
            "next_source": next_source,
            "required_resources": resources,
            "wall_seconds": time.monotonic() - started,
            "physical_evaluated": False,
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        (root / "receipt.json").write_bytes(raw)
        receipt = self.store.write_artifact(raw, "application/json")
        return ResolutionResult(
            status=status,
            resolved=resolved,
            receipt=receipt,
            error_code=error,
            next_source=next_source,
            required_resources=resources,
            pending_color_repairs=tuple(pending),
        )
