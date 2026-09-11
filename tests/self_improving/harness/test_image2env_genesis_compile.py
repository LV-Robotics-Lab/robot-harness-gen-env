"""Native image compilation consumes durable asset authority and measured scene placement."""

import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.genesis_asset_qualification_application import (
    GenesisAssetQualificationApplication,
)
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.image2env_genesis_compile import (
    Image2EnvGenesisCompileReceiptV1,
)
from tests.self_improving.harness.test_genesis_runtime_execution import (
    execution_request,  # noqa: F401
)


def test_malformed_native_compile_request_cannot_write_candidates(execution_request, tmp_path):  # noqa: F811
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile")
    store, _, _, _, request, _ = execution_request
    app = GenesisAssetQualificationApplication(
        tmp_path / "qualification.sqlite", store, request.manifest
    )
    compiler = module.Image2EnvGenesisCompiler(store, app)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"not": "native compile request"}))
    ref = store.put_file(
        path,
        name="invalid.json",
        media_type="application/json",
        schema_version="harness.image2env_genesis_compile_request.v1",
    )
    before = set(store.root.rglob("*"))
    with pytest.raises(ValueError):
        compiler.compile(ref)
    assert set(store.root.rglob("*")) == before


@pytest.mark.parametrize(
    "variation",
    [
        "valid",
        "claim_execution",
        "different_object",
        "duplicate_source",
        "wrong_implementation_type",
        "wrong_scene_type",
    ],
)
def test_native_receipt_contract_cannot_claim_execution_or_change_object_mapping(variation):
    def ref(name, schema, media="application/json"):
        return ArtifactRef(
            name=name,
            uri="artifact://sha256/" + "a" * 64,
            sha256="a" * 64,
            bytes=1,
            media_type=media,
            schema_version=schema,
        ).model_dump(mode="json")

    data = {
        "schema_version": "harness.image2env_genesis_compile_receipt.v1",
        "request": ref("request", "harness.image2env_genesis_compile_request.v1"),
        "scene": ref("scene", "harness.genesis_scene_spec.v1"),
        "qualification": ref("qualification", "harness.asset_qualification.v1"),
        "source_object_id": "mouse",
        "proposal_object_id": "mouse",
        "scene_object_id": "object",
        "implementation_sha256": "b" * 64,
        "implementation": [ref("source.py", None, "text/x-python")],
        "scope": "static_scene_candidate",
        "simulator_executed": False,
    }
    if variation == "claim_execution":
        data["simulator_executed"] = True
    if variation == "different_object":
        data["proposal_object_id"] = "other"
    if variation == "duplicate_source":
        data["implementation"] *= 2
    if variation == "wrong_implementation_type":
        data["implementation"][0]["media_type"] = "text/plain"
    if variation == "wrong_scene_type":
        data["scene"]["schema_version"] = "harness.asset_representation.v1"
    if variation == "valid":
        assert Image2EnvGenesisCompileReceiptV1.model_validate(data).simulator_executed is False
    else:
        with pytest.raises(ValueError):
            Image2EnvGenesisCompileReceiptV1.model_validate(data)


