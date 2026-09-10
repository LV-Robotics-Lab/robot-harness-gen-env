from __future__ import annotations

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

from self_improving.harness.schema_catalog import schema_documents
from self_improving.harness.schemas.common import ArtifactRef
from self_improving.harness.schemas.compile_attempt import (
    CompileAttemptV1,
    InitialCompileAttemptV1,
    PromptRevisionCompileAttemptV1,
)
from self_improving.harness.schemas.x2env import (
    Image2EnvCompileInput,
    Image2EnvCompileV2Input,
    Video2EnvCompileInput,
    Video2EnvCompileV2Input,
)
from self_improving.harness.x2env_contracts import x2env_skill_contract
from tests.self_improving.harness.test_x2env_contracts import compile_input

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def artifact(
    name: str,
    schema_version: str | None,
    *,
    sha256: str,
    media_type: str = "application/json",
) -> dict[str, object]:
    return {
        "name": name,
        "uri": f"artifact://sha256/{sha256}",
        "media_type": media_type,
        "sha256": sha256,
        "bytes": 123,
        "schema_version": schema_version,
    }


def revised_attempt() -> dict[str, object]:
    return {
        "schema_version": "harness.prompt_revision_compile_attempt.v1",
        "kind": "prompt_revision",
        "prompt_revision": artifact(
            "prompt_revision",
            "harness.prompt_revision.v1",
            sha256=SHA_A,
        ),
        "effective_prompt": artifact(
            "effective_prompt",
            "harness.prompt_text.v1",
            sha256=SHA_B,
            media_type="text/plain",
        ),
        "failure_receipts": [
            artifact(
                "failure_b",
                "harness.workflow_operation_receipt.v2",
                sha256=SHA_C,
            ),
            artifact(
                "failure_c",
                "harness.workflow_operation_receipt.v2",
                sha256=SHA_D,
            ),
        ],
        "predecessor_package": artifact(
            "predecessor_package",
            "harness.x2env_environment_package.v1",
            sha256=SHA_C,
        ),
    }


def test_compile_attempt_is_a_strict_discriminated_union() -> None:
    adapter = TypeAdapter(CompileAttemptV1)
    initial = adapter.validate_python(
        {
            "schema_version": "harness.initial_compile_attempt.v1",
            "kind": "initial",
        }
    )
    revised = adapter.validate_python(revised_attempt())

    assert type(initial) is InitialCompileAttemptV1
    assert type(revised) is PromptRevisionCompileAttemptV1
    assert isinstance(revised.prompt_revision, ArtifactRef)
    assert revised.effective_prompt.media_type == "text/plain"

    invalid = revised_attempt()
    invalid["kind"] = "initial"
    with pytest.raises(ValidationError):
        adapter.validate_python(invalid)

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "schema_version": "harness.initial_compile_attempt.v1",
                "kind": "initial",
                "prompt_revision": revised_attempt()["prompt_revision"],
            }
        )


def test_prompt_revision_attempt_rejects_incomplete_or_noncanonical_lineage() -> None:
    adapter = TypeAdapter(CompileAttemptV1)

    invalid_payloads: list[dict[str, object]] = []

    empty_failures = revised_attempt()
    empty_failures["failure_receipts"] = []
    invalid_payloads.append(empty_failures)

    unsorted_failures = revised_attempt()
    unsorted_failures["failure_receipts"] = list(
        reversed(unsorted_failures["failure_receipts"])  # type: ignore[arg-type]
    )
    invalid_payloads.append(unsorted_failures)

    duplicate_failures = revised_attempt()
    duplicate_failures["failure_receipts"] = [
        duplicate_failures["failure_receipts"][0],  # type: ignore[index]
        duplicate_failures["failure_receipts"][0],  # type: ignore[index]
    ]
    invalid_payloads.append(duplicate_failures)

    wrong_prompt_revision = revised_attempt()
    wrong_prompt_revision["prompt_revision"]["schema_version"] = "wrong.v1"  # type: ignore[index]
    invalid_payloads.append(wrong_prompt_revision)

    wrong_effective_prompt = revised_attempt()
    wrong_effective_prompt["effective_prompt"]["media_type"] = "application/json"  # type: ignore[index]
    invalid_payloads.append(wrong_effective_prompt)

    wrong_predecessor = revised_attempt()
    wrong_predecessor["predecessor_package"]["schema_version"] = "wrong.v1"  # type: ignore[index]
    invalid_payloads.append(wrong_predecessor)

    wrong_failure = revised_attempt()
    wrong_failure["failure_receipts"][0]["schema_version"] = "wrong.v2"  # type: ignore[index]
    invalid_payloads.append(wrong_failure)

    non_cas_prompt_revision = revised_attempt()
    non_cas_prompt_revision["prompt_revision"]["uri"] = (  # type: ignore[index]
        f"artifact://sha256/{SHA_D}"
    )
    invalid_payloads.append(non_cas_prompt_revision)

    unknown_field = revised_attempt()
    unknown_field["fallback"] = {"kind": "none"}
    invalid_payloads.append(unknown_field)

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            adapter.validate_python(deepcopy(payload))


