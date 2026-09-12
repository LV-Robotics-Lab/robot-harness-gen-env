from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.events import RecordingEventSink
from self_improving.harness.handlers.text2env_compile import text2env_compile_descriptor
from self_improving.harness.handlers.text2env_replay import text2env_replay_descriptor
from self_improving.harness.registry import (
    HandlerResult,
    RegistryRegistrationError,
    SkillRegistry,
    StaticDependencyResolver,
)
from self_improving.harness.schemas import (
    ArtifactRef,
    ExecutionReproducibility,
    SkillDescriptor,
    SkillDescriptorV2,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _qualification_ref() -> ArtifactRef:
    return ArtifactRef(
        name="qualification",
        uri=f"artifact://sha256/{SHA_A}",
        media_type="application/json",
        sha256=SHA_A,
        bytes=10,
        schema_version="harness.skill_qualification.v1",
    )


def _descriptor_payload(*, reproducibility: str) -> dict[str, object]:
    return {
        "skill_id": "text2env.replay",
        "version": "1.0.0",
        "mcp_tool_name": "text2env_replay_v1_0_0",
        "input_schema": "harness.text2env_replay_input.v1",
        "output_schema": "harness.text2env_replay_output.v1",
        "implementation_name": "self_improving.harness.handlers.text2env_replay",
        "implementation_version": "1",
        "implementation_sha256": SHA_B,
        "reproducibility": reproducibility,
        "max_attempts": 2,
        "qualification_artifact": _qualification_ref().model_dump(mode="json"),
    }


def test_v1_descriptor_remains_strict_and_is_not_silently_upgraded() -> None:
    legacy = {
        **_descriptor_payload(
            reproducibility=ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC.value
        ),
        "deterministic": True,
    }
    del legacy["reproducibility"]

    descriptor = SkillDescriptor.model_validate(legacy)

    assert descriptor.model_json_schema()["$id"] == "harness.skill_descriptor.v1"
    assert descriptor.deterministic is True
    with pytest.raises(ValidationError):
        SkillDescriptorV2.model_validate(legacy)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SkillDescriptor.model_validate(
            {
                **legacy,
                "reproducibility": (ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC.value),
            }
        )
    with pytest.raises(ValidationError):
        SkillDescriptor.model_validate({**legacy, "deterministic": False})


@pytest.mark.parametrize(
    "reproducibility",
    [
        ExecutionReproducibility.CONTENT_BITWISE_DETERMINISTIC,
        ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
    ],
)
def test_v2_descriptor_has_one_explicit_reproducibility_claim(
    reproducibility: ExecutionReproducibility,
) -> None:
    descriptor = SkillDescriptorV2.model_validate(
        _descriptor_payload(reproducibility=reproducibility.value)
    )

    assert descriptor.model_json_schema()["$id"] == "harness.skill_descriptor.v2"
    assert descriptor.reproducibility is reproducibility
    assert "deterministic" not in descriptor.model_dump(mode="json")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"deterministic": True}, "Extra inputs are not permitted"),
        ({"reproducibility": "best_effort"}, "Input should be"),
        ({"mcp_tool_name": "text2env_replay_v2_0_0"}, "mcp_tool_name must be"),
    ],
)
def test_v2_descriptor_rejects_ambiguous_or_inconsistent_claims(
    change: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        SkillDescriptorV2.model_validate(
            {
                **_descriptor_payload(
                    reproducibility=(ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE.value)
                ),
                **change,
            }
        )


def test_v2_descriptor_still_requires_v1_qualification_receipt() -> None:
    payload = _descriptor_payload(
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE.value
    )
    payload["qualification_artifact"] = {
        **_qualification_ref().model_dump(mode="json"),
        "schema_version": "harness.skill_qualification.v2",
    }

    with pytest.raises(ValidationError, match="qualification_artifact.schema_version"):
        SkillDescriptorV2.model_validate(payload)


def _put_json(
    store: LocalArtifactStore,
    path: Path,
    *,
    name: str,
    schema_version: str,
    payload: object,
) -> ArtifactRef:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return store.put_file(
        path,
        name=name,
        media_type="application/json",
        schema_version=schema_version,
    )


def test_registry_rejects_unverified_v2_descriptor_without_conversion(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    report = _put_json(
        store,
        tmp_path / "report.json",
        name="qualification_report",
        schema_version="harness.skill_qualification_report.v1",
        payload={"case": "repeatable-physical-case", "status": "pass"},
    )
    qualification = _put_json(
        store,
        tmp_path / "qualification.json",
        name="qualification",
        schema_version="harness.skill_qualification.v1",
        payload={
            "skill_ref": "test.repeatable@1.0.0",
            "status": "pass",
            "deterministic_case_id": "repeatable-physical-case",
            "regression_command": "pytest -q",
            "report_sha256": report.sha256,
        },
    )
    descriptor = SkillDescriptorV2(
        skill_id="test.repeatable",
        version="1.0.0",
        mcp_tool_name="test_repeatable_v1_0_0",
        input_schema="harness.artifact_ref.v1",
        output_schema="harness.artifact_ref.v1",
        implementation_name="tests.repeatable",
        implementation_version="1",
        implementation_sha256=SHA_B,
        reproducibility=ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE,
        max_attempts=1,
        qualification_artifact=qualification,
    )
    registry = SkillRegistry(
        artifact_resolver=store,
        dependency_resolver=StaticDependencyResolver({}),
        event_sink=RecordingEventSink(),
        clock=lambda: datetime(2026, 8, 31, tzinfo=timezone.utc),
        run_id_factory=lambda: UUID("12345678-1234-4234-9234-123456789abc"),
    )

    with pytest.raises(RegistryRegistrationError, match="verified evidence-invariant"):
        registry.register(descriptor, lambda value, _context: HandlerResult(output=value))

    assert registry.list() == ()


def test_production_factories_make_truthful_versioned_claims() -> None:
    qualification = _qualification_ref()

    compile_descriptor = text2env_compile_descriptor(
        qualification_artifact=qualification,
        implementation_sha256=SHA_B,
    )
    replay_descriptor = text2env_replay_descriptor(
        qualification_artifact=qualification,
        implementation_sha256=SHA_B,
    )

    assert type(compile_descriptor) is SkillDescriptor
    assert compile_descriptor.deterministic is True
    assert type(replay_descriptor) is SkillDescriptorV2
    assert (
        replay_descriptor.reproducibility is ExecutionReproducibility.EVIDENCE_INVARIANT_REPEATABLE
    )
