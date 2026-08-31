from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.runtime_assets import canonical_runtime_asset_manifest_bytes
from self_improving.harness.schemas import ArtifactRef
from self_improving.harness.schemas.text2env_validate_v2 import (
    Text2EnvValidateV2Input,
    Text2EnvValidateV2Output,
)
from self_improving.harness.validate_v2 import (
    StrictValidateV2CasReader,
    ValidateV2EvidenceError,
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


def _replace_artifact(
    store: LocalArtifactStore,
    tmp_path: Path,
    value: Text2EnvValidateV2Input,
    *,
    field_name: str,
    payload: bytes,
) -> Text2EnvValidateV2Input:
    original = getattr(value, field_name)
    replacement = _put(
        store,
        tmp_path,
        name=f"replacement_{field_name}",
        schema_version=original.schema_version,
        payload=payload,
        media_type=original.media_type,
    )
    replacement = replacement.model_copy(update={"name": original.name})
    return value.model_copy(update={field_name: replacement})


def test_strict_reader_loads_every_raw_ref_from_one_cas(tmp_path: Path) -> None:
    store, value, payloads = _fixture_input(tmp_path)

    loaded = StrictValidateV2CasReader(store).load(value)

    assert loaded.field_names == (*JSON_CONTRACTS, TRANSCRIPT_FIELD)
    for field_name, expected in payloads.items():
        artifact = loaded.artifact(field_name)
        assert artifact.payload == expected
        assert artifact.ref == getattr(value, field_name)
    with pytest.raises(KeyError):
        loaded.artifact("not_an_input")


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


def test_reader_rechecks_model_constructed_refs_and_cas_bytes(tmp_path: Path) -> None:
    store, value, _ = _fixture_input(tmp_path)
    original = value.compile_run_receipt
    forged = original.model_copy(update={"uri": "file:///tmp/copied.json"})
    constructed = value.model_copy(update={"compile_run_receipt": forged})
    with pytest.raises(ValidateV2EvidenceError, match="canonical CAS locator") as error:
        StrictValidateV2CasReader(store).load(constructed)
    assert error.value.reason == "invalid_reference"

    cas_path = store.root / "sha256" / original.sha256[:2] / original.sha256
    cas_path.write_bytes(b"{}")
    with pytest.raises(ValidateV2EvidenceError, match="content identity") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_mismatch"


def test_reader_interface_rejects_injected_types(tmp_path: Path) -> None:
    store, value, _ = _fixture_input(tmp_path)
    with pytest.raises(TypeError, match="LocalArtifactStore"):
        StrictValidateV2CasReader(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Text2EnvValidateV2Input"):
        StrictValidateV2CasReader(store).load(value.model_dump())  # type: ignore[arg-type]


def test_reader_rejects_missing_oversize_and_nonregular_objects(tmp_path: Path) -> None:
    store, value, _ = _fixture_input(tmp_path)
    ref = value.environment_package
    cas_path = store.root / "sha256" / ref.sha256[:2] / ref.sha256
    cas_path.unlink()
    with pytest.raises(ValidateV2EvidenceError, match="unavailable") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_unavailable"

    cas_path.write_bytes(b"x" * ref.bytes)
    oversize_ref = ref.model_copy(update={"bytes": 16 * 1024 * 1024 + 1})
    oversize = value.model_copy(update={"environment_package": oversize_ref})
    with pytest.raises(ValidateV2EvidenceError, match="byte limit") as error:
        StrictValidateV2CasReader(store).load(oversize)
    assert error.value.reason == "artifact_too_large"

    cas_path.unlink()
    os.mkfifo(cas_path)
    with pytest.raises(ValidateV2EvidenceError, match="regular CAS file") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_unsafe"


def test_reader_leaves_json_semantics_to_authoritative_later_verifiers(tmp_path: Path) -> None:
    store, value, _ = _fixture_input(tmp_path)
    replacement = _replace_artifact(
        store,
        tmp_path,
        value,
        field_name="environment_package",
        payload=b"not-json-yet",
    )

    loaded = StrictValidateV2CasReader(store).load(replacement)

    assert loaded.artifact("environment_package").payload == b"not-json-yet"


def test_reader_detects_same_size_drift_and_read_time_changes(tmp_path: Path, monkeypatch) -> None:
    store, value, _ = _fixture_input(tmp_path)
    ref = value.environment_package
    cas_path = store.root / "sha256" / ref.sha256[:2] / ref.sha256
    cas_path.write_bytes(b"x" * ref.bytes)
    with pytest.raises(ValidateV2EvidenceError, match="ArtifactRef") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_mismatch"

    expected = json.dumps(
        {"field": "environment_package"}, sort_keys=True, separators=(",", ":")
    ).encode()
    cas_path.write_bytes(expected)
    monkeypatch.setattr("self_improving.harness.validate_v2.os.read", lambda _fd, _size: b"")
    with pytest.raises(ValidateV2EvidenceError, match="ArtifactRef"):
        StrictValidateV2CasReader(store).load(value)


def test_reader_detects_file_growth_after_declared_bytes(tmp_path: Path, monkeypatch) -> None:
    store, value, _ = _fixture_input(tmp_path)
    real_read = os.read

    def growing_read(file_descriptor: int, size: int) -> bytes:
        if size == 1:
            return b"x"
        return real_read(file_descriptor, size)

    monkeypatch.setattr("self_improving.harness.validate_v2.os.read", growing_read)
    with pytest.raises(ValidateV2EvidenceError, match="changed while") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_mismatch"


def test_reader_pins_one_cas_root_for_the_complete_load(tmp_path: Path, monkeypatch) -> None:
    store, value, payloads = _fixture_input(tmp_path)
    replacement_store = LocalArtifactStore(tmp_path / "replacement-cas")
    replacement_store.root.mkdir(parents=True)
    real_read = os.read
    switched = False

    def switch_root_after_first_payload(file_descriptor: int, size: int) -> bytes:
        nonlocal switched
        payload = real_read(file_descriptor, size)
        if payload and not switched:
            store.root = replacement_store.root
            switched = True
        return payload

    monkeypatch.setattr(
        "self_improving.harness.validate_v2.os.read",
        switch_root_after_first_payload,
    )

    loaded = StrictValidateV2CasReader(store).load(value)

    assert switched is True
    assert loaded.artifact("runtime_event_transcript").payload == payloads[TRANSCRIPT_FIELD]


@pytest.mark.parametrize(
    ("replacement", "reason", "message"),
    [
        ("missing", "artifact_unavailable", "CAS root is unavailable"),
        ("symlink", "artifact_unsafe", "regular directory tree"),
    ],
)
def test_reader_rejects_unavailable_or_replaced_cas_root(
    tmp_path: Path,
    replacement: str,
    reason: str,
    message: str,
) -> None:
    store, value, _ = _fixture_input(tmp_path)
    reader = StrictValidateV2CasReader(store)
    original_root = store.root
    moved_root = tmp_path / "moved-cas"
    original_root.rename(moved_root)
    if replacement == "symlink":
        original_root.symlink_to(moved_root, target_is_directory=True)

    with pytest.raises(ValidateV2EvidenceError, match=message) as error:
        reader.load(value)

    assert error.value.reason == reason


def test_reader_accepts_producer_json_formats_and_rejects_symlinked_member(
    tmp_path: Path,
) -> None:
    store, value, _ = _fixture_input(tmp_path)
    original = value.environment_package
    cas_path = store.root / "sha256" / original.sha256[:2] / original.sha256
    formatted = _replace_artifact(
        store,
        tmp_path,
        value,
        field_name="environment_package",
        payload=b'{\n  "field": "environment_package"\n}\n',
    )
    manifest_payload = canonical_runtime_asset_manifest_bytes(
        {"files": [], "schema_version": "harness.runtime_asset_snapshot.v1"}
    )
    producer_compatible = _replace_artifact(
        store,
        tmp_path,
        formatted,
        field_name="runtime_asset_snapshot_manifest",
        payload=manifest_payload,
    )

    loaded = StrictValidateV2CasReader(store).load(producer_compatible)

    assert loaded.artifact("environment_package").payload == (
        b'{\n  "field": "environment_package"\n}\n'
    )
    assert loaded.artifact("runtime_asset_snapshot_manifest").payload == manifest_payload

    replacement_ref = producer_compatible.environment_package
    replacement_path = store.root / "sha256" / replacement_ref.sha256[:2] / replacement_ref.sha256
    replacement_path.unlink()
    os.symlink(cas_path, replacement_path)
    with pytest.raises(ValidateV2EvidenceError, match="regular CAS file") as error:
        StrictValidateV2CasReader(store).load(producer_compatible)
    assert error.value.reason == "artifact_unsafe"


@pytest.mark.parametrize(
    "mutation",
    [
        {"sha256": "../secret", "uri": "artifact://sha256/../secret"},
        {"bytes": True},
        {"name": ""},
    ],
)
def test_reader_strictly_revalidates_model_copied_refs_before_open(
    tmp_path: Path,
    monkeypatch,
    mutation: dict[str, object],
) -> None:
    store, value, _ = _fixture_input(tmp_path)
    forged = value.environment_package.model_copy(update=mutation)
    constructed = value.model_copy(update={"environment_package": forged})
    monkeypatch.setattr(
        "self_improving.harness.validate_v2.os.open",
        lambda *_args, **_kwargs: pytest.fail("invalid input reached the filesystem"),
    )

    with pytest.raises(ValidateV2EvidenceError, match="strict typed schema") as error:
        StrictValidateV2CasReader(store).load(constructed)

    assert error.value.reason == "invalid_reference"


def test_reader_revalidates_copied_gate_profile_before_open(tmp_path: Path, monkeypatch) -> None:
    store, value, _ = _fixture_input(tmp_path)
    constructed = value.model_copy(update={"gate_profile": "weaker.profile.v1"})
    monkeypatch.setattr(
        "self_improving.harness.validate_v2.os.open",
        lambda *_args, **_kwargs: pytest.fail("invalid input reached the filesystem"),
    )

    with pytest.raises(ValidateV2EvidenceError, match="strict typed schema") as error:
        StrictValidateV2CasReader(store).load(constructed)

    assert error.value.reason == "invalid_reference"


def test_reader_validates_all_refs_then_deduplicates_content(tmp_path: Path, monkeypatch) -> None:
    store, value, _ = _fixture_input(tmp_path)
    shared = value.compile_run_receipt.model_copy(update={"name": "replay_run_receipt"})
    deduplicated = value.model_copy(update={"replay_run_receipt": shared})
    real_open = os.open
    shared_file_opens = 0

    def counted_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal shared_file_opens
        if path == shared.sha256:
            shared_file_opens += 1
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("self_improving.harness.validate_v2.os.open", counted_open)

    loaded = StrictValidateV2CasReader(store).load(deduplicated)

    assert (
        loaded.artifact("compile_run_receipt").payload
        == loaded.artifact("replay_run_receipt").payload
    )
    assert shared_file_opens == 1


def test_reader_rejects_conflicting_or_aggregate_oversize_refs_before_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, value, _ = _fixture_input(tmp_path)
    shared = value.compile_run_receipt.model_copy(
        update={"name": "replay_run_receipt", "bytes": value.compile_run_receipt.bytes + 1}
    )
    conflict = value.model_copy(update={"replay_run_receipt": shared})
    with pytest.raises(ValidateV2EvidenceError, match="conflicts") as error:
        StrictValidateV2CasReader(store).load(conflict)
    assert error.value.reason == "invalid_reference"

    monkeypatch.setattr("self_improving.harness.validate_v2._MAX_TOTAL_ARTIFACT_BYTES", 1)
    monkeypatch.setattr(
        "self_improving.harness.validate_v2.os.open",
        lambda *_args, **_kwargs: pytest.fail("oversize input reached the filesystem"),
    )
    with pytest.raises(ValidateV2EvidenceError, match="aggregate byte limit") as error:
        StrictValidateV2CasReader(store).load(value)
    assert error.value.reason == "artifact_too_large"


def test_runtime_asset_manifest_uses_its_authoritative_larger_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, value, _ = _fixture_input(tmp_path)
    payload = json.dumps({"padding": "x" * 512}, separators=(",", ":")).encode()
    replacement = _replace_artifact(
        store,
        tmp_path,
        value,
        field_name="runtime_asset_snapshot_manifest",
        payload=payload,
    )
    monkeypatch.setattr("self_improving.harness.validate_v2._MAX_JSON_ARTIFACT_BYTES", 128)

    loaded = StrictValidateV2CasReader(store).load(replacement)

    assert loaded.artifact("runtime_asset_snapshot_manifest").payload == payload


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
