"""One bounded web acquisition pass through Yuxin, with explicit preparation authority."""

import hashlib
import json
import math
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from .asset_advisory import AssetVisualAssessment, VisualCandidate
from .asset_preparation import NormalizationParameters
from .asset_preview import AssetPreviewProof
from .assets import AssetLicense, AssetSource
from .compile import ResolvedAsset, ResolvedAssetSet
from .contracts import ArtifactRef, SceneIR
from .normalization import normalize_mesh
from .resolver import ResolutionResult


class WebAssetResolver:
    def __init__(self, store, registry, provider, backend, preview, prepare):
        self.store, self.registry, self.provider = store, registry, provider
        self.backend, self.preview, self.prepare = backend, preview, prepare

    def resolve(
        self, scene_ir, *, allowed_sources, allow_cousin, output_root, timeout=600, entity_ids=None
    ):
        root = Path(output_root)
        if (
            not root.is_absolute()
            or any(p.is_symlink() for p in (root, *root.parents))
            or type(timeout) is not int
            or not 1 <= timeout <= 600
            or type(allow_cousin) is not bool
            or not allowed_sources
            or len(set(allowed_sources)) != len(allowed_sources)
            or any(s not in {"local", "web", "reconstruction"} for s in allowed_sources)
        ):
            raise ValueError("invalid web resolver configuration")
        root.mkdir(parents=True, exist_ok=False)
        start = time.monotonic()
        deadline = start + timeout
        records, assets = [], []
        error, unresolved = None, []
        try:
            scene = SceneIR.model_validate_json(self.store.read_artifact(scene_ir))
        except (ValueError, OSError):
            return self._finish(
                scene_ir,
                root,
                start,
                records,
                assets,
                ["invalid_scene"],
                "invalid_scene_evidence",
                allowed_sources,
                allow_cousin,
            )
        selected = {e.id for e in scene.entities if e.role == "foreground"}
        if entity_ids is not None:
            if len(set(entity_ids)) != len(entity_ids) or not set(entity_ids) <= selected:
                raise ValueError("invalid resolver entity filter")
            selected = set(entity_ids)
        for entity in scene.entities:
            if entity.role != "foreground" or entity.id not in selected:
                continue
            accepted = False
            if "web" not in allowed_sources:
                unresolved.append(entity.id)
                error = "web_source_not_allowed"
                continue
            if time.monotonic() >= deadline:
                unresolved.append(entity.id)
                error = "resolver_timeout"
                continue
            try:
                search = self.provider.search(entity, "web", limit=8)
                self.store.read_artifact(search.receipt)
                if search.status != "succeeded":
                    records.append(
                        {"entity_id": entity.id, "search": search.model_dump(mode="json")}
                    )
                    unresolved.append(entity.id)
                    error = search.error_code or "web_search_failed"
                    continue
            except (ValueError, OSError, KeyError, RuntimeError) as exc:
                records.append(
                    {
                        "entity_id": entity.id,
                        "error_code": "invalid_provider_evidence",
                        "reason": str(exc),
                    }
                )
                unresolved.append(entity.id)
                error = "invalid_provider_evidence"
                continue
            seen = set()
            for index, candidate in enumerate(search.candidates[:8]):
                if candidate.candidate_id in seen:
                    continue
                seen.add(candidate.candidate_id)
                entry = {
                    "entity_id": entity.id,
                    "candidate": candidate.model_dump(mode="json"),
                    "search": search.receipt.model_dump(mode="json"),
                }
                records.append(entry)
                try:
                    if int(deadline - time.monotonic()) < 1:
                        raise ValueError("resolver_timeout")
                    if candidate.entity_id != entity.id or candidate.source != "web":
                        raise ValueError("unbound_provider_candidate")
                    self.store.read_artifact(candidate.engine_record)
                    license = candidate.license
                    if license.status != "declared" or license.evidence is None:
                        raise ValueError("blocked_license")
                    license_record = AssetLicense(
                        spdx=license.spdx,
                        attribution=license.attribution,
                        source_url=license.source_url,
                        evidence=license.evidence,
                    )
                    self.store.read_artifact(license.evidence)
                    fetch_root = root / f"{entity.id}-{index}-fetch"
                    fetched = self.provider.fetch(candidate, fetch_root)
                    self.store.read_artifact(fetched.receipt)
                    entry["fetch"] = fetched.model_dump(mode="json")
                    if fetched.status != "succeeded" or not fetched.source_path:
                        raise ValueError(fetched.error_code or "web_fetch_failed")
                    source = Path(fetched.source_path)
                    if any(
                        p.is_symlink() for p in (source, *source.parents)
                    ) or not source.resolve().is_relative_to(fetch_root.resolve()):
                        raise ValueError("unsafe_provider_output")
                    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
                    params = self.prepare(entity, candidate, fetched) if self.prepare else None
                    if params is None:
                        raise ValueError("missing_physical_metadata")
                    params = NormalizationParameters.model_validate_json(params.model_dump_json())
                    expected = {
                        "scene_ir": scene_ir.model_dump(mode="json"),
                        "entity_id": entity.id,
                        "candidate_id": candidate.candidate_id,
                        "fetch_receipt": fetched.receipt.model_dump(mode="json"),
                        "source_sha256": source_sha,
                        "parameters": params.model_dump(mode="json", exclude={"evidence"}),
                    }
                    if json.loads(self.store.read_artifact(params.evidence)) != expected:
                        raise ValueError("unbound_preparation_evidence")
                    entry["preparation"] = params.model_dump(mode="json")
                    if entity.dimensions is not None and any(
                        w is not None and not math.isclose(w, a, abs_tol=1e-6)
                        for w, a in zip(entity.dimensions, params.dimensions_m, strict=True)
                    ):
                        raise ValueError("dimensions_mismatch")
                    if params.base_color is not None:
                        if not params.color_basis or not params.declared_color:
                            raise ValueError("missing_color_provenance")
                        if (
                            entity.color
                            and entity.color.casefold() != params.declared_color.casefold()
                        ):
                            raise ValueError("color_mismatch")
                    normalized = root / f"{entity.id}-{index}-normalized"
                    report = normalize_mesh(
                        source,
                        normalized,
                        dimensions_m=params.dimensions_m,
                        up_axis=params.up_axis,
                        mass_kg=params.mass_kg,
                        friction=params.friction,
                        color_rgba=params.base_color,
                    )
                    version = self.registry.register(
                        candidate.candidate_id,
                        entity.category,
                        normalized,
                        report["entrypoint"],
                        files=tuple(m["path"] for m in report["files"]),
                        normalization_report=self.store.write_artifact(
                            json.dumps(report).encode(), "application/json"
                        ),
                        license=license_record,
                        source=AssetSource(
                            kind="web",
                            provider=candidate.provider,
                            source_ref=candidate.source_page,
                            evidence=fetched.receipt,
                        ),
                        receipt=params.evidence,
                    )
                    version = self.registry.inspect(version.version_sha256)
                    entry["version_sha256"] = version.version_sha256
                    if self.preview is None:
                        raise ValueError("missing_preview")
                    if int(deadline - time.monotonic()) < 1:
                        raise ValueError("resolver_timeout")
                    preview = self.preview(version)
                    preview = AssetPreviewProof.model_validate_json(preview.model_dump_json())
                    entry["preview"] = preview.model_dump(mode="json")
                    proof = json.loads(self.store.read_artifact(preview.receipt))
                    if (
                        preview.status != "passed"
                        or preview.image is None
                        or preview.version_sha256 != version.version_sha256
                        or proof.get("version_sha256") != version.version_sha256
                        or proof.get("scope") != "asset_preview_scope"
                        or proof.get("status") != "passed"
                        or preview.image.model_dump(mode="json")
                        not in proof.get("outputs", {}).values()
                    ):
                        raise ValueError("unbound_preview_evidence")
                    for output_ref in proof["outputs"].values():
                        self.store.read_artifact(ArtifactRef.model_validate(output_ref))
                    for field in ("runtime_scene",):
                        if field in proof:
                            self.store.read_artifact(ArtifactRef.model_validate(proof[field]))
                    for member_ref in proof.get("package", {}).values():
                        self.store.read_artifact(ArtifactRef.model_validate(member_ref))
                    with Image.open(BytesIO(self.store.read_artifact(preview.image))) as image:
                        image.verify()
                    remaining = int(deadline - time.monotonic())
                    if remaining < 1:
                        raise ValueError("resolver_timeout")
                    if self.backend is None:
                        raise ValueError("missing_managed_codex")
                    advisory = self.backend.assess_asset_candidates(
                        (
                            VisualCandidate(
                                candidate_id=version.version_sha256,
                                name=candidate.candidate_id,
                                category=entity.category,
                                want_color=entity.color,
                                want_material=entity.material,
                                preview=preview.image,
                            ),
                        ),
                        output_root=root / f"{entity.id}-{index}-visual",
                        timeout=remaining,
                    )
                    advisory = AssetVisualAssessment.model_validate_json(advisory.model_dump_json())
                    entry["advisory"] = advisory.model_dump(mode="json")
                    for ref in (
                        advisory.receipt,
                        *advisory.evidence,
                        *(v.detail for v in advisory.verdicts),
                    ):
                        self.store.read_artifact(ref)
                    if time.monotonic() >= deadline:
                        raise ValueError("resolver_timeout")
                    if (
                        advisory.status != "completed"
                        or advisory.error_code
                        or len(advisory.verdicts) != 1
                        or advisory.verdicts[0].candidate_id != version.version_sha256
                        or advisory.verdicts[0].preview != preview.image
                    ):
                        raise ValueError("unbound_visual_verdict")
                    if advisory.verdicts[0].verdict != "match":
                        raise ValueError("visual_mismatch")
                    assets.append(
                        ResolvedAsset(
                            entity_id=entity.id,
                            version_sha256=version.version_sha256,
                            acquisition_source="web",
                            selection="exact",
                        )
                    )
                    entry["status"], accepted = "accepted", True
                    break
                except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                    error = entry["error_code"] = str(exc)
                    if error == "resolver_timeout":
                        break
            if not accepted:
                unresolved.append(entity.id)
        return self._finish(
            scene_ir, root, start, records, assets, unresolved, error, allowed_sources, allow_cousin
        )

    def _finish(
        self,
        scene_ir,
        root,
        start,
        records,
        assets,
        unresolved,
        error,
        allowed_sources,
        allow_cousin,
    ):
        status = "blocked" if unresolved else "succeeded"
        error = (error or "web_assets_unresolved") if unresolved else None
        next_source = (
            "reconstruction" if unresolved and "reconstruction" in allowed_sources else None
        )
        resolved = ResolvedAssetSet(scene_ir=scene_ir, assets=tuple(assets))
        payload = {
            "status": status,
            "error_code": error,
            "candidates": records,
            "resolved": resolved.model_dump(mode="json"),
            "wall_seconds": time.monotonic() - start,
            "unresolved_entities": unresolved,
            "next_source": next_source,
            "allow_cousin": allow_cousin,
            "cousin_status": "not_implemented",
            "physical_evaluated": False,
            "preparation_trust": "deployment_port",
            "source_receipts": "same_store_provider; transitive closure requires Registry gate",
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        (root / "receipt.json").write_bytes(raw)
        return ResolutionResult(
            status=status,
            resolved=resolved,
            receipt=self.store.write_artifact(raw, "application/json"),
            error_code=error,
            next_source=next_source,
            required_resources=(error,) if unresolved else (),
        )