@pytest.mark.parametrize(
    ("route_id", "legacy_model", "current_model"),
    (
        ("image2env", Image2EnvCompileInput, Image2EnvCompileV2Input),
        ("video2env", Video2EnvCompileInput, Video2EnvCompileV2Input),
    ),
)
def test_x2env_compile_v2_requires_attempt_while_v1_remains_parse_only(
    route_id: str,
    legacy_model: type,
    current_model: type,
) -> None:
    legacy_payload = compile_input(route_id)
    assert type(legacy_model.model_validate(legacy_payload)) is legacy_model

    v2_payload = {
        **legacy_payload,
        "schema_version": f"harness.{route_id}_compile_input.v2",
        "attempt": revised_attempt(),
    }
    parsed = current_model.model_validate(v2_payload)
    assert type(parsed) is current_model
    assert type(parsed.attempt) is PromptRevisionCompileAttemptV1

    missing_attempt = dict(v2_payload)
    missing_attempt.pop("attempt")
    with pytest.raises(ValidationError):
        current_model.model_validate(missing_attempt)

    contract = x2env_skill_contract(f"{route_id}.compile@1.0.0")
    assert contract.mcp_tool_name == f"{route_id}_compile_v1_0_0"
    assert contract.input_schema == f"harness.{route_id}_compile_input.v2"


def test_compile_attempt_and_x2env_v2_documents_are_standalone_draft_2020_12() -> None:
    image_payload = {
        **compile_input("image2env"),
        "schema_version": "harness.image2env_compile_input.v2",
        "attempt": revised_attempt(),
    }
    video_payload = {
        **compile_input("video2env"),
        "schema_version": "harness.video2env_compile_input.v2",
        "attempt": {
            "schema_version": "harness.initial_compile_attempt.v1",
            "kind": "initial",
        },
    }
    examples = {
        "harness.initial_compile_attempt.v1": {
            "schema_version": "harness.initial_compile_attempt.v1",
            "kind": "initial",
        },
        "harness.prompt_revision_compile_attempt.v1": revised_attempt(),
        "harness.image2env_compile_input.v2": image_payload,
        "harness.video2env_compile_input.v2": video_payload,
    }
    documents = schema_documents()

    for schema_id, example in examples.items():
        document = documents[schema_id]
        assert document["$id"] == schema_id
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["additionalProperties"] is False
        Draft202012Validator.check_schema(document)
        Draft202012Validator(document).validate(json.loads(json.dumps(example)))


def test_compile_attempt_and_x2env_v2_models_are_public_lazy_facades() -> None:
    import self_improving.harness as harness
    import self_improving.harness.schemas as schemas

    expected = {
        "InitialCompileAttemptV1": InitialCompileAttemptV1,
        "PromptRevisionCompileAttemptV1": PromptRevisionCompileAttemptV1,
        "Image2EnvCompileV2Input": Image2EnvCompileV2Input,
        "Video2EnvCompileV2Input": Video2EnvCompileV2Input,
    }
    for name, model in expected.items():
        assert getattr(schemas, name) is model
        assert getattr(harness, name) is model
        assert name in schemas.__all__
        assert name in harness.__all__
