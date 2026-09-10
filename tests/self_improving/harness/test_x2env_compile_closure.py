from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

import self_improving.harness as harness
from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.image_ingest import ImageMediaIngestor
from self_improving.harness.schemas import x2env_compile_evidence as evidence
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileOutput,
    Image2EnvCompileV2Input,
    X2EnvAssetDecision,
    X2EnvEnvironmentPackage,
)

try:
    from self_improving.harness.x2env_compile_closure import (
        ExactReuseCompileClosureVerifier,
        VerifiedExactReuseCompileClosure,
        X2EnvCompileClosureError,
        x2env_environment_package_sha256,
    )
except ImportError:
    ExactReuseCompileClosureVerifier = None  # type: ignore[assignment,misc]
    VerifiedExactReuseCompileClosure = None  # type: ignore[assignment,misc]
    X2EnvCompileClosureError = None  # type: ignore[assignment,misc]
    x2env_environment_package_sha256 = None  # type: ignore[assignment]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()


def _put(
    store: LocalArtifactStore,
    root: Path,
    *,
    name: str,
    payload: bytes,
    media_type: str,
    schema_version: str | None,
) -> ArtifactRef:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _publish_model(store: LocalArtifactStore, root: Path, name: str, value: object) -> ArtifactRef:
    return _put(
        store,
        root,
        name=f"{name}.json",
        payload=_canonical_json(value.model_dump(mode="json")),  # type: ignore[attr-defined]
        media_type="application/json",
        schema_version=value.schema_version,  # type: ignore[attr-defined]
    )


def _build(model: type[object], digest_field: str, value: dict[str, object]) -> object:
    value[digest_field] = evidence.canonical_evidence_sha256(value, digest_field)
    return model.model_validate(value)  # type: ignore[attr-defined]


def _sorted_refs(*refs: ArtifactRef) -> list[ArtifactRef]:
    return sorted(refs, key=lambda item: (item.sha256, item.bytes, item.media_type))


@dataclass(frozen=True, slots=True)
class _Case:
    store: LocalArtifactStore
    compile_input: Image2EnvCompileV2Input
    compile_output: Image2EnvCompileOutput
    verifier: object
    refs: dict[str, ArtifactRef]


