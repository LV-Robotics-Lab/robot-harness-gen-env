"""Strict CAS consumer for one image2env exact-reuse compile evidence graph."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .artifacts import ArtifactResolutionError, LocalArtifactStore
from .image_ingest import ImageMediaConsumerVerifier, ImageMediaIngestError
from .schemas.common import ArtifactRef, ValidationStatus
from .schemas.compile_attempt import InitialCompileAttemptV1
from .schemas.media_ingest import ImageMediaProbeV1
from .schemas.x2env import (
    AcquisitionPolicy,
    AssetAcquisitionLane,
    Image2EnvCompileOutput,
    Image2EnvCompileV2Input,
    RepresentationOrigin,
    RequestedGenesisProfile,
    X2EnvEnvironmentPackage,
)
from .schemas.x2env_compile_evidence import (
    ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
    ASSET_QUALIFICATION_SCHEMA_ID,
    ASSET_REPRESENTATION_SCHEMA_ID,
    GENESIS_RUNTIME_LOCK_SCHEMA_ID,
    PORTABLE_ASSET_CATALOG_SCHEMA_ID,
    SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID,
    SCENE_IR_SCHEMA_ID,
    TASK_IR_SCHEMA_ID,
    X2ENV_ASSET_SELECTION_SCHEMA_ID,
    X2ENV_BINDING_MANIFEST_SCHEMA_ID,
    X2ENV_COMPILE_RECEIPT_SCHEMA_ID,
    X2ENV_STATIC_VALIDATION_SCHEMA_ID,
    AssetClosureManifestV1,
    AssetRepresentationV1,
    GenesisAssetQualificationV1,
    GenesisRuntimeLockV1,
    PortableAssetCatalogV1,
    SceneInterpretationProposalV1,
    SceneIRV1,
    TaskIRV1,
    X2EnvAssetSelectionV1,
    X2EnvBindingManifestV1,
    X2EnvCompileReceiptV1,
    X2EnvStaticValidationV1,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_KeyT = TypeVar("_KeyT")
_ValueT = TypeVar("_ValueT")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class X2EnvCompileClosureError(ValueError):
    """Stable failure raised when an exact-reuse evidence graph is not trustworthy."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class VerifiedExactReuseCompileClosure:
    """Fully parsed image compile graph returned only after strict CAS verification."""

    source_probe: ImageMediaProbeV1
    proposal: SceneInterpretationProposalV1
    scene_ir: SceneIRV1
    task_ir: TaskIRV1
    runtime_lock: GenesisRuntimeLockV1
    asset_catalog: PortableAssetCatalogV1
    representations: tuple[AssetRepresentationV1, ...]
    qualifications: tuple[GenesisAssetQualificationV1, ...]
    selections: tuple[X2EnvAssetSelectionV1, ...]
    asset_closure: AssetClosureManifestV1
    binding_manifest: X2EnvBindingManifestV1
    static_validation: X2EnvStaticValidationV1
    environment_package: X2EnvEnvironmentPackage
    compile_receipt: X2EnvCompileReceiptV1


