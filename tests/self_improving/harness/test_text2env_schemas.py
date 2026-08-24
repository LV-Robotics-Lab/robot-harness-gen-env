from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from self_improving.harness.schemas.common import ValidationStatus
from self_improving.harness.schemas.text2env import (
    CompileConfig,
    EnvironmentPackage,
    RuntimeConfig,
    Text2EnvCompileInput,
    Text2EnvCompileOutput,
    Text2EnvReplayInput,
    Text2EnvReplayOutput,
    Text2EnvValidateInput,
    Text2EnvValidateOutput,
)


def test_compile_config_and_runtime_defaults_are_expanded() -> None:
    assert CompileConfig().model_dump() == {"generate_missing_assets": False}
    assert RuntimeConfig().model_dump() == {
        "precheck_steps": 0,
        "settle_steps": 900,
        "contact_window_steps": 120,
        "video_frames": 120,
        "fps": 12,
    }

    assert CompileConfig(generate_missing_assets=True).generate_missing_assets is True
    with pytest.raises(ValidationError):
        CompileConfig(generate_missing_assets=1)

    for field, value in (
        ("precheck_steps", -1),
        ("settle_steps", 0),
        ("contact_window_steps", 0),
        ("video_frames", -1),
        ("fps", 0),
        ("fps", "12"),
    ):
        with pytest.raises(ValidationError):
            RuntimeConfig.model_validate({field: value})


def test_environment_package_enforces_route_and_hash_binding(
    environment_package_payload,
) -> None:
    payload = environment_package_payload()
    package = EnvironmentPackage.model_validate(payload)
    assert package.package_id == package.resolved_scene_sha256
    assert package.route_id == "text2env"

    cases = [
        ("package_id", "d" * 64, "package_id must equal"),
        ("producer_skill_ref", "text2env.replay@1.0.0", "must identify text2env.compile"),
        (
            "asset_catalog.schema_version",
            "robotwin.asset_catalog.v2",
            "asset_catalog.schema_version",
        ),
        (
            "package_manifest.schema_version",
            "robotwin.generated_scene_package.v2",
            "package_manifest.schema_version",
        ),
    ]
    for path, value, message in cases:
        candidate = deepcopy(payload)
        target = candidate
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        with pytest.raises(ValidationError, match=message):
            EnvironmentPackage.model_validate(candidate)

    invalid_route = deepcopy(payload)
    invalid_route["route_id"] = "anchor2env"
    with pytest.raises(ValidationError):
        EnvironmentPackage.model_validate(invalid_route)


def test_compile_input_preserves_request_and_requires_typed_catalog(
    artifact_payload,
) -> None:
    request = "  Place a can on top of a plate.  "
    payload = {
        "request": request,
        "seed": 42,
        "asset_catalog": artifact_payload("robotwin.asset_catalog.v1"),
        "config": {},
    }
    value = Text2EnvCompileInput.model_validate(payload)
    assert value.request == request
    assert value.config.generate_missing_assets is False

    wrong_catalog = deepcopy(payload)
    wrong_catalog["asset_catalog"]["schema_version"] = "robotwin.other.v1"
    with pytest.raises(ValidationError, match="asset_catalog.schema_version"):
        Text2EnvCompileInput.model_validate(wrong_catalog)

    for field, invalid in (("seed", -1), ("seed", True), ("request", "x")):
        candidate = deepcopy(payload)
        candidate[field] = invalid
        with pytest.raises(ValidationError):
            Text2EnvCompileInput.model_validate(candidate)

    missing_config = deepcopy(payload)
    del missing_config["config"]
    with pytest.raises(ValidationError, match="Field required"):
        Text2EnvCompileInput.model_validate(missing_config)


def test_compile_output_requires_all_authoritative_artifact_schemas(
    artifact_payload,
    environment_package_payload,
) -> None:
    payload = {
        "scene_spec": artifact_payload("robotwin.scene_spec.v1", name="scene_spec"),
        "resolved_scene": artifact_payload(
            "robotwin.resolved_scene.v1",
            name="resolved_scene",
        ),
        "environment_package": environment_package_payload(),
        "static_validation": artifact_payload(
            "robotwin.scene_validation.v1",
            name="static_validation",
        ),
    }
    assert Text2EnvCompileOutput.model_validate(payload).scene_spec.name == "scene_spec"

    for field in ("scene_spec", "resolved_scene", "static_validation"):
        invalid = deepcopy(payload)
        invalid[field]["schema_version"] = "robotwin.wrong.v1"
        with pytest.raises(ValidationError, match=field):
            Text2EnvCompileOutput.model_validate(invalid)