def _case(tmp_path: Path) -> _Case:
    store = LocalArtifactStore(tmp_path / "cas")
    staging = tmp_path / "staging"
    image_path = staging / "source.png"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (3, 2), (12, 34, 56)).save(image_path, format="PNG")
    source = store.put_file(
        image_path,
        name="source.png",
        media_type="image/png",
        schema_version=None,
    )
    media = ImageMediaIngestor(store, 4096, 4096).ingest(source)
    prompt_ref = _put(
        store,
        staging,
        name="prompt.txt",
        payload=b"Put one can on the table.\n",
        media_type="text/plain",
        schema_version="harness.prompt_text.v1",
    )
    proposal = _build(
        evidence.SceneInterpretationProposalV1,
        "proposal_sha256",
        {
            "schema_version": "harness.scene_interpretation_proposal.v1",
            "route_id": "image2env",
            "source_media_sha256": source.sha256,
            "effective_prompt": prompt_ref,
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
    proposal_ref = _publish_model(store, staging, "proposal", proposal)
    scene = _build(
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
    scene_ref = _publish_model(store, staging, "scene", scene)
    task = _build(
        evidence.TaskIRV1,
        "task_ir_sha256",
        {
            "schema_version": "harness.task_ir.v1",
            "scene_ir": scene_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "goal_relations": scene.relations,  # type: ignore[attr-defined]
            "reset_required": True,
            "action_contract": "none",
            "observation_keys": ["observation.scene_visible"],
            "termination_steps": 120,
        },
    )
    task_ref = _publish_model(store, staging, "task", task)
    runtime = _build(
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
    runtime_ref = _publish_model(store, staging, "runtime-lock", runtime)
    subject = {
        "namespace": "robotwin",
        "asset_key": "071_can",
        "ledger_asset_id": "071_can",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "5" * 64,
    }
    member_refs = (
        _put(
            store,
            staging,
            name="071_can/collision/base.glb",
            payload=b"collision-mesh",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
        _put(
            store,
            staging,
            name="071_can/model_data0.json",
            payload=b'{"scale":[1,1,1]}\n',
            media_type="application/json",
            schema_version=None,
        ),
        _put(
            store,
            staging,
            name="071_can/visual/base.glb",
            payload=b"visual-mesh",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
    )
    members = [
        {
            "logical_path": "071_can/collision/base.glb",
            "role": "collision",
            "artifact": member_refs[0],
        },
        {
            "logical_path": "071_can/model_data0.json",
            "role": "metadata",
            "artifact": member_refs[1],
        },
        {"logical_path": "071_can/visual/base.glb", "role": "visual", "artifact": member_refs[2]},
    ]
    loader_sha = evidence.asset_loader_closure_sha256(
        subject, "071_can/collision/base.glb", members
    )
    representation = _build(
        evidence.AssetRepresentationV1,
        "representation_sha256",
        {
            "schema_version": "harness.asset_representation.v1",
            "subject": subject,
            "representation_kind": "rigid_mesh",
            "root_logical_path": "071_can/collision/base.glb",
            "loader_scale": [1.0, 1.0, 1.0],
            "loader_closure_sha256": loader_sha,
            "members": members,
            "collision_member_paths": ["071_can/collision/base.glb"],
            "visual_member_paths": ["071_can/visual/base.glb"],
        },
    )
    representation_ref = _publish_model(store, staging, "representation", representation)
    qualified_subject = {
        "model_subject": subject,
        "representation_sha256": representation.representation_sha256,  # type: ignore[attr-defined]
        "loader_closure_sha256": loader_sha,
    }
    qualification = _build(
        evidence.GenesisAssetQualificationV1,
        "qualification_sha256",
        {
            "schema_version": "harness.asset_qualification.v1",
            "subject": qualified_subject,
            "representation": representation_ref,
            "runtime_lock": runtime_ref,
            "requested_profile": "genesis.rigid_scene@1",
            "issuer_implementation_sha256": "6" * 64,
            "checks": [
                {
                    "code": code,
                    "status": "pass",
                    "evidence": sorted(
                        [runtime_ref, representation_ref],
                        key=lambda item: (item.sha256, item.bytes, item.media_type),
                    ),
                }
                for code in ("backend_load", "collision_probe")
            ],
            "status": "pass",
            "backend_load_executed": True,
            "collision_probe_executed": True,
            "qualified": True,
        },
    )
    qualification_ref = _publish_model(store, staging, "qualification", qualification)
    catalog = _build(
        evidence.PortableAssetCatalogV1,
        "catalog_sha256",
        {
            "schema_version": "harness.portable_asset_catalog.v1",
            "runtime_lock": runtime_ref,
            "entries": [
                {
                    "subject": subject,
                    "semantic_name": "can",
                    "aliases": ["tin_can"],
                    "representation": representation_ref,
                    "representation_sha256": representation.representation_sha256,  # type: ignore[attr-defined]
                    "qualification": qualification_ref,
                    "qualified_subject": qualified_subject,
                    "available": True,
                }
            ],
        },
    )
    catalog_ref = _publish_model(store, staging, "catalog", catalog)
    closure = _build(
        evidence.AssetClosureManifestV1,
        "closure_sha256",
        {
            "schema_version": "harness.asset_closure_manifest.v1",
            "assets": [
                {
                    "subject": subject,
                    "representation": representation_ref,
                    "representation_sha256": representation.representation_sha256,  # type: ignore[attr-defined]
                    "loader_closure_sha256": loader_sha,
                    "root_logical_path": "071_can/collision/base.glb",
                    "members": members,
                }
            ],
        },
    )
    closure_ref = _publish_model(store, staging, "closure", closure)
    selection = _build(
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
            "decision_reason": "exact subject has a current trusted Genesis qualification",
        },
    )
    selection_ref = _publish_model(store, staging, "selection", selection)
    binding = _build(
        evidence.X2EnvBindingManifestV1,
        "binding_sha256",
        {
            "schema_version": "harness.x2env_binding_manifest.v1",
            "scene_ir": scene_ref,
            "task_ir": task_ref,
            "asset_catalog": catalog_ref,
            "asset_closure_manifest": closure_ref,
            "runtime_lock": runtime_ref,
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
    binding_ref = _publish_model(store, staging, "binding", binding)
    static_validation = _build(
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
            "runtime_lock": runtime_ref,
            "status": "pass",
            "checks": [
                {
                    "code": "asset_closure_complete",
                    "status": "pass",
                    "evidence": _sorted_refs(selection_ref, closure_ref),
                },
                {"code": "binding_complete", "status": "pass", "evidence": [binding_ref]},
                {"code": "runtime_lock_bound", "status": "pass", "evidence": [runtime_ref]},
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
    static_ref = _publish_model(store, staging, "static-validation", static_validation)
    package = X2EnvEnvironmentPackage(
        schema_version="harness.x2env_environment_package.v1",
        package_id="0" * 64,
        route_id="image2env",
        producer_skill_ref="image2env.compile@1.0.0",
        source_media=media,
        requested_profile="genesis.rigid_scene@1",
        environment_format="harness.genesis_portable.v1",
        backend="genesis",
        representation_origin="harness_native",
        legacy_adapter_receipt=None,
        scene_ir=scene_ref,
        task_ir=task_ref,
        asset_closure_manifest=closure_ref,
        binding_manifest=binding_ref,
        runtime_lock=runtime_ref,
        simulator_ready=False,
        collision_qualified=False,
    )
    package = package.model_copy(update={"package_id": x2env_environment_package_sha256(package)})
    package_ref = _publish_model(store, staging, "package", package)
    receipt = _build(
        evidence.X2EnvCompileReceiptV1,
        "receipt_sha256",
        {
            "schema_version": "harness.x2env_compile_receipt.v1",
            "skill_ref": "image2env.compile@1.0.0",
            "source_media_sha256": source.sha256,
            "interpretation_proposal": proposal_ref,
            "asset_catalog": catalog_ref,
            "scene_ir": scene_ref,
            "task_ir": task_ref,
            "asset_selections": [selection_ref],
            "asset_closure_manifest": closure_ref,
            "binding_manifest": binding_ref,
            "runtime_lock": runtime_ref,
            "static_validation": static_ref,
            "environment_package": package_ref,
            "package_id": package.package_id,
            "predecessor_package": None,
            "implementation_sha256": "7" * 64,
            "simulator_executed": False,
            "publishable": False,
        },
    )
    receipt_ref = _publish_model(store, staging, "compile-receipt", receipt)
    decision = X2EnvAssetDecision(
        schema_version="harness.x2env_asset_decision.v1",
        object_id="can",
        lane="exact_asset_reuse",
        selected_artifact=representation_ref,
        decision_evidence=selection_ref,
        source_qualification=qualification_ref,
        digital_cousin_evidence=None,
        generation_receipt=None,
    )
    compile_input = Image2EnvCompileV2Input(
        schema_version="harness.image2env_compile_input.v2",
        source_media=media,
        interpretation_proposal=proposal_ref,
        requested_profile="genesis.rigid_scene@1",
        acquisition_policy="exact_only",
        asset_catalog=catalog_ref,
        seed=7,
        predecessor_package=None,
        repair_evidence_refs=(),
        model_execution_allowed=False,
        attempt={"schema_version": "harness.initial_compile_attempt.v1", "kind": "initial"},
    )
    compile_output = Image2EnvCompileOutput(
        schema_version="harness.image2env_compile_output.v1",
        environment_package=package,
        scene_ir=scene_ref,
        task_ir=task_ref,
        asset_decisions=(decision,),
        staged_assets=(),
        static_validation=static_ref,
        compile_receipt=receipt_ref,
        simulator_executed=False,
        publishable=False,
    )
    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=store,
        max_source_bytes=4096,
        max_decoded_pixels=4096,
        expected_implementation_sha256="7" * 64,
        trusted_qualification_issuer_sha256="6" * 64,
        expected_runtime_lock=runtime_ref,
        trusted_asset_qualifications=(qualification_ref,),
    )
    return _Case(
        store=store,
        compile_input=compile_input,
        compile_output=compile_output,
        verifier=verifier,
        refs={
            "proposal": proposal_ref,
            "scene": scene_ref,
            "task": task_ref,
            "runtime": runtime_ref,
            "representation": representation_ref,
            "qualification": qualification_ref,
            "catalog": catalog_ref,
            "closure": closure_ref,
            "selection": selection_ref,
            "binding": binding_ref,
            "static": static_ref,
            "package": package_ref,
            "receipt": receipt_ref,
        },
    )


def _ref(digest: str = "1" * 64) -> ArtifactRef:
    return ArtifactRef(
        name="runtime-lock",
        uri=f"artifact://sha256/{digest}",
        media_type="application/json",
        sha256=digest,
        bytes=37,
        schema_version="harness.genesis_runtime_lock.v1",
    )


def test_exact_reuse_compile_closure_public_seam_is_frozen(tmp_path: Path) -> None:
    assert ExactReuseCompileClosureVerifier is not None, "deep compile verifier is not implemented"
    assert VerifiedExactReuseCompileClosure is not None
    assert X2EnvCompileClosureError is not None
    assert harness.ExactReuseCompileClosureVerifier is ExactReuseCompileClosureVerifier
    assert harness.VerifiedExactReuseCompileClosure is VerifiedExactReuseCompileClosure
    assert harness.X2EnvCompileClosureError is X2EnvCompileClosureError
    assert harness.x2env_environment_package_sha256 is x2env_environment_package_sha256

    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=LocalArtifactStore(tmp_path / "cas"),
        max_source_bytes=1024,
        max_decoded_pixels=4096,
        expected_implementation_sha256="2" * 64,
        trusted_qualification_issuer_sha256="3" * 64,
        expected_runtime_lock=_ref(),
        trusted_asset_qualifications=(
            _ref("4" * 64).model_copy(update={"schema_version": "harness.asset_qualification.v1"}),
        ),
    )
    assert verifier.expected_runtime_lock == _ref()
    with pytest.raises(TypeError, match="compile_input"):
        verifier.verify(object(), object())


@pytest.mark.skipif(ExactReuseCompileClosureVerifier is None, reason="verifier is absent")
def test_exact_reuse_compile_closure_is_verified_without_writing_cas(tmp_path: Path) -> None:
    case = _case(tmp_path)
    before = sorted(
        path.relative_to(case.store.root) for path in case.store.root.rglob("*") if path.is_file()
    )
    verified = case.verifier.verify(case.compile_input, case.compile_output)
    after = sorted(
        path.relative_to(case.store.root) for path in case.store.root.rglob("*") if path.is_file()
    )

    assert isinstance(verified, VerifiedExactReuseCompileClosure)
    assert verified.compile_receipt.environment_package == case.refs["package"]
    assert verified.qualifications[0].subject == verified.selections[0].qualified_subject
    assert before == after


@pytest.mark.skipif(ExactReuseCompileClosureVerifier is None, reason="verifier is absent")
def test_verifier_constructor_and_exact_type_boundaries_are_fail_closed(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    values = {
        "artifact_store": store,
        "max_source_bytes": 1024,
        "max_decoded_pixels": 4096,
        "expected_implementation_sha256": "2" * 64,
        "trusted_qualification_issuer_sha256": "3" * 64,
        "expected_runtime_lock": _ref(),
        "trusted_asset_qualifications": (
            _ref("4" * 64).model_copy(update={"schema_version": "harness.asset_qualification.v1"}),
        ),
    }
    for field, replacement, error, message in (
        ("artifact_store", object(), TypeError, "artifact_store"),
        ("max_source_bytes", False, ValueError, "max_source_bytes"),
        ("max_decoded_pixels", 0, ValueError, "max_decoded_pixels"),
        ("expected_implementation_sha256", 3, ValueError, "expected_implementation"),
        ("trusted_qualification_issuer_sha256", "f", ValueError, "trusted_qualification"),
        ("expected_runtime_lock", object(), TypeError, "expected_runtime_lock"),
        (
            "expected_runtime_lock",
            _ref().model_copy(update={"uri": "artifact://sha256/" + "f" * 64}),
            ValueError,
            "content-addressed",
        ),
        (
            "expected_runtime_lock",
            _ref().model_copy(update={"schema_version": "harness.other.v1"}),
            ValueError,
            "identify",
        ),
        ("trusted_asset_qualifications", [], TypeError, "exact tuple"),
        ("trusted_asset_qualifications", (), ValueError, "non-empty"),
        (
            "trusted_asset_qualifications",
            (
                _ref("4" * 64).model_copy(
                    update={"schema_version": "harness.asset_qualification.v1"}
                ),
            )
            * 2,
            ValueError,
            "sorted, and unique",
        ),
        ("trusted_asset_qualifications", (_ref("4" * 64),), ValueError, "identify"),
    ):
        with pytest.raises(error, match=message):
            ExactReuseCompileClosureVerifier(**{**values, field: replacement})

    case = _case(tmp_path / "exact-types")
    with pytest.raises(TypeError, match="compile_output"):
        case.verifier.verify(case.compile_input, object())
    with pytest.raises(TypeError, match="value"):
        x2env_environment_package_sha256(object())


@pytest.mark.skipif(ExactReuseCompileClosureVerifier is None, reason="verifier is absent")
def test_input_source_and_semantic_drift_are_rejected_before_acceptance(tmp_path: Path) -> None:
    unsupported = _case(tmp_path / "unsupported")
    wrong_input = unsupported.compile_input.model_copy(update={"acquisition_policy": "auto"})
    with pytest.raises(X2EnvCompileClosureError, match="first exact-only") as caught:
        unsupported.verifier.verify(wrong_input, unsupported.compile_output)
    assert caught.value.code == "HARN_X2ENV_COMPILE_EVIDENCE_MISMATCH"

    missing_source = _case(tmp_path / "missing-source")
    missing_source.store.resolve_digest(
        missing_source.compile_input.source_media.source.sha256
    ).unlink()
    with pytest.raises(X2EnvCompileClosureError, match="source image") as caught:
        missing_source.verifier.verify(missing_source.compile_input, missing_source.compile_output)
    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"

    drift = _case(tmp_path / "proposal-drift")
    proposal = evidence.SceneInterpretationProposalV1.model_validate_json(
        drift.store.resolve(drift.refs["proposal"]).path.read_bytes()
    ).model_dump(mode="json")
    proposal["source_media_sha256"] = "f" * 64
    proposal["proposal_sha256"] = evidence.canonical_evidence_sha256(proposal, "proposal_sha256")
    drift_ref = _put(
        drift.store,
        tmp_path / "proposal-drift" / "mutations",
        name="proposal-drift.json",
        payload=_canonical_json(proposal),
        media_type="application/json",
        schema_version="harness.scene_interpretation_proposal.v1",
    )
    drift_input = drift.compile_input.model_copy(update={"interpretation_proposal": drift_ref})
    with pytest.raises(X2EnvCompileClosureError, match="does not bind the image source"):
        drift.verifier.verify(drift_input, drift.compile_output)

    untrusted = _case(tmp_path / "untrusted-qualification")
    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=untrusted.store,
        max_source_bytes=4096,
        max_decoded_pixels=4096,
        expected_implementation_sha256="7" * 64,
        trusted_qualification_issuer_sha256="6" * 64,
        expected_runtime_lock=untrusted.refs["runtime"],
        trusted_asset_qualifications=(
            untrusted.refs["qualification"].model_copy(
                update={"sha256": "f" * 64, "uri": "artifact://sha256/" + "f" * 64}
            ),
        ),
    )
    with pytest.raises(X2EnvCompileClosureError, match="not backed by trusted"):
        verifier.verify(untrusted.compile_input, untrusted.compile_output)

    missing_selected_representation = _case(tmp_path / "missing-selected-representation")
    selection = evidence.X2EnvAssetSelectionV1.model_validate_json(
        missing_selected_representation.store.resolve(
            missing_selected_representation.refs["selection"]
        ).path.read_bytes()
    ).model_dump(mode="json")
    selection["representation"] = ArtifactRef(
        name="unbound-representation",
        uri="artifact://sha256/" + "f" * 64,
        media_type="application/json",
        sha256="f" * 64,
        bytes=99,
        schema_version="harness.asset_representation.v1",
    ).model_dump(mode="json")
    selection["selection_sha256"] = evidence.canonical_evidence_sha256(
        selection, "selection_sha256"
    )
    selection_ref = _put(
        missing_selected_representation.store,
        tmp_path / "missing-selected-representation" / "mutations",
        name="selection-unbound-representation.json",
        payload=_canonical_json(selection),
        media_type="application/json",
        schema_version="harness.x2env_asset_selection.v1",
    )
    decision = missing_selected_representation.compile_output.asset_decisions[0].model_copy(
        update={"decision_evidence": selection_ref}
    )
    changed_output = missing_selected_representation.compile_output.model_copy(
        update={"asset_decisions": (decision,)}
    )
    with pytest.raises(X2EnvCompileClosureError, match="absent from the trusted graph"):
        missing_selected_representation.verifier.verify(
            missing_selected_representation.compile_input, changed_output
        )

    missing_catalog_subject = _case(tmp_path / "missing-catalog-subject")
    catalog = evidence.PortableAssetCatalogV1.model_validate_json(
        missing_catalog_subject.store.resolve(
            missing_catalog_subject.refs["catalog"]
        ).path.read_bytes()
    ).model_dump(mode="json")
    catalog["entries"][0]["subject"]["asset_key"] = "072_can"
    catalog["entries"][0]["subject"]["ledger_asset_id"] = "072_can"
    catalog["entries"][0]["qualified_subject"]["model_subject"] = catalog["entries"][0]["subject"]
    catalog["catalog_sha256"] = evidence.canonical_evidence_sha256(catalog, "catalog_sha256")
    catalog_ref = _put(
        missing_catalog_subject.store,
        tmp_path / "missing-catalog-subject" / "mutations",
        name="other-catalog.json",
        payload=_canonical_json(catalog),
        media_type="application/json",
        schema_version="harness.portable_asset_catalog.v1",
    )
    selection = evidence.X2EnvAssetSelectionV1.model_validate_json(
        missing_catalog_subject.store.resolve(
            missing_catalog_subject.refs["selection"]
        ).path.read_bytes()
    ).model_dump(mode="json")
    selection["asset_catalog"] = catalog_ref.model_dump(mode="json")
    selection["selection_sha256"] = evidence.canonical_evidence_sha256(
        selection, "selection_sha256"
    )
    selection_ref = _put(
        missing_catalog_subject.store,
        tmp_path / "missing-catalog-subject" / "mutations",
        name="selection-other-catalog.json",
        payload=_canonical_json(selection),
        media_type="application/json",
        schema_version="harness.x2env_asset_selection.v1",
    )
    decision = missing_catalog_subject.compile_output.asset_decisions[0].model_copy(
        update={"decision_evidence": selection_ref}
    )
    changed_input = missing_catalog_subject.compile_input.model_copy(
        update={"asset_catalog": catalog_ref}
    )
    changed_output = missing_catalog_subject.compile_output.model_copy(
        update={"asset_decisions": (decision,)}
    )
    with pytest.raises(X2EnvCompileClosureError, match="catalog entry is not backed by trusted"):
        missing_catalog_subject.verifier.verify(changed_input, changed_output)


@pytest.mark.skipif(ExactReuseCompileClosureVerifier is None, reason="verifier is absent")
def test_typed_json_and_asset_member_integrity_are_recomputed(tmp_path: Path) -> None:
    missing = _case(tmp_path / "missing")
    missing.store.resolve_digest(missing.refs["proposal"].sha256).unlink()
    with pytest.raises(X2EnvCompileClosureError, match="unavailable or corrupt") as caught:
        missing.verifier.verify(missing.compile_input, missing.compile_output)
    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"

    invalid = _case(tmp_path / "invalid")
    invalid_ref = _put(
        invalid.store,
        tmp_path / "invalid" / "mutations",
        name="invalid-proposal.json",
        payload=b"{}\n",
        media_type="application/json",
        schema_version="harness.scene_interpretation_proposal.v1",
    )
    invalid_input = invalid.compile_input.model_copy(
        update={"interpretation_proposal": invalid_ref}
    )
    with pytest.raises(X2EnvCompileClosureError, match="strict typed JSON") as caught:
        invalid.verifier.verify(invalid_input, invalid.compile_output)
    assert caught.value.code == "HARN_X2ENV_COMPILE_EVIDENCE_INVALID"

    noncanonical = _case(tmp_path / "noncanonical")
    proposal = evidence.SceneInterpretationProposalV1.model_validate_json(
        noncanonical.store.resolve(noncanonical.refs["proposal"]).path.read_bytes()
    )
    noncanonical_ref = _put(
        noncanonical.store,
        tmp_path / "noncanonical" / "mutations",
        name="noncanonical-proposal.json",
        payload=(json.dumps(proposal.model_dump(mode="json"), indent=2) + "\n").encode(),
        media_type="application/json",
        schema_version="harness.scene_interpretation_proposal.v1",
    )
    noncanonical_input = noncanonical.compile_input.model_copy(
        update={"interpretation_proposal": noncanonical_ref}
    )
    with pytest.raises(X2EnvCompileClosureError, match="not canonical JSON"):
        noncanonical.verifier.verify(noncanonical_input, noncanonical.compile_output)

    member = _case(tmp_path / "member")
    representation = evidence.AssetRepresentationV1.model_validate_json(
        member.store.resolve(member.refs["representation"]).path.read_bytes()
    )
    member.store.resolve_digest(representation.members[0].artifact.sha256).unlink()
    with pytest.raises(X2EnvCompileClosureError, match="member is unavailable"):
        member.verifier.verify(member.compile_input, member.compile_output)
