"""One image-based reconstruction attempt per entity, without workflow authority."""

import json
import time
from pathlib import Path

from .artifacts import artifact_closure
from .asset_advisory import AssetVisualAssessment, VisualCandidate
from .asset_preview import AssetPreviewProof
from .assets import AssetLicense, AssetSource
from .compile import ResolvedAsset, ResolvedAssetSet
from .contracts import ArtifactRef, Model, SceneIR
from .normalization import normalize_mesh
from .reconstruction_planning import plan_reconstruction
from .resolver import ResolutionResult


class ReconstructionImage(Model):
    image: ArtifactRef
    provenance: ArtifactRef


class ReconstructionResolver:
    def __init__(self, store, registry, backend, adapter, preview, image_port, *, seed):
        if type(seed) is not int or not 0 <= seed < 2**31:
            raise ValueError("invalid seed")
        self.store, self.registry, self.backend = store, registry, backend
        self.adapter, self.preview, self.image_port, self.seed = adapter, preview, image_port, seed

    def resolve(
        self, scene_ir, *, allowed_sources, allow_cousin, output_root, timeout=600, entity_ids=None
    ):
        if (
            type(timeout) is not int
            or not 1 <= timeout <= 600
            or type(allow_cousin) is not bool
            or not allowed_sources
            or len(set(allowed_sources)) != len(allowed_sources)
            or any(s not in {"local", "web", "reconstruction"} for s in allowed_sources)
        ):
            raise ValueError("invalid reconstruction resolver constraints")
        root = Path(output_root)
        if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("resolver root must be absolute new nonsymbolic directory")
        root.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()

        def remaining():
            seconds = int(timeout - (time.monotonic() - started))
            if seconds < 1:
                raise ValueError("resolver_timeout")
            return seconds

        scene = SceneIR.model_validate_json(self.store.read_artifact(scene_ir))
        selected = {e.id for e in scene.entities if e.role == "foreground"}
        if entity_ids is not None:
            if len(set(entity_ids)) != len(entity_ids) or not set(entity_ids) <= selected:
                raise ValueError("invalid resolver entity filter")
            selected = set(entity_ids)
        records, assets, missing = [], [], []
        error = None
        for entity in scene.entities:
            if entity.id not in selected:
                continue
            record = {
                "entity_id": entity.id,
                "acquisition_source": "reconstruction",
                "seed": self.seed,
            }
            records.append(record)
            try:
                remaining()
                if "reconstruction" not in allowed_sources:
                    raise ValueError("reconstruction_source_not_allowed")
                if self.adapter is None:
                    raise ValueError("missing_reconstruction_adapter")
                chosen = self.image_port(entity) if self.image_port else None
                if chosen is None:
                    raise ValueError("missing_reconstruction_image")
                chosen = ReconstructionImage.model_validate_json(chosen.model_dump_json())
                artifact_closure(self.store, (chosen.image, chosen.provenance))
                selection = json.loads(self.store.read_artifact(chosen.provenance))
                if (
                    selection.get("scene_ir") != scene_ir.model_dump(mode="json")
                    or selection.get("entity_id") != entity.id
                    or selection.get("image_ref") != chosen.image.model_dump(mode="json")
                    or not selection.get("selection_basis")
                ):
                    raise ValueError("unbound_reconstruction_image_selection")
                record["input"] = chosen.model_dump(mode="json")
                if self.backend is None:
                    raise ValueError("missing_managed_codex")
                plan = plan_reconstruction(
                    self.backend,
                    scene_ir,
                    entity,
                    chosen.image,
                    output_root=root / f"{entity.id}-plan",
                    timeout=remaining(),
                )
                record["plan"] = plan.model_dump(mode="json")
                artifact_closure(self.store, (plan.receipt,))
                if (
                    plan.status != "completed"
                    or plan.parameters is None
                    or plan.segmentation is None
                ):
                    raise ValueError(plan.error_code or "reconstruction_planning_failed")
                generated_root = root / f"{entity.id}-generation"
                generated = self.adapter.reconstruct(
                    chosen.image,
                    proposal=plan.segmentation,
                    mass_kg=plan.parameters.mass_kg,
                    friction=plan.parameters.friction,
                    seed=self.seed,
                    output_root=generated_root,
                    timeout=remaining(),
                )
                record["generation"] = generated.model_dump(mode="json")
                artifact_closure(self.store, (generated.receipt,))
                if generated.geometry is not None:
                    self.store.read_artifact(generated.geometry)
                if generated.status != "succeeded":
                    raise ValueError(generated.error_code or "reconstruction_failed")
                if not generated.registration_allowed or generated.derivation_authorization is None:
                    raise ValueError("blocked_license")
                auth_ref = generated.derivation_authorization
                artifact_closure(self.store, (auth_ref,))
                auth = json.loads(self.store.read_artifact(auth_ref))
                if (
                    auth.get("input_sha256") != chosen.image.sha256
                    or auth.get("allow_derivative") is not True
                ):
                    raise ValueError("blocked_license")
                license = AssetLicense(
                    spdx=auth["output_spdx"],
                    source_url=auth["source_url"],
                    attribution=auth.get("attribution"),
                    evidence=auth_ref,
                )
                if generated.geometry is None or not generated.source_path:
                    raise ValueError("missing_generated_geometry")
                source = Path(generated.source_path)
                if (
                    source.suffix.lower() != ".glb"
                    or any(p.is_symlink() for p in (source, *source.parents))
                    or not source.resolve().is_relative_to(generated_root.resolve())
                    or source.read_bytes() != self.store.read_artifact(generated.geometry)
                ):
                    raise ValueError("unbound_generated_geometry")
                remaining()
                normalized = root / f"{entity.id}-normalized"
                report = normalize_mesh(
                    source,
                    normalized,
                    dimensions_m=plan.parameters.dimensions_m,
                    up_axis="Y",
                    mass_kg=plan.parameters.mass_kg,
                    friction=plan.parameters.friction,
                )
                version = self.registry.register(
                    f"reconstructed-{entity.id}-{generated.geometry.sha256[:12]}",
                    entity.category,
                    normalized,
                    report["entrypoint"],
                    files=tuple(f["path"] for f in report["files"]),
                    normalization_report=self.store.write_artifact(
                        json.dumps(report).encode(), "application/json"
                    ),
                    license=license,
                    source=AssetSource(
                        kind="reconstruction",
                        provider="GujieReconstructionAdapter",
                        source_ref=generated.geometry.sha256,
                        evidence=generated.receipt,
                    ),
                    receipt=plan.receipt,
                )
                version = self.registry.inspect(version.version_sha256)
                record["version_sha256"] = version.version_sha256
                if self.preview is None:
                    raise ValueError("missing_preview")
                preview = self.preview(version, timeout=remaining())
                preview = AssetPreviewProof.model_validate_json(preview.model_dump_json())
                record["preview"] = preview.model_dump(mode="json")
                artifact_closure(self.store, (preview.receipt,))
                proof = json.loads(self.store.read_artifact(preview.receipt))
                if (
                    preview.status != "passed"
                    or preview.image is None
                    or preview.version_sha256 != version.version_sha256
                    or proof.get("scope") != "asset_preview_scope"
                    or proof.get("version_sha256") != version.version_sha256
                    or proof.get("status") != "passed"
                    or preview.image.model_dump(mode="json")
                    not in proof.get("outputs", {}).values()
                ):
                    raise ValueError("unbound_preview_evidence")
                self.store.read_artifact(preview.image)
                advisory = self.backend.assess_asset_candidates(
                    (
                        VisualCandidate(
                            candidate_id=version.version_sha256,
                            name=version.asset_id,
                            category=entity.category,
                            want_color=entity.color,
                            want_material=entity.material,
                            preview=preview.image,
                        ),
                    ),
                    output_root=root / f"{entity.id}-visual",
                    timeout=remaining(),
                )
                advisory = AssetVisualAssessment.model_validate_json(advisory.model_dump_json())
                record["advisory"] = advisory.model_dump(mode="json")
                artifact_closure(
                    self.store,
                    (advisory.receipt, *advisory.evidence, *(v.detail for v in advisory.verdicts)),
                )
                remaining()
                if (
                    advisory.status != "completed"
                    or advisory.error_code
                    or len(advisory.verdicts) != 1
                    or advisory.verdicts[0].candidate_id != version.version_sha256
                    or advisory.verdicts[0].preview != preview.image
                    or advisory.verdicts[0].verdict != "match"
                ):
                    raise ValueError("visual_assessment_failed")
                assets.append(
                    ResolvedAsset(
                        entity_id=entity.id,
                        version_sha256=version.version_sha256,
                        acquisition_source="reconstruction",
                        selection="exact",
                    )
                )
                record["status"] = "accepted"
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                error = record["error_code"] = str(exc)
                missing.append(entity.id)
        status = "blocked" if missing else "succeeded"
        error = error if missing else None
        resolved = ResolvedAssetSet(scene_ir=scene_ir, assets=tuple(assets))
        data = json.dumps(
            {
                "status": status,
                "error_code": error,
                "resolved": resolved.model_dump(mode="json"),
                "records": records,
                "unresolved_entities": missing,
                "physical_evaluated": False,
                "image_selection_trust": "Harness_input_bundle_port",
                "license_authority": "deployment_authorization",
                "cousin_status": "not_implemented",
                "wall_seconds": time.monotonic() - started,
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
