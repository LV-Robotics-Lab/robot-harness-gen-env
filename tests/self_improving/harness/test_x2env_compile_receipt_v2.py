from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

import self_improving.harness as harness
import self_improving.harness.schemas as schemas
from self_improving.harness.schema_catalog import (
    DEFAULT_SCHEMA_ROOT,
    export_schema_snapshots,
    render_schema_document,
    schema_documents,
    schema_model,
)
from self_improving.harness.schemas import x2env_compile_evidence as evidence
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileOutput,
    Video2EnvCompileOutput,
)
from self_improving.harness.x2env_contracts import x2env_skill_contract
from tests.self_improving.harness.test_x2env_compile_closure import (
    _case,
    _publish_model,
)

try:
    from self_improving.harness.schemas.x2env_compile_evidence import (
        X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID,
        X2EnvCompileReceiptV2,
    )
except ImportError:
    X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID = None  # type: ignore[assignment]
    X2EnvCompileReceiptV2 = None  # type: ignore[assignment,misc]

try:
    from self_improving.harness.schemas.x2env import (
        IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        Image2EnvCompileV2Output,
        Video2EnvCompileV2Output,
    )
except ImportError:
    IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID = None  # type: ignore[assignment]
    VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID = None  # type: ignore[assignment]
    Image2EnvCompileV2Output = None  # type: ignore[assignment,misc]
    Video2EnvCompileV2Output = None  # type: ignore[assignment,misc]


_V2_AVAILABLE = all(
    value is not None
    for value in (
        X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID,
        X2EnvCompileReceiptV2,
        IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        Image2EnvCompileV2Output,
        VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        Video2EnvCompileV2Output,
    )
)
_requires_v2 = pytest.mark.skipif(not _V2_AVAILABLE, reason="x2env compile v2 seam is absent")

_V1_SNAPSHOT_SHA256 = {
    "harness.image2env_compile_output.v1.schema.json": (
        "42b620ab6b0a683928dbdd08269455dae52ebeee03855f278d3aa0399267bdcd"
    ),
    "harness.video2env_compile_output.v1.schema.json": (
        "dea7e481e0be5e43a09d38ae0632fc8ff1a9e8d7519c35b7cef0c52361cc7478"
    ),
    "harness.x2env_compile_receipt.v1.schema.json": (
        "167855326d49ed4a981c36c18a6bdfbe05212e9c9d15a4b55eaaeab148648137"
    ),
}


def _artifact(
    schema_version: str,
    *,
    digest: str = "d" * 64,
    media_type: str = "application/json",
) -> ArtifactRef:
    return ArtifactRef(
        name=schema_version,
        uri=f"artifact://sha256/{digest}",
        media_type=media_type,
        sha256=digest,
        bytes=123,
        schema_version=schema_version,
    )


def _receipt_v2_payload(
    tmp_path: Path,
    *,
    route: str = "image2env",
) -> tuple[object, dict[str, object]]:
    case = _case(tmp_path)
    receipt_v1 = evidence.X2EnvCompileReceiptV1.model_validate_json(
        case.store.resolve(case.refs["receipt"]).path.read_bytes()
    )
    if route == "image2env":
        compile_input = _publish_model(
            case.store,
            tmp_path / "v2",
            "image-compile-input",
            case.compile_input,
        )
    else:
        compile_input = _artifact(
            "harness.video2env_compile_input.v2",
            digest="e" * 64,
        )
    payload = receipt_v1.model_dump(mode="json")
    payload.update(
        {
            "schema_version": X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID,
            "skill_ref": f"{route}.compile@1.0.0",
            "compile_input": compile_input.model_dump(mode="json"),
        }
    )
    payload["receipt_sha256"] = evidence.canonical_evidence_sha256(payload, "receipt_sha256")
    return case, payload


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    payload["receipt_sha256"] = evidence.canonical_evidence_sha256(payload, "receipt_sha256")
    return payload


def test_compile_v2_symbols_are_public_and_identity_preserving() -> None:
    expected = {
        "X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID": X2ENV_COMPILE_V2_RECEIPT_SCHEMA_ID,
        "X2EnvCompileReceiptV2": X2EnvCompileReceiptV2,
        "IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID": IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        "Image2EnvCompileV2Output": Image2EnvCompileV2Output,
        "VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID": VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
        "Video2EnvCompileV2Output": Video2EnvCompileV2Output,
    }
    assert all(value is not None for value in expected.values()), "x2env compile v2 seam is absent"
    for name, value in expected.items():
        assert getattr(schemas, name) is value
        assert getattr(harness, name) is value