@dataclass(frozen=True, slots=True)
class ExactReuseCompileClosureVerifier:
    """Verify the first image2env exact-only, harness-native, rigid-scene compile slice."""

    artifact_store: LocalArtifactStore
    max_source_bytes: int
    max_decoded_pixels: int
    expected_implementation_sha256: str
    trusted_qualification_issuer_sha256: str
    expected_runtime_lock: ArtifactRef
    trusted_asset_qualifications: tuple[ArtifactRef, ...]

    def __post_init__(self) -> None:
        if type(self.artifact_store) is not LocalArtifactStore:
            raise TypeError("artifact_store must be the exact LocalArtifactStore")
        for value, field in (
            (self.max_source_bytes, "max_source_bytes"),
            (self.max_decoded_pixels, "max_decoded_pixels"),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field} must be a positive exact integer")
        for value, field in (
            (self.expected_implementation_sha256, "expected_implementation_sha256"),
            (
                self.trusted_qualification_issuer_sha256,
                "trusted_qualification_issuer_sha256",
            ),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{field} must be a lowercase SHA-256")
        if type(self.expected_runtime_lock) is not ArtifactRef:
            raise TypeError("expected_runtime_lock must be the exact ArtifactRef")
        _require_ref(
            self.expected_runtime_lock,
            "expected_runtime_lock",
            GENESIS_RUNTIME_LOCK_SCHEMA_ID,
        )
        if type(self.trusted_asset_qualifications) is not tuple:
            raise TypeError("trusted_asset_qualifications must be an exact tuple")
        qualification_keys = tuple(ref.sha256 for ref in self.trusted_asset_qualifications)
        if not qualification_keys or qualification_keys != tuple(sorted(set(qualification_keys))):
            raise ValueError("trusted_asset_qualifications must be non-empty, sorted, and unique")
        for index, ref in enumerate(self.trusted_asset_qualifications):
            _require_ref(
                ref,
                f"trusted_asset_qualifications[{index}]",
                ASSET_QUALIFICATION_SCHEMA_ID,
            )

    def verify(
        self,
        compile_input: Image2EnvCompileV2Input,
        compile_output: Image2EnvCompileOutput,
    ) -> VerifiedExactReuseCompileClosure:
        if type(compile_input) is not Image2EnvCompileV2Input:
            raise TypeError("compile_input must be the exact Image2EnvCompileV2Input")
        if type(compile_output) is not Image2EnvCompileOutput:
            raise TypeError("compile_output must be the exact Image2EnvCompileOutput")
        self._verify_supported_input(compile_input)

        try:
            source_probe = ImageMediaConsumerVerifier(
                artifact_store=self.artifact_store,
                max_source_bytes=self.max_source_bytes,
                max_decoded_pixels=self.max_decoded_pixels,
            ).verify(compile_input.source_media)
        except ImageMediaIngestError as error:
            raise X2EnvCompileClosureError(
                error.code, "source image evidence failed verification"
            ) from error

        proposal = self._read(
            compile_input.interpretation_proposal,
            SceneInterpretationProposalV1,
            SCENE_INTERPRETATION_PROPOSAL_SCHEMA_ID,
        )
        scene_ir = self._read(compile_output.scene_ir, SceneIRV1, SCENE_IR_SCHEMA_ID)
        task_ir = self._read(compile_output.task_ir, TaskIRV1, TASK_IR_SCHEMA_ID)
        runtime_lock = self._read(
            self.expected_runtime_lock,
            GenesisRuntimeLockV1,
            GENESIS_RUNTIME_LOCK_SCHEMA_ID,
        )
        asset_catalog = self._read(
            compile_input.asset_catalog,
            PortableAssetCatalogV1,
            PORTABLE_ASSET_CATALOG_SCHEMA_ID,
        )
        environment_package_ref = self._find_package_ref(compile_output)
        environment_package = self._read(
            environment_package_ref,
            X2EnvEnvironmentPackage,
            "harness.x2env_environment_package.v1",
        )
        compile_receipt = self._read(
            compile_output.compile_receipt,
            X2EnvCompileReceiptV1,
            X2ENV_COMPILE_RECEIPT_SCHEMA_ID,
        )
        asset_closure = self._read(
            environment_package.asset_closure_manifest,
            AssetClosureManifestV1,
            ASSET_CLOSURE_MANIFEST_SCHEMA_ID,
        )
        binding_manifest = self._read(
            environment_package.binding_manifest,
            X2EnvBindingManifestV1,
            X2ENV_BINDING_MANIFEST_SCHEMA_ID,
        )
        static_validation = self._read(
            compile_output.static_validation,
            X2EnvStaticValidationV1,
            X2ENV_STATIC_VALIDATION_SCHEMA_ID,
        )
        selection_refs = tuple(item.decision_evidence for item in compile_output.asset_decisions)
        selections = tuple(
            self._read(ref, X2EnvAssetSelectionV1, X2ENV_ASSET_SELECTION_SCHEMA_ID)
            for ref in selection_refs
        )
        representation_refs = _unique_refs(item.representation for item in asset_catalog.entries)
        representations = tuple(
            self._read(ref, AssetRepresentationV1, ASSET_REPRESENTATION_SCHEMA_ID)
            for ref in representation_refs
        )
        qualification_refs = _unique_refs(item.qualification for item in asset_catalog.entries)
        qualifications = tuple(
            self._read(ref, GenesisAssetQualificationV1, ASSET_QUALIFICATION_SCHEMA_ID)
            for ref in qualification_refs
        )

        self._verify_semantics(
            compile_input=compile_input,
            compile_output=compile_output,
            proposal=proposal,
            scene_ir=scene_ir,
            task_ir=task_ir,
            runtime_lock=runtime_lock,
            asset_catalog=asset_catalog,
            representations=representations,
            representation_refs=representation_refs,
            qualifications=qualifications,
            qualification_refs=qualification_refs,
            selections=selections,
            selection_refs=selection_refs,
            asset_closure=asset_closure,
            binding_manifest=binding_manifest,
            static_validation=static_validation,
            environment_package=environment_package,
            environment_package_ref=environment_package_ref,
            compile_receipt=compile_receipt,
        )
        return VerifiedExactReuseCompileClosure(
            source_probe=source_probe,
            proposal=proposal,
            scene_ir=scene_ir,
            task_ir=task_ir,
            runtime_lock=runtime_lock,
            asset_catalog=asset_catalog,
            representations=representations,
            qualifications=qualifications,
            selections=selections,
            asset_closure=asset_closure,
            binding_manifest=binding_manifest,
            static_validation=static_validation,
            environment_package=environment_package,
            compile_receipt=compile_receipt,
        )

    def _verify_supported_input(self, value: Image2EnvCompileV2Input) -> None:
        _require_equal(
            (
                type(value.attempt),
                value.requested_profile,
                value.acquisition_policy,
                value.predecessor_package,
                value.repair_evidence_refs,
                value.model_execution_allowed,
            ),
            (
                InitialCompileAttemptV1,
                RequestedGenesisProfile.RIGID_SCENE,
                AcquisitionPolicy.EXACT_ONLY,
                None,
                (),
                False,
            ),
            "compile input is outside the first exact-only rigid-scene slice",
        )

    def _find_package_ref(self, output: Image2EnvCompileOutput) -> ArtifactRef:
        receipt = self._read(
            output.compile_receipt,
            X2EnvCompileReceiptV1,
            X2ENV_COMPILE_RECEIPT_SCHEMA_ID,
        )
        return receipt.environment_package

    def _read(self, ref: ArtifactRef, model: type[_ModelT], schema_version: str) -> _ModelT:
        _require_ref(ref, model.__name__, schema_version)
        try:
            resolved = self.artifact_store.resolve(ref)
            payload = resolved.path.read_bytes()
        except (ArtifactResolutionError, OSError) as error:
            raise X2EnvCompileClosureError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"{schema_version} evidence is unavailable or corrupt",
            ) from error
        if (  # pragma: no cover - concurrent external CAS tamper defense
            len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256
        ):
            raise X2EnvCompileClosureError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"{schema_version} evidence changed during verified consumption",
            )
        try:
            parsed = model.model_validate_json(payload)
        except ValidationError as error:
            raise X2EnvCompileClosureError(
                "HARN_X2ENV_COMPILE_EVIDENCE_INVALID",
                f"{schema_version} evidence is not strict typed JSON",
            ) from error
        if payload != _canonical_json_bytes(parsed.model_dump(mode="json")):
            raise X2EnvCompileClosureError(
                "HARN_X2ENV_COMPILE_EVIDENCE_INVALID",
                f"{schema_version} evidence is not canonical JSON",
            )
        return parsed

    def _verify_semantics(  # noqa: PLR0913
        self,
        *,
        compile_input: Image2EnvCompileV2Input,
        compile_output: Image2EnvCompileOutput,
        proposal: SceneInterpretationProposalV1,
        scene_ir: SceneIRV1,
        task_ir: TaskIRV1,
        runtime_lock: GenesisRuntimeLockV1,
        asset_catalog: PortableAssetCatalogV1,
        representations: tuple[AssetRepresentationV1, ...],
        representation_refs: tuple[ArtifactRef, ...],
        qualifications: tuple[GenesisAssetQualificationV1, ...],
        qualification_refs: tuple[ArtifactRef, ...],
        selections: tuple[X2EnvAssetSelectionV1, ...],
        selection_refs: tuple[ArtifactRef, ...],
        asset_closure: AssetClosureManifestV1,
        binding_manifest: X2EnvBindingManifestV1,
        static_validation: X2EnvStaticValidationV1,
        environment_package: X2EnvEnvironmentPackage,
        environment_package_ref: ArtifactRef,
        compile_receipt: X2EnvCompileReceiptV1,
    ) -> None:
        _require_equal(
            (proposal.route_id, proposal.source_media_sha256),
            ("image2env", compile_input.source_media.source_media_sha256),
            "interpretation proposal does not bind the image source",
        )
        self._verify_artifact(proposal.effective_prompt, "effective prompt")
        _require_equal(
            scene_ir.interpretation_proposal,
            compile_input.interpretation_proposal,
            "SceneIR does not bind the interpretation proposal",
        )
        expected_scene_objects = tuple(
            (
                item.object_id,
                item.category,
                item.role == "dynamic",
                item.dimensions_m,
            )
            for item in proposal.objects
        )
        actual_scene_objects = tuple(
            (item.object_id, item.category, item.dynamic, item.dimensions_m)
            for item in scene_ir.objects
        )
        _require_equal(
            (actual_scene_objects, scene_ir.relations),
            (expected_scene_objects, proposal.relations),
            "SceneIR does not exactly preserve interpreted objects and relations",
        )
        _require_equal(
            (task_ir.scene_ir, task_ir.requested_profile, task_ir.goal_relations),
            (compile_output.scene_ir, compile_input.requested_profile, scene_ir.relations),
            "TaskIR does not bind the compiled scene and requested profile",
        )
        _require_equal(
            (compile_output.scene_ir, compile_output.task_ir),
            (environment_package.scene_ir, environment_package.task_ir),
            "compile output and package disagree on SceneIR or TaskIR",
        )
        _require_equal(
            runtime_lock.requested_profile,
            compile_input.requested_profile,
            "runtime lock does not satisfy the requested profile",
        )
        _require_equal(
            asset_catalog.runtime_lock,
            self.expected_runtime_lock,
            "asset catalog does not bind the trusted runtime lock",
        )

        representation_by_ref = dict(zip(representation_refs, representations, strict=True))
        qualification_by_ref = dict(zip(qualification_refs, qualifications, strict=True))
        catalog_by_subject = {item.subject: item for item in asset_catalog.entries}
        for entry in asset_catalog.entries:
            representation = _required(
                representation_by_ref,
                entry.representation,
                "catalog representation is absent from the trusted graph",
            )
            qualification = _required(
                qualification_by_ref,
                entry.qualification,
                "catalog qualification is absent from the trusted graph",
            )
            _require_equal(
                (
                    representation.subject,
                    representation.representation_sha256,
                    representation.loader_closure_sha256,
                    qualification.subject,
                    qualification.representation,
                    qualification.runtime_lock,
                    qualification.requested_profile,
                    qualification.issuer_implementation_sha256,
                    entry.qualification in self.trusted_asset_qualifications,
                ),
                (
                    entry.subject,
                    entry.representation_sha256,
                    entry.qualified_subject.loader_closure_sha256,
                    entry.qualified_subject,
                    entry.representation,
                    self.expected_runtime_lock,
                    compile_input.requested_profile,
                    self.trusted_qualification_issuer_sha256,
                    True,
                ),
                "catalog entry is not backed by trusted complete asset evidence",
            )
            for member in representation.members:
                self._verify_artifact(member.artifact, "catalog asset representation member")
            for check in qualification.checks:
                for check_evidence in check.evidence:
                    self._verify_artifact(check_evidence, "catalog qualification check evidence")
        closure_by_subject = {item.subject: item for item in asset_closure.assets}
        selection_by_object = {item.scene_object_id: item for item in selections}
        selection_ref_by_object = dict(
            zip((item.scene_object_id for item in selections), selection_refs, strict=True)
        )
        _require_equal(
            len(selection_by_object),
            len(selections),
            "asset selections must identify unique scene objects",
        )
        scene_object_ids = tuple(item.object_id for item in scene_ir.objects)
        _require_equal(
            tuple(sorted(selection_by_object)),
            tuple(sorted(scene_object_ids)),
            "every SceneIR object must have exactly one asset selection",
        )

        decision_by_object = {item.object_id: item for item in compile_output.asset_decisions}
        _require_equal(
            len(decision_by_object),
            len(compile_output.asset_decisions),
            "compile asset decisions must identify unique scene objects",
        )
        _require_equal(
            set(decision_by_object),
            set(scene_object_ids),
            "compile asset decisions do not cover the SceneIR",
        )

        for object_id in scene_object_ids:
            selection = selection_by_object[object_id]
            selection_ref = selection_ref_by_object[object_id]
            _require_equal(
                (
                    selection.lane,
                    selection.asset_catalog,
                    selection.asset_closure_manifest,
                ),
                (
                    "exact_asset_reuse",
                    compile_input.asset_catalog,
                    environment_package.asset_closure_manifest,
                ),
                "asset selection is not bound to the exact-reuse compile inputs",
            )
            representation = _required(
                representation_by_ref,
                selection.representation,
                "selected representation is absent from the trusted graph",
            )
            qualification = _required(
                qualification_by_ref,
                selection.qualification,
                "selected qualification is absent from the trusted graph",
            )
            catalog_entry = _required(
                catalog_by_subject,
                selection.catalog_subject,
                "selected asset is absent from the catalog",
            )
            closure_entry = _required(
                closure_by_subject,
                selection.catalog_subject,
                "selected asset is absent from the scene closure",
            )
            _require_equal(
                (
                    representation.subject,
                    representation.representation_sha256,
                    representation.loader_closure_sha256,
                ),
                (
                    selection.catalog_subject,
                    selection.qualified_subject.representation_sha256,
                    selection.qualified_subject.loader_closure_sha256,
                ),
                "selected representation does not match the qualified subject",
            )
            _require_equal(
                (
                    qualification.subject,
                    qualification.representation,
                    qualification.runtime_lock,
                    qualification.requested_profile,
                    qualification.issuer_implementation_sha256,
                    selection.qualification in self.trusted_asset_qualifications,
                ),
                (
                    selection.qualified_subject,
                    selection.representation,
                    self.expected_runtime_lock,
                    compile_input.requested_profile,
                    self.trusted_qualification_issuer_sha256,
                    True,
                ),
                "selected asset qualification is not trusted for this representation",
            )
            required_evidence = {selection.representation, self.expected_runtime_lock}
            _require_equal(
                (
                    {item.code for item in qualification.checks},
                    tuple(
                        required_evidence.issubset(set(item.evidence))
                        for item in qualification.checks
                    ),
                ),
                ({"backend_load", "collision_probe"}, (True, True)),
                "asset qualification checks do not bind load and collision evidence",
            )
            for check in qualification.checks:
                for check_evidence in check.evidence:
                    self._verify_artifact(check_evidence, "asset qualification check evidence")
            _require_equal(
                (
                    catalog_entry.representation,
                    catalog_entry.qualification,
                    catalog_entry.qualified_subject,
                    closure_entry.representation,
                    closure_entry.representation_sha256,
                    closure_entry.loader_closure_sha256,
                    closure_entry.root_logical_path,
                    closure_entry.members,
                ),
                (
                    selection.representation,
                    selection.qualification,
                    selection.qualified_subject,
                    selection.representation,
                    representation.representation_sha256,
                    representation.loader_closure_sha256,
                    representation.root_logical_path,
                    representation.members,
                ),
                "catalog or scene asset closure drifts from the selected representation",
            )
            for member in representation.members:
                self._verify_artifact(member.artifact, "asset representation member")
            decision = decision_by_object[object_id]
            _require_equal(
                (
                    decision.lane,
                    decision.selected_artifact,
                    decision.decision_evidence,
                    decision.source_qualification,
                    decision.digital_cousin_evidence,
                    decision.generation_receipt,
                ),
                (
                    AssetAcquisitionLane.EXACT_ASSET_REUSE,
                    selection.representation,
                    selection_ref,
                    selection.qualification,
                    None,
                    None,
                ),
                "compile asset decision does not mirror the typed selection",
            )

        selected_subjects = {item.catalog_subject for item in selections}
        _require_equal(
            set(closure_by_subject),
            selected_subjects,
            "scene asset closure has missing or extra representations",
        )
        binding_by_object = {item.scene_object_id: item for item in binding_manifest.bindings}
        _require_equal(
            set(binding_by_object),
            set(scene_object_ids),
            "binding manifest does not cover every SceneIR object",
        )
        for object_id, binding in binding_by_object.items():
            selection = selection_by_object[object_id]
            representation = representation_by_ref[selection.representation]
            _require_equal(
                (
                    binding.selection,
                    binding.qualified_subject,
                    binding.representation,
                    binding.qualification,
                    binding.root_logical_path,
                ),
                (
                    selection_ref_by_object[object_id],
                    selection.qualified_subject,
                    selection.representation,
                    selection.qualification,
                    representation.root_logical_path,
                ),
                "object binding does not mirror its selected representation",
            )
        _require_equal(
            (
                binding_manifest.scene_ir,
                binding_manifest.task_ir,
                binding_manifest.asset_catalog,
                binding_manifest.asset_closure_manifest,
                binding_manifest.runtime_lock,
            ),
            (
                compile_output.scene_ir,
                compile_output.task_ir,
                compile_input.asset_catalog,
                environment_package.asset_closure_manifest,
                self.expected_runtime_lock,
            ),
            "binding manifest does not bind the compile evidence graph",
        )

        expected_selection_refs = tuple(sorted(selection_refs, key=lambda item: item.sha256))
        _require_equal(
            (
                static_validation.scene_ir,
                static_validation.task_ir,
                static_validation.asset_catalog,
                static_validation.asset_selections,
                static_validation.asset_closure_manifest,
                static_validation.binding_manifest,
                static_validation.runtime_lock,
                static_validation.status,
            ),
            (
                compile_output.scene_ir,
                compile_output.task_ir,
                compile_input.asset_catalog,
                expected_selection_refs,
                environment_package.asset_closure_manifest,
                environment_package.binding_manifest,
                self.expected_runtime_lock,
                ValidationStatus.PASS,
            ),
            "static validation does not bind the complete compile graph",
        )
        expected_static_checks = {
            "asset_closure_complete": frozenset(
                (*expected_selection_refs, environment_package.asset_closure_manifest)
            ),
            "binding_complete": frozenset((environment_package.binding_manifest,)),
            "runtime_lock_bound": frozenset((self.expected_runtime_lock,)),
            "scene_task_consistent": frozenset((compile_output.scene_ir, compile_output.task_ir)),
        }
        actual_static_checks = {
            check.code: frozenset(check.evidence) for check in static_validation.checks
        }
        _require_equal(
            actual_static_checks,
            expected_static_checks,
            "static validation does not contain the required evidence-bound checks",
        )
        for check in static_validation.checks:
            for check_evidence in check.evidence:
                self._verify_artifact(check_evidence, "static validation check evidence")
        _require_equal(
            compile_output.static_validation,
            compile_receipt.static_validation,
            "compile receipt does not bind static validation",
        )

        _require_equal(
            (
                environment_package,
                environment_package.route_id,
                environment_package.producer_skill_ref,
                environment_package.source_media,
                environment_package.requested_profile,
                environment_package.representation_origin,
                environment_package.legacy_adapter_receipt,
                environment_package.runtime_lock,
                environment_package.simulator_ready,
                environment_package.collision_qualified,
            ),
            (
                compile_output.environment_package,
                "image2env",
                "image2env.compile@1.0.0",
                compile_input.source_media,
                compile_input.requested_profile,
                RepresentationOrigin.HARNESS_NATIVE,
                None,
                self.expected_runtime_lock,
                False,
                False,
            ),
            "environment package is not the exact harness-native compile candidate",
        )
        _require_equal(
            environment_package.package_id,
            x2env_environment_package_sha256(environment_package),
            "environment package_id does not bind its content closure",
        )
        _require_equal(
            compile_output.staged_assets,
            (),
            "exact asset reuse cannot publish staged assets",
        )

        _require_equal(
            (
                compile_receipt.skill_ref,
                compile_receipt.source_media_sha256,
                compile_receipt.interpretation_proposal,
                compile_receipt.asset_catalog,
                compile_receipt.scene_ir,
                compile_receipt.task_ir,
                compile_receipt.asset_selections,
                compile_receipt.asset_closure_manifest,
                compile_receipt.binding_manifest,
                compile_receipt.runtime_lock,
                compile_receipt.environment_package,
                compile_receipt.package_id,
                compile_receipt.predecessor_package,
                compile_receipt.implementation_sha256,
            ),
            (
                "image2env.compile@1.0.0",
                compile_input.source_media.source_media_sha256,
                compile_input.interpretation_proposal,
                compile_input.asset_catalog,
                compile_output.scene_ir,
                compile_output.task_ir,
                expected_selection_refs,
                environment_package.asset_closure_manifest,
                environment_package.binding_manifest,
                self.expected_runtime_lock,
                environment_package_ref,
                environment_package.package_id,
                None,
                self.expected_implementation_sha256,
            ),
            "compile receipt does not bind the exact output evidence graph",
        )

    def _verify_artifact(self, ref: ArtifactRef, label: str) -> None:
        try:
            resolved = self.artifact_store.resolve(ref)
            payload = resolved.path.read_bytes()
        except (ArtifactResolutionError, OSError) as error:
            raise X2EnvCompileClosureError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"{label} is unavailable or corrupt",
            ) from error
        if (  # pragma: no cover - concurrent external CAS tamper defense
            len(payload) != ref.bytes or hashlib.sha256(payload).hexdigest() != ref.sha256
        ):
            raise X2EnvCompileClosureError(
                "HARN_ARTIFACT_UNAVAILABLE",
                f"{label} changed during verified consumption",
            )


