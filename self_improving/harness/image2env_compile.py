"""Controlled exact-reuse image compile candidate over a complete CAS evidence graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel

from .artifacts import LocalArtifactStore
from .registry import HandlerResult, RunContext, SkillBlocked
from .schemas.common import ArtifactRef, Blocker
from .schemas.x2env import (
    Image2EnvCompileV2Input,
    Image2EnvCompileV2Output,
    X2EnvAssetDecision,
    X2EnvEnvironmentPackage,
)
from .schemas.x2env_compile_evidence import (
    AssetClosureManifestV1,
    SceneIRV1,
    TaskIRV1,
    X2EnvAssetSelectionV1,
    X2EnvBindingManifestV1,
    X2EnvCompileReceiptV2,
    X2EnvStaticValidationV1,
    canonical_evidence_sha256,
)
from .x2env_compile_closure import (
    ExactReuseCompileClosureVerifier,
    X2EnvCompileClosureError,
    x2env_environment_package_sha256,
)


@dataclass(frozen=True, slots=True)
class Image2EnvExactReuseCompileHandler:
    """Build one deterministic, non-physical image2env exact-reuse candidate."""

    artifact_store: LocalArtifactStore
    max_source_bytes: int
    max_decoded_pixels: int
    expected_implementation_sha256: str
    trusted_qualification_issuer_sha256: str
    expected_runtime_lock: ArtifactRef
    trusted_asset_qualifications: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        self._verifier()

    def __call__(
        self,
        value: Image2EnvCompileV2Input,
        context: RunContext,
    ) -> HandlerResult:
        if type(value) is not Image2EnvCompileV2Input:
            raise TypeError("value must be the exact Image2EnvCompileV2Input")
        if type(context) is not RunContext:
            raise TypeError("context must be the exact RunContext")
        verifier = self._verifier()
        try:
            inputs = verifier.preflight(value)
        except X2EnvCompileClosureError as error:
            raise SkillBlocked(
                Blocker(
                    code="HARN_X2ENV_COMPILE_ASSET_SELECTION_FAILED",
                    message="exact-reuse image compile input failed trusted preflight",
                    stage="image2env.compile.preflight",
                    retryable=False,
                    details={"reason_code": error.code},
                    unknowns=(),
                    artifact_refs=(),
                )
            ) from error

        match = inputs.object_matches[0]
        proposed = inputs.proposal.objects[0]
        representation = match.asset.representation
        compile_input_ref = self._publish("compile-input", value)
        scene = _self_hashed(
            SceneIRV1,
            "scene_ir_sha256",
            {
                "schema_version": "harness.scene_ir.v1",
                "interpretation_proposal": value.interpretation_proposal,
                "frame": {
                    "name": "genesis_world",
                    "handedness": "right_handed",
                    "up_axis": "z",
                    "unit": "m",
                },
                "objects": [
                    {
                        "object_id": proposed.object_id,
                        "category": proposed.category,
                        "dynamic": True,
                        "collision_required": True,
                        "dimensions_m": proposed.dimensions_m,
                    }
                ],
                "relations": inputs.proposal.relations,
            },
        )
        scene_ref = self._publish("scene-ir", scene)
        task = _self_hashed(
            TaskIRV1,
            "task_ir_sha256",
            {
                "schema_version": "harness.task_ir.v1",
                "scene_ir": scene_ref,
                "requested_profile": value.requested_profile,
                "goal_relations": scene.relations,
                "reset_required": True,
                "action_contract": "none",
                "observation_keys": ["observation.scene_visible"],
                "termination_steps": 120,
            },
        )
        task_ref = self._publish("task-ir", task)
        closure = _self_hashed(
            AssetClosureManifestV1,
            "closure_sha256",
            {
                "schema_version": "harness.asset_closure_manifest.v1",
                "assets": [
                    {
                        "subject": match.asset.entry.subject,
                        "representation": match.asset.representation_ref,
                        "representation_sha256": representation.representation_sha256,
                        "loader_closure_sha256": representation.loader_closure_sha256,
                        "root_logical_path": representation.root_logical_path,
                        "members": representation.members,
                    }
                ],
            },
        )
        closure_ref = self._publish("asset-closure", closure)
        selection = _self_hashed(
            X2EnvAssetSelectionV1,
            "selection_sha256",
            {
                "schema_version": "harness.x2env_asset_selection.v1",
                "scene_object_id": proposed.object_id,
                "lane": "exact_asset_reuse",
                "asset_catalog": value.asset_catalog,
                "catalog_subject": match.asset.entry.subject,
                "qualified_subject": match.asset.entry.qualified_subject,
                "representation": match.asset.representation_ref,
                "qualification": match.asset.qualification_ref,
                "asset_closure_manifest": closure_ref,
                "selected": True,
                "decision_reason": (
                    "case-sensitive category matched trusted qualified catalog asset"
                ),
            },
        )
        selection_ref = self._publish("asset-selection", selection)
        binding = _self_hashed(
            X2EnvBindingManifestV1,
            "binding_sha256",
            {
                "schema_version": "harness.x2env_binding_manifest.v1",
                "scene_ir": scene_ref,
                "task_ir": task_ref,
                "asset_catalog": value.asset_catalog,
                "asset_closure_manifest": closure_ref,
                "runtime_lock": self.expected_runtime_lock,
                "bindings": [
                    {
                        "scene_object_id": proposed.object_id,
                        "selection": selection_ref,
                        "qualified_subject": match.asset.entry.qualified_subject,
                        "representation": match.asset.representation_ref,
                        "qualification": match.asset.qualification_ref,
                        "root_logical_path": representation.root_logical_path,
                        "position_m": [0.0, 0.0, 0.74 + proposed.dimensions_m[2] / 2.0],
                        "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                    }
                ],
            },
        )
        binding_ref = self._publish("binding-manifest", binding)
        static_validation = _self_hashed(
            X2EnvStaticValidationV1,
            "validation_sha256",
            {
                "schema_version": "harness.x2env_static_validation.v1",
                "scene_ir": scene_ref,
                "task_ir": task_ref,
                "asset_catalog": value.asset_catalog,
                "asset_selections": [selection_ref],
                "asset_closure_manifest": closure_ref,
                "binding_manifest": binding_ref,
                "runtime_lock": self.expected_runtime_lock,
                "status": "pass",
                "checks": [
                    {
                        "code": "asset_closure_complete",
                        "status": "pass",
                        "evidence": _sorted_refs(selection_ref, closure_ref),
                    },
                    {"code": "binding_complete", "status": "pass", "evidence": [binding_ref]},
                    {
                        "code": "runtime_lock_bound",
                        "status": "pass",
                        "evidence": [self.expected_runtime_lock],
                    },
                    {
                        "code": "scene_task_consistent",
                        "status": "pass",
                        "evidence": _sorted_refs(scene_ref, task_ref),
                    },
                ],
                "blockers": [],
                "simulator_executed": False,
                "physical_validation_claimed": False,
                "publishable": False,
            },
        )
        static_ref = self._publish("static-validation", static_validation)
        package = X2EnvEnvironmentPackage(
            schema_version="harness.x2env_environment_package.v1",
            package_id="0" * 64,
            route_id="image2env",
            producer_skill_ref="image2env.compile@1.0.0",
            source_media=value.source_media,
            requested_profile=value.requested_profile,
            environment_format="harness.genesis_portable.v1",
            backend="genesis",
            representation_origin="harness_native",
            legacy_adapter_receipt=None,
            scene_ir=scene_ref,
            task_ir=task_ref,
            asset_closure_manifest=closure_ref,
            binding_manifest=binding_ref,
            runtime_lock=self.expected_runtime_lock,
            simulator_ready=False,
            collision_qualified=False,
        )
        package = package.model_copy(
            update={"package_id": x2env_environment_package_sha256(package)}
        )
        package_ref = self._publish("environment-package", package)
        receipt = _self_hashed(
            X2EnvCompileReceiptV2,
            "receipt_sha256",
            {
                "schema_version": "harness.x2env_compile_receipt.v2",
                "skill_ref": "image2env.compile@1.0.0",
                "compile_input": compile_input_ref,
                "source_media_sha256": value.source_media.source_media_sha256,
                "interpretation_proposal": value.interpretation_proposal,
                "asset_catalog": value.asset_catalog,
                "scene_ir": scene_ref,
                "task_ir": task_ref,
                "asset_selections": [selection_ref],
                "asset_closure_manifest": closure_ref,
                "binding_manifest": binding_ref,
                "runtime_lock": self.expected_runtime_lock,
                "static_validation": static_ref,
                "environment_package": package_ref,
                "package_id": package.package_id,
                "predecessor_package": value.predecessor_package,
                "implementation_sha256": self.expected_implementation_sha256,
                "simulator_executed": False,
                "publishable": False,
            },
        )
        receipt_ref = self._publish("compile-receipt", receipt)
        output = Image2EnvCompileV2Output(
            schema_version="harness.image2env_compile_output.v2",
            environment_package=package,
            scene_ir=scene_ref,
            task_ir=task_ref,
            asset_decisions=(
                X2EnvAssetDecision(
                    schema_version="harness.x2env_asset_decision.v1",
                    object_id=proposed.object_id,
                    lane="exact_asset_reuse",
                    selected_artifact=match.asset.representation_ref,
                    decision_evidence=selection_ref,
                    source_qualification=match.asset.qualification_ref,
                    digital_cousin_evidence=None,
                    generation_receipt=None,
                ),
            ),
            staged_assets=(),
            static_validation=static_ref,
            compile_receipt=receipt_ref,
            simulator_executed=False,
            publishable=False,
        )
        verifier.verify(value, output)
        artifacts = self._supporting_closure(value, output)
        context.emit("image2env.compile.evidence.complete", artifact_refs=artifacts)
        return HandlerResult(output=output, artifacts=artifacts)

    def _verifier(self) -> ExactReuseCompileClosureVerifier:
        return ExactReuseCompileClosureVerifier(
            artifact_store=self.artifact_store,
            max_source_bytes=self.max_source_bytes,
            max_decoded_pixels=self.max_decoded_pixels,
            expected_implementation_sha256=self.expected_implementation_sha256,
            trusted_qualification_issuer_sha256=self.trusted_qualification_issuer_sha256,
            expected_runtime_lock=self.expected_runtime_lock,
            trusted_asset_qualifications=self.trusted_asset_qualifications,
        )

    def _publish(self, label: str, value: object) -> ArtifactRef:
        schema_version = getattr(value, "schema_version")
        payload = _canonical_json_bytes(value.model_dump(mode="json"))
        with TemporaryDirectory(prefix="harness-image2env-compile-") as root:
            path = Path(root) / f"{label}.json"
            path.write_bytes(payload)
            return self.artifact_store.put_file(
                path,
                name=f"{label}.json",
                media_type="application/json",
                schema_version=schema_version,
            )

    def _supporting_closure(
        self,
        compile_input: Image2EnvCompileV2Input,
        compile_output: Image2EnvCompileV2Output,
    ) -> tuple[ArtifactRef, ...]:
        pending = list(_refs((compile_input, compile_output)))
        refs: dict[tuple[str, str | None, str], ArtifactRef] = {}
        while pending:
            ref = pending.pop()
            identity = (ref.media_type, ref.schema_version, ref.sha256)
            if identity in refs:
                continue
            payload = self.artifact_store.resolve(ref).path.read_bytes()
            refs[identity] = ref
            if ref.media_type == "application/json":
                pending.extend(_refs(json.loads(payload)))
        return tuple(
            refs[key] for key in sorted(refs, key=lambda item: (item[0], item[1] or "", item[2]))
        )


def _self_hashed(model: type, digest_field: str, payload: dict[str, object]):
    value = dict(payload)
    value[digest_field] = canonical_evidence_sha256(value, digest_field)
    return model.model_validate(value)


def _sorted_refs(*refs: ArtifactRef) -> tuple[ArtifactRef, ...]:
    return tuple(sorted(refs, key=lambda item: (item.sha256, item.bytes, item.media_type)))


def _refs(value: object):
    if isinstance(value, ArtifactRef):
        yield value
    elif isinstance(value, BaseModel):
        for name in value.__class__.model_fields:
            yield from _refs(getattr(value, name))
    elif isinstance(value, dict):
        required = {"name", "uri", "media_type", "sha256", "bytes", "schema_version"}
        if required.issubset(value):
            yield ArtifactRef.model_validate(value)
        else:
            for child in value.values():
                yield from _refs(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _refs(child)


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


__all__ = ["Image2EnvExactReuseCompileHandler"]