@pytest.mark.parametrize(
    ("skill_ref", "expected_output_schema"),
    [
        ("image2env.compile@1.0.0", "harness.image2env_compile_output.v2"),
        ("video2env.compile@1.0.0", "harness.video2env_compile_output.v2"),
    ],
)
def test_compile_v1_skill_descriptors_select_v2_outputs(
    skill_ref: str,
    expected_output_schema: str,
) -> None:
    contract = x2env_skill_contract(skill_ref)
    assert contract.output_schema == expected_output_schema
    assert contract.handler_registered is False
    assert contract.qualification_available is False


@_requires_v2
def test_receipt_v2_binds_the_exact_route_compile_input_and_self_hash(tmp_path: Path) -> None:
    assert X2EnvCompileReceiptV2 is not None
    for route in ("image2env", "video2env"):
        _, payload = _receipt_v2_payload(tmp_path / route, route=route)
        receipt = X2EnvCompileReceiptV2.model_validate(payload)
        assert receipt.compile_input.schema_version == f"harness.{route}_compile_input.v2"
        assert receipt.receipt_sha256 == evidence.canonical_evidence_sha256(
            receipt, "receipt_sha256"
        )

    _, valid = _receipt_v2_payload(tmp_path / "negative")
    mutations = []
    wrong_input_schema = dict(valid)
    wrong_input_schema["compile_input"] = _artifact(
        "harness.image2env_compile_input.v1", digest="1" * 64
    ).model_dump(mode="json")
    mutations.append(_rehash(wrong_input_schema))
    wrong_route = dict(valid)
    wrong_route["compile_input"] = _artifact(
        "harness.video2env_compile_input.v2", digest="2" * 64
    ).model_dump(mode="json")
    mutations.append(_rehash(wrong_route))
    wrong_ref = dict(valid)
    wrong_ref["compile_input"] = {
        **valid["compile_input"],  # type: ignore[misc]
        "uri": "artifact://sha256/" + "3" * 64,
    }
    mutations.append(_rehash(wrong_ref))
    wrong_media = dict(valid)
    wrong_media["compile_input"] = {
        **valid["compile_input"],  # type: ignore[misc]
        "media_type": "text/plain",
    }
    mutations.append(_rehash(wrong_media))
    wrong_hash = dict(valid)
    wrong_hash["receipt_sha256"] = "4" * 64
    mutations.append(wrong_hash)
    wrong_skill = dict(valid)
    wrong_skill["skill_ref"] = "text2env.compile@1.0.0"
    mutations.append(_rehash(wrong_skill))
    extra = {**valid, "unexpected": True}
    mutations.append(extra)
    for mutation in mutations:
        with pytest.raises(ValidationError):
            X2EnvCompileReceiptV2.model_validate(mutation)

    predecessor = dict(valid)
    predecessor["predecessor_package"] = _artifact(
        "harness.x2env_environment_package.v1", digest="5" * 64
    ).model_dump(mode="json")
    assert X2EnvCompileReceiptV2.model_validate(_rehash(predecessor)).predecessor_package