def x2env_environment_package_sha256(value: X2EnvEnvironmentPackage) -> str:
    """Return the domain-separated identity of one x2env package closure."""

    if type(value) is not X2EnvEnvironmentPackage:
        raise TypeError("value must be the exact X2EnvEnvironmentPackage")
    payload = value.model_dump(mode="json")
    payload.pop("package_id")
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("utf-8")


def _require_ref(ref: ArtifactRef, field: str, schema_version: str) -> None:
    if ref.uri != f"artifact://sha256/{ref.sha256}":
        raise ValueError(f"{field} must use its content-addressed artifact URI")
    if ref.media_type != "application/json" or ref.schema_version != schema_version:
        raise ValueError(f"{field} must identify {schema_version} JSON")


def _unique_refs(values: Iterable[ArtifactRef]) -> tuple[ArtifactRef, ...]:
    by_digest = {item.sha256: item for item in values}
    return tuple(by_digest[digest] for digest in sorted(by_digest))


def _required(
    values: Mapping[_KeyT, _ValueT],
    key: _KeyT,
    message: str,
) -> _ValueT:
    value = values.get(key)
    if value is None:
        _mismatch(message)
    return value


def _require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        _mismatch(message)


def _mismatch(message: str) -> None:
    raise X2EnvCompileClosureError("HARN_X2ENV_COMPILE_EVIDENCE_MISMATCH", message)


__all__ = [
    "ExactReuseCompileClosureVerifier",
    "VerifiedExactReuseCompileClosure",
    "X2EnvCompileClosureError",
    "x2env_environment_package_sha256",
]
