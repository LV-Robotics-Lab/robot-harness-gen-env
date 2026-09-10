from __future__ import annotations

import hashlib
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import self_improving.harness as harness
import self_improving.harness.schemas as public_schemas
from self_improving.harness.schema_catalog import schema_documents
from self_improving.harness.schemas.common import ArtifactRef

try:
    from self_improving.harness.schemas import x2env_compile_evidence as evidence
except ImportError:
    evidence = None  # type: ignore[assignment]


EXPECTED_MODELS = (
    "AssetClosureManifestV1",
    "AssetRepresentationV1",
    "GenesisAssetQualificationV1",
    "GenesisRuntimeLockV1",
    "PortableAssetCatalogV1",
    "SceneIRV1",
    "SceneInterpretationProposalV1",
    "TaskIRV1",
    "X2EnvAssetSelectionV1",
    "X2EnvBindingManifestV1",
    "X2EnvCompileReceiptV1",
    "X2EnvStaticValidationV1",
)


def test_compile_evidence_models_are_public_and_exact() -> None:
    assert evidence is not None, "x2env compile evidence schemas are not implemented"
    for name in EXPECTED_MODELS:
        model = getattr(evidence, name, None)
        assert model is not None
        assert getattr(public_schemas, name) is model
        assert getattr(harness, name) is model
    assert public_schemas.asset_loader_closure_sha256 is evidence.asset_loader_closure_sha256
    assert harness.asset_loader_closure_sha256 is evidence.asset_loader_closure_sha256