@_requires_v2
def test_output_v2_requires_the_v2_receipt_and_preserves_route(tmp_path: Path) -> None:
    assert Image2EnvCompileV2Output is not None
    assert Video2EnvCompileV2Output is not None
    case, receipt_payload = _receipt_v2_payload(tmp_path)
    receipt = X2EnvCompileReceiptV2.model_validate(receipt_payload)  # type: ignore[union-attr]
    receipt_ref = _publish_model(case.store, tmp_path / "v2", "compile-receipt", receipt)
    image_payload = case.compile_output.model_dump(mode="json")
    image_payload.update(
        {
            "schema_version": IMAGE2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
            "compile_receipt": receipt_ref.model_dump(mode="json"),
        }
    )
    output = Image2EnvCompileV2Output.model_validate(image_payload)
    assert output.compile_receipt == receipt_ref

    video_source = schemas.VideoSourceMedia(
        schema_version="harness.video_source_media.v1",
        source_media_sha256="5" * 64,
        source=_artifact("harness.video_bytes.v1", digest="5" * 64, media_type="video/mp4"),
        probe=_artifact("harness.video_media_probe.v1", digest="6" * 64),
        frame_sequence_manifest=_artifact("harness.video_frame_sequence.v1", digest="7" * 64),
        width=64,
        height=48,
        duration_ms=600,
        fps_numerator=30,
        fps_denominator=1,
        decoded_frame_count=18,
        unique_frame_count=16,
        frame_sequence_sha256="8" * 64,
    )
    video_package = case.compile_output.environment_package.model_copy(
        update={
            "route_id": "video2env",
            "producer_skill_ref": "video2env.compile@1.0.0",
            "source_media": video_source,
        }
    )
    with pytest.raises(ValidationError, match="image2env"):
        Image2EnvCompileV2Output.model_validate(
            {**image_payload, "environment_package": video_package.model_dump(mode="json")}
        )

    _, video_receipt_payload = _receipt_v2_payload(tmp_path / "video", route="video2env")
    video_receipt = X2EnvCompileReceiptV2.model_validate(video_receipt_payload)
    video_receipt_ref = _publish_model(
        case.store, tmp_path / "v2", "video-compile-receipt", video_receipt
    )
    video_output = Video2EnvCompileV2Output.model_validate(
        {
            **image_payload,
            "schema_version": VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID,
            "environment_package": video_package.model_dump(mode="json"),
            "compile_receipt": video_receipt_ref.model_dump(mode="json"),
        }
    )
    assert video_output.environment_package.route_id == "video2env"

    old_receipt = {
        **image_payload,
        "compile_receipt": case.refs["receipt"].model_dump(mode="json"),
    }
    with pytest.raises(ValidationError, match="compile_receipt"):
        Image2EnvCompileV2Output.model_validate(old_receipt)
    with pytest.raises(ValidationError, match="video2env"):
        Video2EnvCompileV2Output.model_validate(
            {**image_payload, "schema_version": VIDEO2ENV_COMPILE_V2_OUTPUT_SCHEMA_ID}
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Image2EnvCompileV2Output.model_validate({**image_payload, "unexpected": True})


@_requires_v2
def test_three_v2_schemas_are_standalone_snapshotted_and_catalogued() -> None:
    expected_ids = {
        "harness.x2env_compile_receipt.v2",
        "harness.image2env_compile_output.v2",
        "harness.video2env_compile_output.v2",
    }
    documents = schema_documents()
    assert len(documents) == 97
    assert expected_ids.issubset(documents)
    for schema_id in expected_ids:
        assert schema_model(schema_id).model_json_schema()["$id"] == schema_id
        Draft202012Validator.check_schema(documents[schema_id])
        snapshot = DEFAULT_SCHEMA_ROOT / f"{schema_id}.schema.json"
        assert snapshot.read_text(encoding="utf-8") == render_schema_document(documents[schema_id])
    export_schema_snapshots(check=True)


def test_v1_receipt_and_outputs_remain_parseable_with_byte_identical_snapshots(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    receipt = evidence.X2EnvCompileReceiptV1.model_validate_json(
        case.store.resolve(case.refs["receipt"]).path.read_bytes()
    )
    assert receipt.schema_version == "harness.x2env_compile_receipt.v1"
    Image2EnvCompileOutput.model_validate(case.compile_output.model_dump(mode="json"))

    video_payload = case.compile_output.model_dump(mode="json")
    video_payload["schema_version"] = "harness.video2env_compile_output.v1"
    video_payload["environment_package"]["route_id"] = "video2env"
    video_payload["environment_package"]["producer_skill_ref"] = "video2env.compile@1.0.0"
    video_payload["environment_package"]["source_media"] = {
        "schema_version": "harness.video_source_media.v1",
        "source_media_sha256": "5" * 64,
        "source": _artifact(
            "harness.video_bytes.v1", digest="5" * 64, media_type="video/mp4"
        ).model_dump(mode="json"),
        "probe": _artifact("harness.video_media_probe.v1", digest="6" * 64).model_dump(mode="json"),
        "frame_sequence_manifest": _artifact(
            "harness.video_frame_sequence.v1", digest="7" * 64
        ).model_dump(mode="json"),
        "width": 64,
        "height": 48,
        "duration_ms": 600,
        "fps_numerator": 30,
        "fps_denominator": 1,
        "decoded_frame_count": 18,
        "unique_frame_count": 16,
        "frame_sequence_sha256": "8" * 64,
    }
    Video2EnvCompileOutput.model_validate(video_payload)
    for name, expected_sha256 in _V1_SNAPSHOT_SHA256.items():
        payload = (DEFAULT_SCHEMA_ROOT / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
