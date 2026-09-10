from __future__ import annotations

from pathlib import Path

import pytest

from self_improving.harness.schemas import x2env_compile_evidence as evidence
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.x2env import X2EnvEnvironmentPackage
from self_improving.harness.x2env_compile_closure import (
    ExactReuseCompileClosureVerifier,
    X2EnvCompileClosureError,
    x2env_environment_package_sha256,
)
from tests.self_improving.harness.test_x2env_compile_closure import (
    _build,
    _case,
    _publish_model,
    _put,
)


def _load(case: object, key: str, model: type[object]) -> object:
    ref = case.refs[key]  # type: ignore[attr-defined]
    payload = case.store.resolve(ref).path.read_bytes()  # type: ignore[attr-defined]
    return model.model_validate_json(payload)  # type: ignore[attr-defined]


def _cas_manifest(case: object) -> tuple[tuple[str, int], ...]:
    root = case.store.root  # type: ignore[attr-defined]
    return tuple(
        (str(path.relative_to(root)), path.stat().st_size)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _with_available_unselected_qualification(
    tmp_path: Path,
    *,
    check_codes: tuple[str, ...],
    bind_required_evidence: bool,
) -> tuple[object, ExactReuseCompileClosureVerifier, object, object]:
    case = _case(tmp_path)
    staging = tmp_path / "unselected-qualification"
    subject = {
        "namespace": "robotwin",
        "asset_key": "999_ghost",
        "ledger_asset_id": "999_ghost",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "a" * 64,
    }
    member_refs = (
        _put(
            case.store,
            staging,
            name="999_ghost/collision.glb",
            payload=b"ghost-collision",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
        _put(
            case.store,
            staging,
            name="999_ghost/visual.glb",
            payload=b"ghost-visual",
            media_type="model/gltf-binary",
            schema_version=None,
        ),
    )
    members = [
        {
            "logical_path": "999_ghost/collision.glb",
            "role": "collision",
            "artifact": member_refs[0],
        },
        {
            "logical_path": "999_ghost/visual.glb",
            "role": "visual",
            "artifact": member_refs[1],
        },
    ]
    loader_sha = evidence.asset_loader_closure_sha256(subject, "999_ghost/collision.glb", members)
    representation = _build(
        evidence.AssetRepresentationV1,
        "representation_sha256",
        {
            "schema_version": "harness.asset_representation.v1",
            "subject": subject,
            "representation_kind": "rigid_mesh",
            "root_logical_path": "999_ghost/collision.glb",
            "loader_scale": [1.0, 1.0, 1.0],
            "loader_closure_sha256": loader_sha,
            "members": members,
            "collision_member_paths": ["999_ghost/collision.glb"],
            "visual_member_paths": ["999_ghost/visual.glb"],
        },
    )
    representation_ref = _publish_model(case.store, staging, "ghost-representation", representation)
    qualified_subject = {
        "model_subject": subject,
        "representation_sha256": representation.representation_sha256,
        "loader_closure_sha256": loader_sha,
    }
    unrelated_evidence = _put(
        case.store,
        staging,
        name="unrelated-proof.bin",
        payload=b"real-but-unrelated-proof-bytes",
        media_type="application/octet-stream",
        schema_version=None,
    )
    check_evidence = (
        [representation_ref, case.refs["runtime"]]
        if bind_required_evidence
        else [unrelated_evidence]
    )
    check_evidence = sorted(
        check_evidence,
        key=lambda ref: (ref.sha256, ref.bytes, ref.media_type),
    )
    qualification = _build(
        evidence.GenesisAssetQualificationV1,
        "qualification_sha256",
        {
            "schema_version": "harness.asset_qualification.v1",
            "subject": qualified_subject,
            "representation": representation_ref,
            "runtime_lock": case.refs["runtime"],
            "requested_profile": "genesis.rigid_scene@1",
            "issuer_implementation_sha256": "6" * 64,
            "checks": [
                {"code": code, "status": "pass", "evidence": check_evidence} for code in check_codes
            ],
            "status": "pass",
            "backend_load_executed": True,
            "collision_probe_executed": True,
            "qualified": True,
        },
    )
    qualification_ref = _publish_model(case.store, staging, "ghost-qualification", qualification)
    catalog = _load(case, "catalog", evidence.PortableAssetCatalogV1).model_dump(mode="json")
    catalog["entries"].append(
        {
            "subject": subject,
            "semantic_name": "ghost",
            "aliases": [],
            "representation": representation_ref,
            "representation_sha256": representation.representation_sha256,
            "qualification": qualification_ref,
            "qualified_subject": qualified_subject,
            "available": True,
        }
    )
    catalog_model = _build(evidence.PortableAssetCatalogV1, "catalog_sha256", catalog)
    catalog_ref = _publish_model(case.store, staging, "catalog", catalog_model)
    selection = _load(case, "selection", evidence.X2EnvAssetSelectionV1).model_dump(mode="json")
    selection["asset_catalog"] = catalog_ref.model_dump(mode="json")
    selection_model = _build(evidence.X2EnvAssetSelectionV1, "selection_sha256", selection)
    selection_ref = _publish_model(case.store, staging, "selection", selection_model)
    binding = _load(case, "binding", evidence.X2EnvBindingManifestV1).model_dump(mode="json")
    binding["asset_catalog"] = catalog_ref.model_dump(mode="json")
    binding["bindings"][0]["selection"] = selection_ref.model_dump(mode="json")
    binding_model = _build(evidence.X2EnvBindingManifestV1, "binding_sha256", binding)
    binding_ref = _publish_model(case.store, staging, "binding", binding_model)
    static = _load(case, "static", evidence.X2EnvStaticValidationV1).model_dump(mode="json")
    static["asset_catalog"] = catalog_ref.model_dump(mode="json")
    static["asset_selections"] = [selection_ref.model_dump(mode="json")]
    static["binding_manifest"] = binding_ref.model_dump(mode="json")
    for check in static["checks"]:
        if check["code"] == "asset_closure_complete":
            check["evidence"] = sorted(
                [selection_ref, case.refs["closure"]],
                key=lambda ref: (ref.sha256, ref.bytes, ref.media_type),
            )
        elif check["code"] == "binding_complete":
            check["evidence"] = [binding_ref]
    static_model = _build(evidence.X2EnvStaticValidationV1, "validation_sha256", static)
    static_ref = _publish_model(case.store, staging, "static", static_model)
    package = _load(case, "package", X2EnvEnvironmentPackage).model_dump(mode="json")
    package["binding_manifest"] = binding_ref.model_dump(mode="json")
    package["package_id"] = "0" * 64
    package_model = X2EnvEnvironmentPackage.model_validate(package)
    package_model = package_model.model_copy(
        update={"package_id": x2env_environment_package_sha256(package_model)}
    )
    package_ref = _publish_model(case.store, staging, "package", package_model)
    receipt = _load(case, "receipt", evidence.X2EnvCompileReceiptV1).model_dump(mode="json")
    receipt.update(
        {
            "asset_catalog": catalog_ref.model_dump(mode="json"),
            "asset_selections": [selection_ref.model_dump(mode="json")],
            "binding_manifest": binding_ref.model_dump(mode="json"),
            "static_validation": static_ref.model_dump(mode="json"),
            "environment_package": package_ref.model_dump(mode="json"),
            "package_id": package_model.package_id,
        }
    )
    receipt_model = _build(evidence.X2EnvCompileReceiptV1, "receipt_sha256", receipt)
    receipt_ref = _publish_model(case.store, staging, "receipt", receipt_model)
    decision = case.compile_output.asset_decisions[0].model_copy(
        update={"decision_evidence": selection_ref}
    )
    compile_input = case.compile_input.model_copy(update={"asset_catalog": catalog_ref})
    compile_output = case.compile_output.model_copy(
        update={
            "environment_package": package_model,
            "asset_decisions": (decision,),
            "static_validation": static_ref,
            "compile_receipt": receipt_ref,
        }
    )
    trusted = tuple(
        sorted(
            (case.refs["qualification"], qualification_ref),
            key=lambda ref: ref.sha256,
        )
    )
    verifier = ExactReuseCompileClosureVerifier(
        artifact_store=case.store,
        max_source_bytes=4096,
        max_decoded_pixels=4096,
        expected_implementation_sha256="7" * 64,
        trusted_qualification_issuer_sha256="6" * 64,
        expected_runtime_lock=case.refs["runtime"],
        trusted_asset_qualifications=trusted,
    )
    return case, verifier, compile_input, compile_output


def test_available_catalog_entries_are_complete_even_when_not_selected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    staging = tmp_path / "mutations"
    catalog = _load(case, "catalog", evidence.PortableAssetCatalogV1).model_dump(mode="json")
    ghost_subject = {
        "namespace": "robotwin",
        "asset_key": "999_ghost",
        "ledger_asset_id": "999_ghost",
        "model_id": 0,
        "backend": "genesis",
        "source_record_sha256": "a" * 64,
    }
    missing_representation = ArtifactRef(
        name="missing-representation",
        uri="artifact://sha256/" + "b" * 64,
        media_type="application/json",
        sha256="b" * 64,
        bytes=999,
        schema_version="harness.asset_representation.v1",
    )
    missing_qualification = ArtifactRef(
        name="missing-qualification",
        uri="artifact://sha256/" + "c" * 64,
        media_type="application/json",
        sha256="c" * 64,
        bytes=999,
        schema_version="harness.asset_qualification.v1",
    )
    catalog["entries"].append(
        {
            "subject": ghost_subject,
            "semantic_name": "ghost",
            "aliases": [],
            "representation": missing_representation.model_dump(mode="json"),
            "representation_sha256": "d" * 64,
            "qualification": missing_qualification.model_dump(mode="json"),
            "qualified_subject": {
                "model_subject": ghost_subject,
                "representation_sha256": "d" * 64,
                "loader_closure_sha256": "e" * 64,
            },
            "available": True,
        }
    )
    catalog_model = _build(evidence.PortableAssetCatalogV1, "catalog_sha256", catalog)
    catalog_ref = _publish_model(case.store, staging, "catalog", catalog_model)

    selection = _load(case, "selection", evidence.X2EnvAssetSelectionV1).model_dump(mode="json")
    selection["asset_catalog"] = catalog_ref.model_dump(mode="json")
    selection_model = _build(evidence.X2EnvAssetSelectionV1, "selection_sha256", selection)
    selection_ref = _publish_model(case.store, staging, "selection", selection_model)

    binding = _load(case, "binding", evidence.X2EnvBindingManifestV1).model_dump(mode="json")
    binding["asset_catalog"] = catalog_ref.model_dump(mode="json")
    binding["bindings"][0]["selection"] = selection_ref.model_dump(mode="json")
    binding_model = _build(evidence.X2EnvBindingManifestV1, "binding_sha256", binding)
    binding_ref = _publish_model(case.store, staging, "binding", binding_model)

    static = _load(case, "static", evidence.X2EnvStaticValidationV1).model_dump(mode="json")
    static["asset_catalog"] = catalog_ref.model_dump(mode="json")
    static["asset_selections"] = [selection_ref.model_dump(mode="json")]
    static["binding_manifest"] = binding_ref.model_dump(mode="json")
    static["checks"][0]["evidence"] = [binding_ref.model_dump(mode="json")]
    static_model = _build(evidence.X2EnvStaticValidationV1, "validation_sha256", static)
    static_ref = _publish_model(case.store, staging, "static", static_model)

    package = _load(case, "package", X2EnvEnvironmentPackage).model_dump(mode="json")
    package["binding_manifest"] = binding_ref.model_dump(mode="json")
    package["package_id"] = "0" * 64
    package_model = X2EnvEnvironmentPackage.model_validate(package)
    package_model = package_model.model_copy(
        update={"package_id": x2env_environment_package_sha256(package_model)}
    )
    package_ref = _publish_model(case.store, staging, "package", package_model)

    receipt = _load(case, "receipt", evidence.X2EnvCompileReceiptV1).model_dump(mode="json")
    receipt.update(
        {
            "asset_catalog": catalog_ref.model_dump(mode="json"),
            "asset_selections": [selection_ref.model_dump(mode="json")],
            "binding_manifest": binding_ref.model_dump(mode="json"),
            "static_validation": static_ref.model_dump(mode="json"),
            "environment_package": package_ref.model_dump(mode="json"),
            "package_id": package_model.package_id,
        }
    )
    receipt_model = _build(evidence.X2EnvCompileReceiptV1, "receipt_sha256", receipt)
    receipt_ref = _publish_model(case.store, staging, "receipt", receipt_model)
    decision = case.compile_output.asset_decisions[0].model_copy(
        update={"decision_evidence": selection_ref}
    )
    compile_input = case.compile_input.model_copy(update={"asset_catalog": catalog_ref})
    compile_output = case.compile_output.model_copy(
        update={
            "environment_package": package_model,
            "asset_decisions": (decision,),
            "static_validation": static_ref,
            "compile_receipt": receipt_ref,
        }
    )
    before = _cas_manifest(case)

    with pytest.raises(X2EnvCompileClosureError) as caught:
        case.verifier.verify(compile_input, compile_output)

    assert caught.value.code == "HARN_ARTIFACT_UNAVAILABLE"
    assert _cas_manifest(case) == before


def test_static_validation_pass_requires_qualified_checks_and_evidence(tmp_path: Path) -> None:
    case = _case(tmp_path)
    staging = tmp_path / "mutations"
    static = _load(case, "static", evidence.X2EnvStaticValidationV1).model_dump(mode="json")
    static["checks"] = [{"code": "noop", "status": "pass", "evidence": []}]
    static_model = _build(evidence.X2EnvStaticValidationV1, "validation_sha256", static)
    static_ref = _publish_model(case.store, staging, "static", static_model)
    receipt = _load(case, "receipt", evidence.X2EnvCompileReceiptV1).model_dump(mode="json")
    receipt["static_validation"] = static_ref.model_dump(mode="json")
    receipt_model = _build(evidence.X2EnvCompileReceiptV1, "receipt_sha256", receipt)
    receipt_ref = _publish_model(case.store, staging, "receipt", receipt_model)
    compile_output = case.compile_output.model_copy(
        update={"static_validation": static_ref, "compile_receipt": receipt_ref}
    )
    before = _cas_manifest(case)

    with pytest.raises(X2EnvCompileClosureError) as caught:
        case.verifier.verify(case.compile_input, compile_output)

    assert caught.value.code == "HARN_X2ENV_COMPILE_EVIDENCE_MISMATCH"
    assert _cas_manifest(case) == before


def test_available_unselected_qualification_requires_exact_check_vocabulary(
    tmp_path: Path,
) -> None:
    case, verifier, compile_input, compile_output = _with_available_unselected_qualification(
        tmp_path,
        check_codes=("noop",),
        bind_required_evidence=True,
    )
    before = _cas_manifest(case)

    with pytest.raises(X2EnvCompileClosureError) as caught:
        verifier.verify(compile_input, compile_output)

    assert caught.value.code == "HARN_X2ENV_COMPILE_EVIDENCE_MISMATCH"
    assert _cas_manifest(case) == before


def test_available_unselected_qualification_checks_bind_representation_and_runtime(
    tmp_path: Path,
) -> None:
    case, verifier, compile_input, compile_output = _with_available_unselected_qualification(
        tmp_path,
        check_codes=("backend_load", "collision_probe"),
        bind_required_evidence=False,
    )
    before = _cas_manifest(case)

    with pytest.raises(X2EnvCompileClosureError) as caught:
        verifier.verify(compile_input, compile_output)

    assert caught.value.code == "HARN_X2ENV_COMPILE_EVIDENCE_MISMATCH"
    assert _cas_manifest(case) == before
