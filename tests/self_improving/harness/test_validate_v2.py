from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.schemas import ArtifactRef
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)

JSON_CONTRACTS = {
    "environment_package": "harness.environment_package.v1",
    "compile_run_receipt": "harness.portable_run_receipt.v1",
    "compile_qualification": "harness.skill_qualification.v1",
    "replay_run_receipt": "harness.portable_run_receipt.v1",
    "replay_qualification": "harness.skill_qualification.v1",
    "replay_execution_receipt": "harness.text2env_replay_receipt.v1",
    "runtime_evidence": "robotwin.scene_runtime_evidence.v2",
    "runtime_validation_report": "robotwin.scene_validation.v1",
    "runtime_asset_snapshot_manifest": "harness.runtime_asset_snapshot.v1",
    "media_verification_receipt": "harness.replay_media_verification.v1",
    "request_provenance": "harness.text2env_request_provenance.v1",
}
TRANSCRIPT_FIELD = "runtime_event_transcript"


def _put(
    store: LocalArtifactStore,
    tmp_path: Path,
    *,
    name: str,
    schema_version: str,
    payload: bytes,
    media_type: str = "application/json",
) -> ArtifactRef:
    path = tmp_path / f"{name}.payload"
    path.write_bytes(payload)
    return store.put_file(
        path,
        name=name,
        media_type=media_type,
        schema_version=schema_version,
    )


def _fixture_input(
    tmp_path: Path,
) -> tuple[LocalArtifactStore, Text2EnvValidateV2Input, dict[str, bytes]]:
    store = LocalArtifactStore(tmp_path / "cas")
    refs: dict[str, ArtifactRef] = {}
    payloads: dict[str, bytes] = {}
    for field_name, schema_version in JSON_CONTRACTS.items():
        payload = json.dumps(
            {"field": field_name},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        payloads[field_name] = payload
        refs[field_name] = _put(
            store,
            tmp_path,
            name=field_name,
            schema_version=schema_version,
            payload=payload,
        )
    transcript = b'{"kind":"preflight.completed","seq":1}\n'
    payloads[TRANSCRIPT_FIELD] = transcript
    refs[TRANSCRIPT_FIELD] = _put(
        store,
        tmp_path,
        name=TRANSCRIPT_FIELD,
        schema_version="harness.runtime_event_transcript.v1",
        payload=transcript,
        media_type="application/x-ndjson",
    )
    return (
        store,
        Text2EnvValidateV2Input.model_validate(
            {
                **{name: ref.model_dump(mode="json") for name, ref in refs.items()},
                "gate_profile": "robotwin.scene_validation.v1",
            }
        ),
        payloads,
    )


def test_v2_input_rejects_each_wrong_locator_before_content_dedup(tmp_path: Path) -> None:
    _, value, _ = _fixture_input(tmp_path)
    payload = value.model_dump(mode="json")
    valid = payload["environment_package"]
    forged = dict(valid, name="compile_run_receipt", uri="file:///tmp/copied.json")
    forged["schema_version"] = "harness.portable_run_receipt.v1"
    payload["compile_run_receipt"] = forged

    with pytest.raises(ValidationError, match="compile_run_receipt.uri"):
        Text2EnvValidateV2Input.model_validate(payload)


@pytest.mark.parametrize("field_name", [*JSON_CONTRACTS, TRANSCRIPT_FIELD])
def test_v2_input_rejects_wrong_schema_or_media(
    tmp_path: Path,
    field_name: str,
) -> None:
    _, value, _ = _fixture_input(tmp_path)
    payload = value.model_dump(mode="json")
    payload[field_name]["schema_version"] = "wrong.v1"
    with pytest.raises(ValidationError, match=field_name):
        Text2EnvValidateV2Input.model_validate(payload)

    payload = value.model_dump(mode="json")
    payload[field_name]["media_type"] = "application/octet-stream"
    with pytest.raises(ValidationError, match=field_name):
        Text2EnvValidateV2Input.model_validate(payload)


def test_v2_output_requires_bound_decision_and_consistent_publication(
    tmp_path: Path,
    blocker_payload,
) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    report = _put(
        store,
        tmp_path,
        name="validation_report",
        schema_version="robotwin.scene_validation.v1",
        payload=b"{}",
    )
    decision = _put(
        store,
        tmp_path,
        name="validation_decision_receipt",
        schema_version="harness.text2env_validation_decision.v2",
        payload=b"{}",
    )
    output = Text2EnvValidateV2Output(
        validation_report=report,
        validation_decision_receipt=decision,
        validation_status="pass",
        publishable=True,
        blockers=(),
    )
    assert output.publishable is True

    bound_blocker = blocker_payload()
    bound_blocker["artifact_refs"] = [report.model_dump(mode="json")]
    rejected = Text2EnvValidateV2Output(
        validation_report=report,
        validation_decision_receipt=decision,
        validation_status="fail",
        publishable=False,
        blockers=(bound_blocker,),
    )
    assert rejected.validation_status.value == "fail"

    with pytest.raises(ValidationError, match="canonical CAS locator"):
        Text2EnvValidateV2Output(
            validation_report=report,
            validation_decision_receipt=decision,
            validation_status="fail",
            publishable=False,
            blockers=(blocker_payload(),),
        )

    blocker_ref_items = Text2EnvValidateV2Output.model_json_schema()["properties"]["blockers"][
        "items"
    ]["allOf"][1]["properties"]["artifact_refs"]["items"]
    assert blocker_ref_items["properties"]["uri"]["pattern"] == (
        r"^artifact://sha256/[0-9a-f]{64}$"
    )
    assert blocker_ref_items["properties"]["uri"]["minLength"] == 82
    assert blocker_ref_items["properties"]["uri"]["maxLength"] == 82

    schema_validator = Draft202012Validator(Text2EnvValidateV2Output.model_json_schema())
    newline_direct_ref = output.model_dump(mode="json")
    newline_direct_ref["validation_report"]["uri"] += "\n"
    assert list(schema_validator.iter_errors(newline_direct_ref))

    newline_blocker_uri = {"uri": f"artifact://sha256/{report.sha256}\n"}
    assert list(Draft202012Validator(blocker_ref_items).iter_errors(newline_blocker_uri))

    with pytest.raises(ValidationError, match="publishable output requires"):
        Text2EnvValidateV2Output(
            validation_report=report,
            validation_decision_receipt=decision,
            validation_status="fail",
            publishable=True,
            blockers=(),
        )
    with pytest.raises(ValidationError, match="at least one blocker"):
        Text2EnvValidateV2Output(
            validation_report=report,
            validation_decision_receipt=decision,
            validation_status="pass",
            publishable=False,
            blockers=(),
        )