@pytest.mark.parametrize(
    "variation",
    [
        "unknown_run",
        "wrong_store",
        "wrong_app",
        "noncanonical",
        "wrong_schema",
        "table_below_ground",
        "source_type",
    ],
)
def test_unrecorded_qualification_cannot_authorize_compile(execution_request, tmp_path, variation):  # noqa: F811
    from self_improving.harness.schemas.image2env_genesis_compile import (
        Image2EnvGenesisCompileRequestV1,
    )
    from tests.self_improving.harness.test_x2env_compile_closure import _case

    module = importlib.import_module("self_improving.harness.image2env_genesis_compile")
    _, _, _, _, runtime_request, _ = execution_request
    case = _case(tmp_path / "input")
    app = GenesisAssetQualificationApplication(
        tmp_path / "empty.sqlite", case.store, runtime_request.manifest
    )
    source = case.compile_input.interpretation_proposal.model_copy(update={"schema_version": None})
    request = Image2EnvGenesisCompileRequestV1(
        schema_version="harness.image2env_genesis_compile_request.v1",
        compile_input=case.compile_input,
        qualification_run_id=uuid4(),
        source_scene=source,
        table_center_m=(0.0, 0.0, 0.2),
        table_size_m=(1.0, 0.8, 0.4),
        table_friction=0.5,
    )
    if variation in ("table_below_ground", "source_type"):
        data = request.model_dump(mode="json")
        if variation == "table_below_ground":
            data["table_center_m"] = [0.0, 0.0, 0.0]
        else:
            data["source_scene"]["schema_version"] = "wrong"
        with pytest.raises(ValueError):
            Image2EnvGenesisCompileRequestV1.model_validate(data)
        return
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    ref = case.store.put_file(
        path,
        name="request.json",
        media_type="application/json",
        schema_version=request.schema_version,
    )
    if variation == "wrong_store":
        app = GenesisAssetQualificationApplication(
            tmp_path / "other.sqlite",
            LocalArtifactStore(tmp_path / "othercas"),
            runtime_request.manifest,
        )
    if variation == "wrong_app":
        app = object()
    if variation == "wrong_schema":
        ref = ref.model_copy(update={"schema_version": "wrong"})
    if variation == "noncanonical":
        path.write_bytes(path.read_bytes() + b"\n")
        ref = case.store.put_file(
            path, name=ref.name, media_type=ref.media_type, schema_version=ref.schema_version
        )
    before = set(case.store.root.rglob("*"))
    with pytest.raises((ValueError, TypeError)):
        module.Image2EnvGenesisCompiler(case.store, app).compile(ref)
    assert set(case.store.root.rglob("*")) == before


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"),
    reason="requires real qualified runtime CAS",
)
@pytest.mark.parametrize("variation", ["source_identity", "implementation", "scene_binding"])
def test_formal_native_binding_drift_fails_closed(variation, tmp_path):
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler

    config_path = Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"])
    config = json.loads(config_path.read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    app = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    compiler = Image2EnvGenesisCompiler(store, app)
    request_ref = ArtifactRef.model_validate(config["request_ref"])

    def put(payload, ref):
        path = tmp_path / "changed.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        return store.put_file(
            path, name=ref.name, media_type=ref.media_type, schema_version=ref.schema_version
        )

    if variation == "source_identity":
        request = json.loads(store.resolve(request_ref).path.read_bytes())
        source_ref = ArtifactRef.model_validate(request["source_scene"])
        source = json.loads(store.resolve(source_ref).path.read_bytes())
        source["metadata"]["category"] = "different"
        request["source_scene"] = put(source, source_ref).model_dump(mode="json")
        changed = put(request, request_ref)
        with pytest.raises(ValueError, match="Gujie source"):
            compiler.compile(changed)
    else:
        # Obtain a current candidate through the real producer, never a fabricated qualification.
        receipt_ref = compiler.compile(request_ref)
        receipt = json.loads(store.resolve(receipt_ref).path.read_bytes())
        if variation == "implementation":
            receipt["implementation_sha256"] = "0" * 64
        else:
            from self_improving.harness.gujie_scene_adapter import adapt_gujie_tabletop_scene

            request = json.loads(store.resolve(request_ref).path.read_bytes())
            scene = json.loads(
                store.resolve(ArtifactRef.model_validate(receipt["scene"])).path.read_bytes()
            )
            other = adapt_gujie_tabletop_scene(
                store,
                source_ref=ArtifactRef.model_validate(request["source_scene"]),
                representation_ref=ArtifactRef.model_validate(scene["representation"]),
                table_center_m=(0.0, 0.0, 0.25),
                table_size_m=(1.0, 0.8, 0.4),
                table_friction=0.5,
            )
            receipt["scene"] = other.model_dump(mode="json")
        changed = put(receipt, receipt_ref)
        with pytest.raises(ValueError, match="differs"):
            compiler.read(changed)


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"),
    reason="requires real qualified runtime CAS",
)
def test_formal_known_mouse_image_compiles_to_measured_tabletop_scene():
    module = importlib.import_module("self_improving.harness.image2env_genesis_compile")
    config = json.loads(Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).read_text())
    store = LocalArtifactStore(Path(config["cas_root"]))
    app = GenesisAssetQualificationApplication(
        Path(config["state_path"]),
        store,
        ArtifactRef.model_validate(config["approved_manifest_ref"]),
    )
    compiler = module.Image2EnvGenesisCompiler(store, app)
    ref = compiler.compile(ArtifactRef.model_validate(config["request_ref"]))
    receipt, scene = compiler.read(ref)
    assert receipt.scope == "static_scene_candidate" and receipt.simulator_executed is False
    assert scene.table_size_m == (1.0, 0.8, 0.4)
    assert scene.position_m[2] == pytest.approx(0.43564921021933667)
    Path(os.environ["GOLDEN_NATIVE_IMAGE_COMPILE_CONFIG"]).with_name(
        "compile-result.json"
    ).write_text(
        json.dumps(
            {
                "receipt_ref": ref.model_dump(mode="json"),
                "scene_ref": receipt.scene.model_dump(mode="json"),
            },
            indent=2,
        )
        + "\n"
    )