def test_replay_schemas_require_explicit_config_and_runtime_evidence_type(
    artifact_payload,
    environment_package_payload,
) -> None:
    input_payload = {
        "environment_package": environment_package_payload(),
        "runtime_config": {},
    }
    replay_input = Text2EnvReplayInput.model_validate(input_payload)
    assert replay_input.runtime_config.contact_window_steps == 120

    missing_config = deepcopy(input_payload)
    del missing_config["runtime_config"]
    with pytest.raises(ValidationError, match="Field required"):
        Text2EnvReplayInput.model_validate(missing_config)

    output_payload = {
        "runtime_evidence": artifact_payload(
            "robotwin.scene_runtime_evidence.v2",
            name="runtime_evidence",
        ),
        "replay_artifacts": [artifact_payload(None, name="video", media_type="video/mp4")],
    }
    replay_output = Text2EnvReplayOutput.model_validate(output_payload)
    assert replay_output.replay_artifacts[0].schema_version is None

    wrong_runtime = deepcopy(output_payload)
    wrong_runtime["runtime_evidence"]["schema_version"] = "robotwin.scene_runtime_evidence.v1"
    with pytest.raises(ValidationError, match="runtime_evidence.schema_version"):
        Text2EnvReplayOutput.model_validate(wrong_runtime)


def test_validate_input_defaults_gate_and_rejects_wrong_evidence(
    artifact_payload,
    environment_package_payload,
) -> None:
    payload = {
        "environment_package": environment_package_payload(),
        "runtime_evidence": artifact_payload(
            "robotwin.scene_runtime_evidence.v2",
            name="runtime_evidence",
        ),
    }
    value = Text2EnvValidateInput.model_validate(payload)
    assert value.gate_profile == "robotwin.scene_validation.v1"

    wrong_gate = deepcopy(payload)
    wrong_gate["gate_profile"] = "robotwin.scene_validation.v2"
    with pytest.raises(ValidationError):
        Text2EnvValidateInput.model_validate(wrong_gate)

    wrong_runtime = deepcopy(payload)
    wrong_runtime["runtime_evidence"]["schema_version"] = "robotwin.scene_runtime_evidence.v1"
    with pytest.raises(ValidationError, match="runtime_evidence.schema_version"):
        Text2EnvValidateInput.model_validate(wrong_runtime)


def test_validate_output_distinguishes_skill_success_from_publication(
    artifact_payload,
    blocker_payload,
) -> None:
    report = artifact_payload("robotwin.scene_validation.v1", name="validation_report")
    publishable = Text2EnvValidateOutput(
        validation_report=report,
        validation_status="pass",
        publishable=True,
        blockers=(),
    )
    assert publishable.validation_status == ValidationStatus.PASS

    rejected = Text2EnvValidateOutput(
        validation_report=report,
        validation_status="fail",
        publishable=False,
        blockers=(blocker_payload(),),
    )
    assert rejected.publishable is False

    incomplete = Text2EnvValidateOutput(
        validation_report=report,
        validation_status="incomplete",
        publishable=False,
        blockers=(blocker_payload(code="T2E_VALIDATION_INCOMPLETE"),),
    )
    assert incomplete.validation_status == ValidationStatus.INCOMPLETE

    wrong_report = artifact_payload("robotwin.scene_validation.v2", name="validation_report")
    with pytest.raises(ValidationError, match="validation_report.schema_version"):
        Text2EnvValidateOutput(
            validation_report=wrong_report,
            validation_status="pass",
            publishable=True,
            blockers=(),
        )

    for status, blockers in (("fail", ()), ("pass", (blocker_payload(),))):
        with pytest.raises(ValidationError, match="publishable output requires"):
            Text2EnvValidateOutput(
                validation_report=report,
                validation_status=status,
                publishable=True,
                blockers=blockers,
            )

    with pytest.raises(ValidationError, match="at least one blocker"):
        Text2EnvValidateOutput(
            validation_report=report,
            validation_status="pass",
            publishable=False,
            blockers=(),
        )
