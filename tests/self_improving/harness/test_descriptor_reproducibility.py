from __future__ import annotations

import pytest
from pydantic import ValidationError

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