@pytest.fixture
def documents() -> dict[str, object]:
    assert evidence is not None

    def ref(name: str, schema: str | None, media_type: str = "application/json") -> ArtifactRef:
        digest = hashlib.sha256(f"{name}:{schema}".encode()).hexdigest()
        return ArtifactRef(
            name=name,
            uri=f"artifact://sha256/{digest}",
            media_type=media_type,
            sha256=digest,
            bytes=37,
            schema_version=schema,
        )

    def build(model: type[object], digest_field: str, payload: dict[str, object]) -> object:
        payload[digest_field] = evidence.canonical_evidence_sha256(payload, digest_field)
        return model.model_validate(payload)  # type: ignore[attr-defined]

    source_sha = "1" * 64
    prompt = ref("effective-prompt", "harness.prompt_text.v1", "text/plain")
    proposal = build(
        evidence.SceneInterpretationProposalV1,
        "proposal_sha256",
        {
            "schema_version": "harness.scene_interpretation_proposal.v1",
            "route_id": "image2env",
            "source_media_sha256": source_sha,
            "effective_prompt": prompt,
            "objects": [
                {
                    "object_id": "can",
                    "category": "can",
                    "role": "dynamic",
                    "dimensions_m": [0.07, 0.07, 0.12],
                }
            ],
            "relations": [{"relation": "on_table", "source": "can", "target": "table"}],
            "unknowns": [],
        },
    )
    proposal_ref = ref("proposal", "harness.scene_interpretation_proposal.v1")
    scene = build(
        evidence.SceneIRV1,
        "scene_ir_sha256",
        {
            "schema_version": "harness.scene_ir.v1",
            "interpretation_proposal": proposal_ref,
            "frame": {
                "name": "genesis_world",
                "handedness": "right_handed",
                "up_axis": "z",
                "unit": "m",
            },
            "objects": [
                {
                    "object_id": "can",
                    "category": "can",
                    "dynamic": True,
                    "collision_required": True,
                    "dimensions_m": [0.07, 0.07, 0.12],
                }
            ],
            "relations": [{"relation": "on_table", "source": "can", "target": "table"}],
        },
    )
    scene_ref = ref("scene", "harness.scene_ir.v1")
    task = build(
        evidence.TaskIRV1,
        "task_ir_sha256",
        {
            "schema_version": "harness.task_ir.v1",
            "scene_ir": scene_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "goal_relations": [{"relation": "on_table", "source": "can", "target": "table"}],
            "reset_required": True,
            "action_contract": "none",
            "observation_keys": ["observation.scene_visible"],
            "termination_steps": 120,
        },
    )
    task_ref = ref("task", "harness.task_ir.v1")
    subject = {
        "namespace": "robotwin",
        "asset_key": "071_can",
        "ledger_asset_id": "071_can",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "2" * 64,
    }
    members = [
        {
            "logical_path": "071_can/collision/base.glb",
            "role": "collision",
            "artifact": ref("collision", None, "model/gltf-binary"),
        },
        {
            "logical_path": "071_can/model_data0.json",
            "role": "metadata",
            "artifact": ref("metadata", None),
        },
        {
            "logical_path": "071_can/visual/base.glb",
            "role": "visual",
            "artifact": ref("visual", None, "model/gltf-binary"),
        },
    ]
    loader_closure_sha = evidence.asset_loader_closure_sha256(
        subject,
        "071_can/collision/base.glb",
        members,
    )
    representation = build(
        evidence.AssetRepresentationV1,
        "representation_sha256",
        {
            "schema_version": "harness.asset_representation.v1",
            "subject": subject,
            "representation_kind": "rigid_mesh",
            "root_logical_path": "071_can/collision/base.glb",
            "loader_scale": [1.0, 1.0, 1.0],
            "loader_closure_sha256": loader_closure_sha,
            "members": members,
            "collision_member_paths": ["071_can/collision/base.glb"],
            "visual_member_paths": ["071_can/visual/base.glb"],
        },
    )
    representation_ref = ref("representation", "harness.asset_representation.v1")
    closure = build(
        evidence.AssetClosureManifestV1,
        "closure_sha256",
        {
            "schema_version": "harness.asset_closure_manifest.v1",
            "assets": [
                {
                    "subject": subject,
                    "representation": representation_ref,
                    "representation_sha256": representation.representation_sha256,
                    "loader_closure_sha256": loader_closure_sha,
                    "root_logical_path": "071_can/collision/base.glb",
                    "members": members,
                }
            ],
        },
    )
    closure_ref = ref("closure", "harness.asset_closure_manifest.v1")
    runtime_lock = build(
        evidence.GenesisRuntimeLockV1,
        "runtime_lock_sha256",
        {
            "schema_version": "harness.genesis_runtime_lock.v1",
            "backend": "genesis",
            "backend_version": "0.3.3",
            "runtime_implementation_sha256": "3" * 64,
            "loader_contract": "harness.genesis_rigid_loader.v1",
            "requested_profile": "genesis.rigid_scene@1",
            "dependencies": [{"name": "genesis", "version": "0.3.3", "sha256": "4" * 64}],
        },
    )
    runtime_lock_ref = ref("runtime-lock", "harness.genesis_runtime_lock.v1")
    qualified_subject = {
        "model_subject": subject,
        "representation_sha256": representation.representation_sha256,
        "loader_closure_sha256": loader_closure_sha,
    }
    qualification = build(
        evidence.GenesisAssetQualificationV1,
        "qualification_sha256",
        {
            "schema_version": "harness.asset_qualification.v1",
            "subject": qualified_subject,
            "representation": representation_ref,
            "runtime_lock": runtime_lock_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "issuer_implementation_sha256": "5" * 64,
            "checks": [{"code": "collision_load", "status": "pass", "evidence": [closure_ref]}],
            "status": "pass",
            "backend_load_executed": True,
            "collision_probe_executed": True,
            "qualified": True,
        },
    )
    qualification_ref = ref("qualification", "harness.asset_qualification.v1")
    catalog = build(
        evidence.PortableAssetCatalogV1,
        "catalog_sha256",
        {
            "schema_version": "harness.portable_asset_catalog.v1",
            "runtime_lock": runtime_lock_ref,
            "entries": [
                {
                    "subject": subject,
                    "semantic_name": "can",
                    "aliases": ["tin_can"],
                    "representation": representation_ref,
                    "representation_sha256": representation.representation_sha256,
                    "qualification": qualification_ref,
                    "qualified_subject": qualified_subject,
                    "available": True,
                }
            ],
        },
    )
    catalog_ref = ref("catalog", "harness.portable_asset_catalog.v1")
    selection = build(
        evidence.X2EnvAssetSelectionV1,
        "selection_sha256",
        {
            "schema_version": "harness.x2env_asset_selection.v1",
            "scene_object_id": "can",
            "lane": "exact_asset_reuse",
            "asset_catalog": catalog_ref,
            "catalog_subject": subject,
            "qualified_subject": qualified_subject,
            "representation": representation_ref,
            "qualification": qualification_ref,
            "asset_closure_manifest": closure_ref,
            "selected": True,
            "decision_reason": "exact identity and current Genesis qualification",
        },
    )
    selection_ref = ref("selection", "harness.x2env_asset_selection.v1")
    binding = build(
        evidence.X2EnvBindingManifestV1,
        "binding_sha256",
        {
            "schema_version": "harness.x2env_binding_manifest.v1",
            "scene_ir": scene_ref,
            "task_ir": task_ref,
            "asset_catalog": catalog_ref,
            "asset_closure_manifest": closure_ref,
            "runtime_lock": runtime_lock_ref,
            "bindings": [
                {
                    "scene_object_id": "can",
                    "selection": selection_ref,
                    "qualified_subject": qualified_subject,
                    "representation": representation_ref,
                    "qualification": qualification_ref,
                    "root_logical_path": "071_can/collision/base.glb",
                    "position_m": [0.0, 0.0, 0.8],
                    "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                }
            ],
        },
    )
    binding_ref = ref("binding", "harness.x2env_binding_manifest.v1")
    validation = build(
        evidence.X2EnvStaticValidationV1,
        "validation_sha256",
        {
            "schema_version": "harness.x2env_static_validation.v1",
            "scene_ir": scene_ref,
            "task_ir": task_ref,
            "asset_catalog": catalog_ref,
            "asset_selections": [selection_ref],
            "asset_closure_manifest": closure_ref,
            "binding_manifest": binding_ref,
            "runtime_lock": runtime_lock_ref,
            "status": "pass",
            "checks": [{"code": "binding_complete", "status": "pass", "evidence": [binding_ref]}],
            "blockers": [],
            "simulator_executed": False,
            "physical_validation_claimed": False,
            "publishable": False,
        },
    )
    validation_ref = ref("static-validation", "harness.x2env_static_validation.v1")
    package_ref = ref("package", "harness.x2env_environment_package.v1")
    receipt = build(
        evidence.X2EnvCompileReceiptV1,
        "receipt_sha256",
        {
            "schema_version": "harness.x2env_compile_receipt.v1",
            "skill_ref": "image2env.compile@1.0.0",
            "source_media_sha256": source_sha,
            "interpretation_proposal": proposal_ref,
            "asset_catalog": catalog_ref,
            "scene_ir": scene_ref,
            "task_ir": task_ref,
            "asset_selections": [selection_ref],
            "asset_closure_manifest": closure_ref,
            "binding_manifest": binding_ref,
            "runtime_lock": runtime_lock_ref,
            "static_validation": validation_ref,
            "environment_package": package_ref,
            "package_id": "6" * 64,
            "predecessor_package": None,
            "implementation_sha256": "7" * 64,
            "simulator_executed": False,
            "publishable": False,
        },
    )
    return {
        "proposal": proposal,
        "scene": scene,
        "task": task,
        "representation": representation,
        "closure": closure,
        "runtime_lock": runtime_lock,
        "qualification": qualification,
        "catalog": catalog,
        "selection": selection,
        "binding": binding,
        "validation": validation,
        "receipt": receipt,
    }


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_compile_evidence_positive_documents_are_strict_and_roundtrip(
    documents: dict[str, object],
) -> None:
    assert len(documents) == 12
    catalog = schema_documents()
    for value in documents.values():
        model = type(value)
        dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
        assert model.model_validate_json(json.dumps(dumped)).model_dump(mode="json") == dumped
        Draft202012Validator(catalog[model.model_json_schema()["$id"]]).validate(dumped)
        with pytest.raises(ValidationError, match="extra"):
            model.model_validate({**dumped, "extra": True})


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_every_top_level_document_rejects_digest_drift(documents: dict[str, object]) -> None:
    for value in documents.values():
        dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
        digest_field = next(key for key in dumped if key.endswith("_sha256"))
        with pytest.raises(ValidationError, match="digest is inconsistent"):
            type(value).model_validate({**dumped, digest_field: "f" * 64})


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_canonical_order_and_local_subject_invariants_are_fail_closed(
    documents: dict[str, object],
) -> None:
    representation = documents["representation"].model_dump(mode="json")  # type: ignore[attr-defined]
    representation["members"] = list(reversed(representation["members"]))
    representation["representation_sha256"] = evidence.canonical_evidence_sha256(
        representation, "representation_sha256"
    )
    with pytest.raises(ValidationError, match="sorted and unique"):
        evidence.AssetRepresentationV1.model_validate(representation)

    catalog = documents["catalog"].model_dump(mode="json")  # type: ignore[attr-defined]
    catalog["entries"][0]["qualified_subject"]["representation_sha256"] = "f" * 64
    catalog["catalog_sha256"] = evidence.canonical_evidence_sha256(catalog, "catalog_sha256")
    with pytest.raises(ValidationError, match="qualified subject"):
        evidence.PortableAssetCatalogV1.model_validate(catalog)

    scene = documents["scene"].model_dump(mode="json")  # type: ignore[attr-defined]
    scene["relations"][0]["source"] = "missing"
    scene["scene_ir_sha256"] = evidence.canonical_evidence_sha256(scene, "scene_ir_sha256")
    with pytest.raises(ValidationError, match="unknown relation source"):
        evidence.SceneIRV1.model_validate(scene)


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_static_and_compile_receipts_cannot_claim_runtime_authority(
    documents: dict[str, object],
) -> None:
    validation = documents["validation"].model_dump(mode="json")  # type: ignore[attr-defined]
    validation["simulator_executed"] = True
    with pytest.raises(ValidationError):
        evidence.X2EnvStaticValidationV1.model_validate(validation)
    receipt = documents["receipt"].model_dump(mode="json")  # type: ignore[attr-defined]
    receipt["publishable"] = True
    with pytest.raises(ValidationError):
        evidence.X2EnvCompileReceiptV1.model_validate(receipt)


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_canonical_digest_and_artifact_reference_failures_are_exact(
    documents: dict[str, object],
) -> None:
    assert evidence.canonical_evidence_sha256(
        {"profile": evidence.RequestedGenesisProfile.RIGID_SCENE}, "record_sha256"
    )
    with pytest.raises(TypeError, match="digest_field"):
        evidence.canonical_evidence_sha256({}, 1)
    with pytest.raises(TypeError, match="digest_field"):
        evidence.canonical_evidence_sha256({}, "digest")
    with pytest.raises(TypeError, match="model or dict"):
        evidence.canonical_evidence_sha256([], "record_sha256")

    for field, replacement, message in (
        ("uri", "artifact://sha256/" + "f" * 64, "content-addressed"),
        ("schema_version", "harness.other.v1", "schema_version"),
        ("media_type", "application/json", "media_type"),
    ):
        proposal = documents["proposal"].model_dump(mode="json")  # type: ignore[attr-defined]
        proposal["effective_prompt"][field] = replacement
        proposal["proposal_sha256"] = evidence.canonical_evidence_sha256(
            proposal, "proposal_sha256"
        )
        with pytest.raises(ValidationError, match=message):
            evidence.SceneInterpretationProposalV1.model_validate(proposal)


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_scene_relation_and_profile_invariants_are_fail_closed(
    documents: dict[str, object],
) -> None:
    for relation, source, target, message in (
        ("near", "can", "can", "source and target"),
        ("near", "can", "table", "only on_table"),
        ("on_table", "can", "other", "only on_table"),
    ):
        with pytest.raises(ValidationError, match=message):
            evidence.SceneRelationV1(relation=relation, source=source, target=target)

    for key, value, message in (
        ("source", "missing", "unknown relation source"),
        ("target", "missing", "unknown relation target"),
    ):
        proposal = documents["proposal"].model_dump(mode="json")  # type: ignore[attr-defined]
        proposal["relations"][0][key] = value
        if key == "target":
            proposal["relations"][0]["relation"] = "near"
        proposal["proposal_sha256"] = evidence.canonical_evidence_sha256(
            proposal, "proposal_sha256"
        )
        with pytest.raises(ValidationError, match=message):
            evidence.SceneInterpretationProposalV1.model_validate(proposal)

    scene = documents["scene"].model_dump(mode="json")  # type: ignore[attr-defined]
    scene["relations"][0] = {"relation": "near", "source": "can", "target": "missing"}
    scene["scene_ir_sha256"] = evidence.canonical_evidence_sha256(scene, "scene_ir_sha256")
    with pytest.raises(ValidationError, match="unknown relation target"):
        evidence.SceneIRV1.model_validate(scene)

    task = documents["task"].model_dump(mode="json")  # type: ignore[attr-defined]
    task["requested_profile"] = "genesis.robot_policy@1"
    task["task_ir_sha256"] = evidence.canonical_evidence_sha256(task, "task_ir_sha256")
    with pytest.raises(ValidationError, match="action_contract"):
        evidence.TaskIRV1.model_validate(task)


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_representation_closure_and_subject_drift_are_fail_closed(
    documents: dict[str, object],
) -> None:
    for field, value, message in (
        ("root_logical_path", "071_can/missing.glb", "root_logical_path"),
        ("collision_member_paths", ["071_can/missing.glb"], "collision_member_paths"),
        ("visual_member_paths", ["071_can/missing.glb"], "visual_member_paths"),
        ("loader_closure_sha256", "f" * 64, "loader_closure_sha256"),
    ):
        representation = documents["representation"].model_dump(mode="json")  # type: ignore[attr-defined]
        representation[field] = value
        representation["representation_sha256"] = evidence.canonical_evidence_sha256(
            representation, "representation_sha256"
        )
        with pytest.raises(ValidationError, match=message):
            evidence.AssetRepresentationV1.model_validate(representation)

    closure = documents["closure"].model_dump(mode="json")  # type: ignore[attr-defined]
    closure["assets"][0]["root_logical_path"] = "071_can/missing.glb"
    closure["closure_sha256"] = evidence.canonical_evidence_sha256(closure, "closure_sha256")
    with pytest.raises(ValidationError, match="closure member"):
        evidence.AssetClosureManifestV1.model_validate(closure)

    closure = documents["closure"].model_dump(mode="json")  # type: ignore[attr-defined]
    second = json.loads(json.dumps(closure["assets"][0]))
    second["subject"]["asset_key"] = "072_can"
    second["subject"]["ledger_asset_id"] = "072_can"
    second["loader_closure_sha256"] = evidence.asset_loader_closure_sha256(
        second["subject"], second["root_logical_path"], second["members"]
    )
    closure["assets"].append(second)
    closure["closure_sha256"] = evidence.canonical_evidence_sha256(closure, "closure_sha256")
    with pytest.raises(ValidationError, match="globally unique"):
        evidence.AssetClosureManifestV1.model_validate(closure)

    closure = documents["closure"].model_dump(mode="json")  # type: ignore[attr-defined]
    closure["assets"][0]["loader_closure_sha256"] = "f" * 64
    closure["closure_sha256"] = evidence.canonical_evidence_sha256(closure, "closure_sha256")
    with pytest.raises(ValidationError, match="loader_closure_sha256"):
        evidence.AssetClosureManifestV1.model_validate(closure)

    catalog = documents["catalog"].model_dump(mode="json")  # type: ignore[attr-defined]
    catalog["entries"][0]["qualified_subject"]["model_subject"]["asset_key"] = "other"
    catalog["catalog_sha256"] = evidence.canonical_evidence_sha256(catalog, "catalog_sha256")
    with pytest.raises(ValidationError, match="catalog subject"):
        evidence.PortableAssetCatalogV1.model_validate(catalog)

    selection = documents["selection"].model_dump(mode="json")  # type: ignore[attr-defined]
    selection["qualified_subject"]["model_subject"]["asset_key"] = "other"
    selection["selection_sha256"] = evidence.canonical_evidence_sha256(
        selection, "selection_sha256"
    )
    with pytest.raises(ValidationError, match="catalog_subject"):
        evidence.X2EnvAssetSelectionV1.model_validate(selection)


@pytest.mark.skipif(evidence is None, reason="x2env compile evidence schemas are not implemented")
def test_runtime_binding_validation_and_receipt_edges_are_fail_closed(
    documents: dict[str, object],
) -> None:
    runtime = documents["runtime_lock"].model_dump(mode="json")  # type: ignore[attr-defined]
    runtime["dependencies"].append(dict(runtime["dependencies"][0]))
    runtime["runtime_lock_sha256"] = evidence.canonical_evidence_sha256(
        runtime, "runtime_lock_sha256"
    )
    with pytest.raises(ValidationError, match="dependencies"):
        evidence.GenesisRuntimeLockV1.model_validate(runtime)

    binding = documents["binding"].model_dump(mode="json")  # type: ignore[attr-defined]
    binding["bindings"][0]["orientation_wxyz"] = [2.0, 0.0, 0.0, 0.0]
    binding["binding_sha256"] = evidence.canonical_evidence_sha256(binding, "binding_sha256")
    with pytest.raises(ValidationError, match="unit quaternion"):
        evidence.X2EnvBindingManifestV1.model_validate(binding)

    validation = documents["validation"].model_dump(mode="json")  # type: ignore[attr-defined]
    validation["checks"][0]["status"] = "fail"
    validation["validation_sha256"] = evidence.canonical_evidence_sha256(
        validation, "validation_sha256"
    )
    with pytest.raises(ValidationError, match="pass static validation"):
        evidence.X2EnvStaticValidationV1.model_validate(validation)

    validation = documents["validation"].model_dump(mode="json")  # type: ignore[attr-defined]
    validation["status"] = "incomplete"
    validation["validation_sha256"] = evidence.canonical_evidence_sha256(
        validation, "validation_sha256"
    )
    with pytest.raises(ValidationError, match="requires blockers"):
        evidence.X2EnvStaticValidationV1.model_validate(validation)
    validation["checks"][0]["status"] = "incomplete"
    validation["blockers"] = [
        {
            "code": "HARN_STATIC_INCOMPLETE",
            "message": "runtime evidence is not part of compile",
            "stage": "compile.static_validation",
            "retryable": False,
            "details": {},
            "unknowns": [],
            "artifact_refs": [],
        }
    ]
    validation["validation_sha256"] = evidence.canonical_evidence_sha256(
        validation, "validation_sha256"
    )
    assert evidence.X2EnvStaticValidationV1.model_validate(validation).status.value == "incomplete"

    receipt = documents["receipt"].model_dump(mode="json")  # type: ignore[attr-defined]
    receipt["skill_ref"] = "text2env.compile@1.0.0"
    receipt["receipt_sha256"] = evidence.canonical_evidence_sha256(receipt, "receipt_sha256")
    with pytest.raises(ValidationError, match="exact x2env compile"):
        evidence.X2EnvCompileReceiptV1.model_validate(receipt)

    receipt = documents["receipt"].model_dump(mode="json")  # type: ignore[attr-defined]
    receipt["predecessor_package"] = dict(receipt["environment_package"])
    receipt["receipt_sha256"] = evidence.canonical_evidence_sha256(receipt, "receipt_sha256")
    assert evidence.X2EnvCompileReceiptV1.model_validate(receipt).predecessor_package is not None
