from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from self_improving.harness.schema_catalog import DEFAULT_SCHEMA_ROOT
from self_improving.harness.schemas.x2env import (
    AssetAcquisitionLane,
    Image2EnvCompileInput,
    Image2EnvCompileOutput,
    Image2EnvReplayInput,
    Image2EnvReplayOutput,
    Image2EnvValidateInput,
    Image2EnvValidateOutput,
    ImageSourceMedia,
    RequestedGenesisProfile,
    Video2EnvCompileInput,
    Video2EnvCompileOutput,
    Video2EnvReplayInput,
    Video2EnvReplayOutput,
    Video2EnvValidateInput,
    Video2EnvValidateOutput,
    VideoSourceMedia,
    X2EnvAssetDecision,
    X2EnvEnvironmentPackage,
)
from self_improving.harness.x2env_contracts import (
    X2ENV_SKILL_CONTRACTS,
    build_x2env_skill_descriptor,
    x2env_skill_contract,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def artifact(
    name: str,
    schema_version: str | None,
    *,
    sha256: str = SHA_A,
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


def image_media() -> dict[str, object]:
    return {
        "schema_version": "harness.image_source_media.v1",
        "source_media_sha256": SHA_A,
        "source": artifact("source_image", None, media_type="image/jpeg"),
        "canonical_image": artifact(
            "canonical_image", None, sha256=SHA_B, media_type="image/png"
        ),
        "probe": artifact("image_probe", "harness.image_media_probe.v1", sha256=SHA_C),
        "width": 640,
        "height": 480,
        "color_mode": "RGB",
        "decoded_frame_count": 1,
        "unique_frame_count": 1,
    }


def video_media() -> dict[str, object]:
    return {
        "schema_version": "harness.video_source_media.v1",
        "source_media_sha256": SHA_A,
        "source": artifact("source_video", None, media_type="video/mp4"),
        "probe": artifact("video_probe", "harness.video_media_probe.v1", sha256=SHA_B),
        "frame_sequence_manifest": artifact(
            "frame_sequence", "harness.video_frame_sequence.v1", sha256=SHA_C
        ),
        "width": 1280,
        "height": 720,
        "duration_ms": 10_000,
        "fps_numerator": 30,
        "fps_denominator": 1,
        "decoded_frame_count": 300,
        "unique_frame_count": 287,
        "frame_sequence_sha256": SHA_D,
    }


def package(route_id: str) -> dict[str, object]:
    source_media = image_media() if route_id == "image2env" else video_media()
    return {
        "schema_version": "harness.x2env_environment_package.v1",
        "package_id": SHA_D,
        "route_id": route_id,
        "producer_skill_ref": f"{route_id}.compile@1.0.0",
        "source_media": source_media,
        "requested_profile": "genesis.rigid_scene@1",
        "environment_format": "harness.genesis_portable.v1",
        "backend": "genesis",
        "representation_origin": "harness_native",
        "legacy_adapter_receipt": None,
        "scene_ir": artifact("scene_ir", "harness.scene_ir.v1"),
        "task_ir": artifact("task_ir", "harness.task_ir.v1", sha256=SHA_B),
        "asset_closure_manifest": artifact(
            "asset_closure", "harness.asset_closure_manifest.v1", sha256=SHA_C
        ),
        "binding_manifest": artifact(
            "binding_manifest", "harness.x2env_binding_manifest.v1", sha256=SHA_D
        ),
        "runtime_lock": artifact("runtime_lock", "harness.genesis_runtime_lock.v1"),
        "simulator_ready": False,
        "collision_qualified": False,
    }


def decision(lane: str = "exact_asset_reuse") -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "harness.x2env_asset_decision.v1",
        "object_id": "mouse",
        "lane": lane,
        "selected_artifact": artifact("selected_asset", "harness.asset_representation.v1"),
        "decision_evidence": artifact(
            "decision", "harness.x2env_asset_selection.v1", sha256=SHA_B
        ),
        "source_qualification": artifact(
            "qualification", "harness.asset_qualification.v1", sha256=SHA_C
        ),
        "digital_cousin_evidence": None,
        "generation_receipt": None,
    }
    if lane == "digital_cousin_reuse":
        value["digital_cousin_evidence"] = artifact(
            "cousin", "harness.digital_cousin_equivalence.v1", sha256=SHA_D
        )
    if lane == "fresh_asset_generation":
        value["source_qualification"] = None
        value["generation_receipt"] = artifact(
            "generation", "harness.asset_generation_receipt.v1", sha256=SHA_D
        )
    return value


def compile_input(route_id: str) -> dict[str, object]:
    return {
        "schema_version": f"harness.{route_id}_compile_input.v1",
        "source_media": image_media() if route_id == "image2env" else video_media(),
        "interpretation_proposal": artifact(
            "proposal", "harness.scene_interpretation_proposal.v1"
        ),
        "requested_profile": "genesis.rigid_scene@1",
        "acquisition_policy": "digital_cousin_allowed",
        "asset_catalog": artifact(
            "asset_catalog", "harness.portable_asset_catalog.v1", sha256=SHA_B
        ),
        "seed": 17,
        "predecessor_package": None,
        "repair_evidence_refs": (),
        "model_execution_allowed": False,
    }


def compile_output(route_id: str) -> dict[str, object]:
    return {
        "schema_version": f"harness.{route_id}_compile_output.v1",
        "environment_package": package(route_id),
        "scene_ir": artifact("scene_ir", "harness.scene_ir.v1"),
        "task_ir": artifact("task_ir", "harness.task_ir.v1", sha256=SHA_B),
        "asset_decisions": [decision()],
        "staged_assets": [],
        "static_validation": artifact(
            "static_validation", "harness.x2env_static_validation.v1", sha256=SHA_C
        ),
        "compile_receipt": artifact(
            "compile_receipt", "harness.x2env_compile_receipt.v1", sha256=SHA_D
        ),
        "simulator_executed": False,
        "publishable": False,
    }


def replay_input(route_id: str, *, profile: str = "genesis.rigid_scene@1") -> dict[str, object]:
    value = package(route_id)
    value["requested_profile"] = profile
    return {
        "schema_version": f"harness.{route_id}_replay_input.v1",
        "environment_package": value,
        "compile_receipt": artifact(
            "compile_receipt", "harness.x2env_compile_receipt.v1"
        ),
        "requested_profile": profile,
        "runtime_attestation": artifact(
            "runtime", "harness.genesis_runtime_attestation.v1", sha256=SHA_B
        ),
        "seed": 17,
        "physics_plan": {
            "collision_enabled": True,
            "settle_steps": 900,
            "observation_steps": 120,
            "video_frames": 120,
            "fps": 12,
            "robot_probe_steps": 0 if profile == "genesis.rigid_scene@1" else 8,
        },
    }


def replay_output(route_id: str, *, profile: str = "genesis.rigid_scene@1") -> dict[str, object]:
    environment_package = package(route_id)
    environment_package["requested_profile"] = profile
    value = {
        "schema_version": f"harness.{route_id}_replay_output.v1",
        "environment_package": environment_package,
        "package_id": SHA_D,
        "requested_profile": profile,
        "runtime_evidence": artifact(
            "runtime_evidence", "harness.genesis_runtime_evidence.v1"
        ),
        "physics_report": artifact(
            "physics_report", "harness.genesis_physics_report.v1", sha256=SHA_B
        ),
        "robot_probe_report": None,
        "trajectory": None,
        "video": artifact("replay_video", None, sha256=SHA_C, media_type="video/mp4"),
        "frame_sequence_manifest": artifact(
            "replay_frames", "harness.video_frame_sequence.v1", sha256=SHA_D
        ),
        "media_verification": artifact(
            "media_verification", "harness.x2env_media_verification.v1"
        ),
        "runtime_receipt": artifact(
            "runtime_receipt", "harness.x2env_runtime_receipt.v1", sha256=SHA_B
        ),
        "fresh_observation_refs": [
            artifact("observation", "harness.fresh_observation.v1", sha256=SHA_C)
        ],
        "steps_executed": 1020,
        "total_frame_count": 120,
        "unique_frame_count": 119,
        "collisions_enabled": True,
        "simulator_executed": True,
    }
    if profile == "genesis.robot_policy@1":
        value["robot_probe_report"] = artifact(
            "robot_probe", "harness.genesis_robot_probe.v1", sha256=SHA_C
        )
        value["trajectory"] = artifact(
            "trajectory", "harness.genesis_trajectory.v1", sha256=SHA_D
        )
    return value


def validate_input(route_id: str) -> dict[str, object]:
    return {
        "schema_version": f"harness.{route_id}_validate_input.v1",
        "environment_package": package(route_id),
        "compile_receipt": artifact(
            "compile_receipt", "harness.x2env_compile_receipt.v1"
        ),
        "compile_qualification": artifact(
            "compile_qualification", "harness.skill_qualification.v1", sha256=SHA_B
        ),
        "replay_receipt": artifact(
            "replay_receipt", "harness.x2env_runtime_receipt.v1", sha256=SHA_C
        ),
        "replay_qualification": artifact(
            "replay_qualification", "harness.skill_qualification.v1", sha256=SHA_D
        ),
        "runtime_evidence": artifact(
            "runtime_evidence", "harness.genesis_runtime_evidence.v1"
        ),
        "physics_report": artifact(
            "physics_report", "harness.genesis_physics_report.v1", sha256=SHA_B
        ),
        "media_verification": artifact(
            "media", "harness.x2env_media_verification.v1", sha256=SHA_C
        ),
        "requested_profile": "genesis.rigid_scene@1",
        "fresh_observation_refs": [
            artifact("observation", "harness.fresh_observation.v1", sha256=SHA_D)
        ],
    }


def blocker() -> dict[str, object]:
    return {
        "code": "HARN_VALIDATION_INCOMPLETE",
        "message": "runtime gate is incomplete",
        "stage": "validate",
        "retryable": False,
        "details": {},
        "unknowns": (),
        "artifact_refs": (),
    }


def validate_output(route_id: str, *, publishable: bool = True) -> dict[str, object]:
    return {
        "schema_version": f"harness.{route_id}_validate_output.v1",
        "validation_decision": artifact(
            "validation_decision", "harness.x2env_validation_decision.v1"
        ),
        "validation_status": "pass" if publishable else "incomplete",
        "publishable": publishable,
        "requested_profile": "genesis.rigid_scene@1",
        "satisfied_profiles": ["genesis.rigid_scene@1"] if publishable else [],
        "blockers": [] if publishable else [blocker()],
        "portable_closure_reloaded": True,
        "evidence_recomputed": True,
    }


@pytest.mark.parametrize(
    ("model", "example"),
    (
        pytest.param(ImageSourceMedia, image_media(), id="image-source-media"),
        pytest.param(VideoSourceMedia, video_media(), id="video-source-media"),
        pytest.param(X2EnvAssetDecision, decision(), id="asset-decision"),
        pytest.param(
            X2EnvEnvironmentPackage,
            package("image2env"),
            id="environment-package",
        ),
        pytest.param(
            Image2EnvCompileInput,
            compile_input("image2env"),
            id="image-compile-input",
        ),
        pytest.param(
            Image2EnvCompileOutput,
            compile_output("image2env"),
            id="image-compile-output",
        ),
        pytest.param(
            Image2EnvReplayInput,
            replay_input("image2env"),
            id="image-replay-input",
        ),
        pytest.param(
            Image2EnvReplayOutput,
            replay_output("image2env"),
            id="image-replay-output",
        ),
        pytest.param(
            Image2EnvValidateInput,
            validate_input("image2env"),
            id="image-validate-input",
        ),
        pytest.param(
            Image2EnvValidateOutput,
            validate_output("image2env"),
            id="image-validate-output",
        ),
        pytest.param(
            Video2EnvCompileInput,
            compile_input("video2env"),
            id="video-compile-input",
        ),
        pytest.param(
            Video2EnvCompileOutput,
            compile_output("video2env"),
            id="video-compile-output",
        ),
        pytest.param(
            Video2EnvReplayInput,
            replay_input("video2env"),
            id="video-replay-input",
        ),
        pytest.param(
            Video2EnvReplayOutput,
            replay_output("video2env"),
            id="video-replay-output",
        ),
        pytest.param(
            Video2EnvValidateInput,
            validate_input("video2env"),
            id="video-validate-input",
        ),
        pytest.param(
            Video2EnvValidateOutput,
            validate_output("video2env"),
            id="video-validate-output",
        ),
    ),
)
def test_x2env_public_schema_documents_validate_canonical_examples_standalone(
    model: type,
    example: dict[str, object],
) -> None:
    canonical_json = json.loads(model.model_validate(example).model_dump_json())
    schema_id = canonical_json["schema_version"]
    snapshot = json.loads(
        (DEFAULT_SCHEMA_ROOT / f"{schema_id}.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(snapshot).validate(canonical_json)


def test_image_and_video_source_media_are_distinct_hash_bound_identities() -> None:
    image = ImageSourceMedia.model_validate(image_media())
    video = VideoSourceMedia.model_validate(video_media())
    assert image.source_media_sha256 == image.source.sha256
    assert image.decoded_frame_count == image.unique_frame_count == 1
    assert video.unique_frame_count < video.decoded_frame_count

    for payload, model, path, replacement in (
        (image_media(), ImageSourceMedia, "source_media_sha256", SHA_D),
        (image_media(), ImageSourceMedia, "source.media_type", "video/mp4"),
        (image_media(), ImageSourceMedia, "canonical_image.media_type", "image/jpeg"),
        (video_media(), VideoSourceMedia, "source.media_type", "image/png"),
        (video_media(), VideoSourceMedia, "unique_frame_count", 301),
        (video_media(), VideoSourceMedia, "frame_sequence_manifest.schema_version", "wrong.v1"),
    ):
        invalid = deepcopy(payload)
        target = invalid
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]  # type: ignore[index,assignment]
        target[parts[-1]] = replacement  # type: ignore[index]
        with pytest.raises(ValidationError):
            model.model_validate(invalid)

    mixed = image_media()
    mixed["schema_version"] = "harness.video_source_media.v1"
    with pytest.raises(ValidationError):
        VideoSourceMedia.model_validate(mixed)


def test_environment_package_keeps_legacy_output_a_non_ready_candidate() -> None:
    native = X2EnvEnvironmentPackage.model_validate(package("image2env"))
    assert native.simulator_ready is False
    assert native.collision_qualified is False

    legacy = package("video2env")
    legacy["representation_origin"] = "legacy_genenv_adapted"
    legacy["legacy_adapter_receipt"] = artifact(
        "adapter", "harness.legacy_genenv_adapter_receipt.v1"
    )
    assert X2EnvEnvironmentPackage.model_validate(legacy).representation_origin.value == (
        "legacy_genenv_adapted"
    )

    for field, value in (
        ("producer_skill_ref", "image2env.compile@1.0.0"),
        ("source_media", image_media()),
        ("legacy_adapter_receipt", None),
        ("simulator_ready", True),
        ("collision_qualified", True),
    ):
        invalid = deepcopy(legacy)
        invalid[field] = value
        with pytest.raises(ValidationError):
            X2EnvEnvironmentPackage.model_validate(invalid)

    invalid_native = package("image2env")
    invalid_native["legacy_adapter_receipt"] = artifact(
        "adapter", "harness.legacy_genenv_adapter_receipt.v1"
    )
    with pytest.raises(ValidationError):
        X2EnvEnvironmentPackage.model_validate(invalid_native)


def test_asset_decision_lanes_make_reuse_cousin_and_generation_explicit() -> None:
    for lane in AssetAcquisitionLane:
        value = X2EnvAssetDecision.model_validate(decision(lane.value))
        assert value.lane is lane

    invalid = decision("digital_cousin_reuse")
    invalid["digital_cousin_evidence"] = None
    with pytest.raises(ValidationError, match="digital_cousin_evidence"):
        X2EnvAssetDecision.model_validate(invalid)

    invalid = decision("fresh_asset_generation")
    invalid["generation_receipt"] = None
    with pytest.raises(ValidationError, match="generation_receipt"):
        X2EnvAssetDecision.model_validate(invalid)

    invalid = decision()
    invalid["source_qualification"] = None
    with pytest.raises(ValidationError, match="source_qualification"):
        X2EnvAssetDecision.model_validate(invalid)

    invalid = decision()
    invalid["digital_cousin_evidence"] = artifact(
        "cousin", "harness.digital_cousin_equivalence.v1"
    )
    with pytest.raises(ValidationError, match="only valid"):
        X2EnvAssetDecision.model_validate(invalid)


@pytest.mark.parametrize(
    ("route_id", "model"),
    [("image2env", Image2EnvCompileInput), ("video2env", Video2EnvCompileInput)],
)
def test_compile_input_is_modality_specific_and_forbids_internal_model_execution(
    route_id: str, model: type[Image2EnvCompileInput] | type[Video2EnvCompileInput]
) -> None:
    value = model.model_validate(compile_input(route_id))
    assert value.requested_profile is RequestedGenesisProfile.RIGID_SCENE
    assert value.model_execution_allowed is False

    wrong_media = compile_input(route_id)
    wrong_media["source_media"] = (
        video_media() if route_id == "image2env" else image_media()
    )
    with pytest.raises(ValidationError):
        model.model_validate(wrong_media)

    internal_model = compile_input(route_id)
    internal_model["model_execution_allowed"] = True
    with pytest.raises(ValidationError):
        model.model_validate(internal_model)

    bad_ref = compile_input(route_id)
    bad_ref["interpretation_proposal"] = artifact("proposal", "wrong.proposal.v1")
    with pytest.raises(ValidationError):
        model.model_validate(bad_ref)


@pytest.mark.parametrize(
    ("route_id", "model"),
    [("image2env", Image2EnvCompileOutput), ("video2env", Video2EnvCompileOutput)],
)
def test_compile_output_is_a_static_candidate_not_runtime_or_publishability_proof(
    route_id: str, model: type[Image2EnvCompileOutput] | type[Video2EnvCompileOutput]
) -> None:
    value = model.model_validate(compile_output(route_id))
    assert value.environment_package.route_id == route_id
    assert value.simulator_executed is value.publishable is False

    generated = compile_output(route_id)
    generated["asset_decisions"] = [decision("fresh_asset_generation")]
    with pytest.raises(ValidationError, match="staged_assets"):
        model.model_validate(generated)
    generated["staged_assets"] = [
        artifact("staged", "harness.staged_asset_candidate.v1")
    ]
    assert model.model_validate(generated).staged_assets

    for field in ("simulator_executed", "publishable"):
        invalid = compile_output(route_id)
        invalid[field] = True
        with pytest.raises(ValidationError):
            model.model_validate(invalid)


@pytest.mark.parametrize(
    ("route_id", "input_model", "output_model"),
    [
        ("image2env", Image2EnvReplayInput, Image2EnvReplayOutput),
        ("video2env", Video2EnvReplayInput, Video2EnvReplayOutput),
    ],
)
def test_replay_contract_requires_real_steps_collision_and_sequential_media(
    route_id: str,
    input_model: type[Image2EnvReplayInput] | type[Video2EnvReplayInput],
    output_model: type[Image2EnvReplayOutput] | type[Video2EnvReplayOutput],
) -> None:
    request = input_model.model_validate(replay_input(route_id))
    result = output_model.model_validate(replay_output(route_id))
    assert request.physics_plan.collision_enabled is True
    assert result.simulator_executed is result.collisions_enabled is True
    assert result.steps_executed > 0

    robot_request = input_model.model_validate(
        replay_input(route_id, profile="genesis.robot_policy@1")
    )
    robot_result = output_model.model_validate(
        replay_output(route_id, profile="genesis.robot_policy@1")
    )
    assert robot_request.physics_plan.robot_probe_steps == 8
    assert robot_result.robot_probe_report is not None
    assert robot_result.trajectory is not None

    invalid_request = replay_input(route_id, profile="genesis.robot_policy@1")
    invalid_request["physics_plan"]["robot_probe_steps"] = 0  # type: ignore[index]
    with pytest.raises(ValidationError, match="robot_probe_steps"):
        input_model.model_validate(invalid_request)

    for field, value in (
        ("unique_frame_count", 121),
        ("video.media_type", "image/png"),
        ("package_id", SHA_A),
    ):
        invalid = replay_output(route_id)
        target = invalid
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[part]  # type: ignore[index,assignment]
        target[parts[-1]] = value  # type: ignore[index]
        with pytest.raises(ValidationError):
            output_model.model_validate(invalid)

    missing_robot_evidence = replay_output(route_id, profile="genesis.robot_policy@1")
    missing_robot_evidence["trajectory"] = None
    with pytest.raises(ValidationError, match="robot_probe_report and trajectory"):
        output_model.model_validate(missing_robot_evidence)


@pytest.mark.parametrize(
    ("route_id", "input_model", "output_model"),
    [
        ("image2env", Image2EnvValidateInput, Image2EnvValidateOutput),
        ("video2env", Video2EnvValidateInput, Video2EnvValidateOutput),
    ],
)
def test_validate_is_the_only_publishability_boundary_and_fails_closed(
    route_id: str,
    input_model: type[Image2EnvValidateInput] | type[Video2EnvValidateInput],
    output_model: type[Image2EnvValidateOutput] | type[Video2EnvValidateOutput],
) -> None:
    request = input_model.model_validate(validate_input(route_id))
    passed = output_model.model_validate(validate_output(route_id))
    incomplete = output_model.model_validate(validate_output(route_id, publishable=False))
    assert request.requested_profile is RequestedGenesisProfile.RIGID_SCENE
    assert passed.publishable is True
    assert incomplete.publishable is False

    invalid_input = validate_input(route_id)
    invalid_input["requested_profile"] = "genesis.robot_policy@1"
    with pytest.raises(ValidationError, match="requested_profile"):
        input_model.model_validate(invalid_input)

    for mutation in (
        {"validation_status": "fail"},
        {"blockers": [blocker()]},
        {"satisfied_profiles": []},
    ):
        invalid = validate_output(route_id)
        invalid.update(mutation)
        with pytest.raises(ValidationError, match="publishable"):
            output_model.model_validate(invalid)

    no_blocker = validate_output(route_id, publishable=False)
    no_blocker["blockers"] = []
    with pytest.raises(ValidationError, match="non-publishable"):
        output_model.model_validate(no_blocker)


def test_exact_skill_namespace_has_six_contract_only_descriptors() -> None:
    expected = {
        "image2env.compile@1.0.0": (
            "image2env_compile_v1_0_0",
            "harness.image2env_compile_input.v1",
            "harness.image2env_compile_output.v1",
        ),
        "image2env.replay@1.0.0": (
            "image2env_replay_v1_0_0",
            "harness.image2env_replay_input.v1",
            "harness.image2env_replay_output.v1",
        ),
        "image2env.validate@1.0.0": (
            "image2env_validate_v1_0_0",
            "harness.image2env_validate_input.v1",
            "harness.image2env_validate_output.v1",
        ),
        "video2env.compile@1.0.0": (
            "video2env_compile_v1_0_0",
            "harness.video2env_compile_input.v1",
            "harness.video2env_compile_output.v1",
        ),
        "video2env.replay@1.0.0": (
            "video2env_replay_v1_0_0",
            "harness.video2env_replay_input.v1",
            "harness.video2env_replay_output.v1",
        ),
        "video2env.validate@1.0.0": (
            "video2env_validate_v1_0_0",
            "harness.video2env_validate_input.v1",
            "harness.video2env_validate_output.v1",
        ),
    }
    assert set(X2ENV_SKILL_CONTRACTS) == set(expected)
    for skill_ref, values in expected.items():
        contract = x2env_skill_contract(skill_ref)
        assert (contract.mcp_tool_name, contract.input_schema, contract.output_schema) == values
        assert contract.handler_registered is False
        assert contract.qualification_available is False

    with pytest.raises(KeyError):
        x2env_skill_contract("image2env.compile@2.0.0")


def test_descriptor_construction_is_guarded_by_real_qualification() -> None:
    qualification = artifact(
        "qualification", "harness.skill_qualification.v1", sha256=SHA_D
    )
    with pytest.raises(RuntimeError, match="verified qualification"):
        build_x2env_skill_descriptor(
            "image2env.compile@1.0.0",
            qualification_artifact=qualification,
            implementation_name="self_improving.harness.handlers.image2env_compile",
            implementation_sha256=SHA_C,
            qualification_verified=False,
        )

    descriptor = build_x2env_skill_descriptor(
        "video2env.validate@1.0.0",
        qualification_artifact=qualification,
        implementation_name="self_improving.harness.handlers.video2env_validate",
        implementation_sha256=SHA_C,
        qualification_verified=True,
    )
    assert descriptor.skill_id == "video2env.validate"
    assert descriptor.mcp_tool_name == "video2env_validate_v1_0_0"
    assert descriptor.input_schema == "harness.video2env_validate_input.v1"
    assert descriptor.output_schema == "harness.video2env_validate_output.v1"


def test_x2env_contracts_are_available_from_the_public_harness_facades() -> None:
    import self_improving.harness as harness
    import self_improving.harness.schemas as schemas

    model_names = {
        "ImageSourceMedia",
        "VideoSourceMedia",
        "X2EnvAssetDecision",
        "X2EnvEnvironmentPackage",
        "Image2EnvCompileInput",
        "Image2EnvCompileOutput",
        "Image2EnvReplayInput",
        "Image2EnvReplayOutput",
        "Image2EnvValidateInput",
        "Image2EnvValidateOutput",
        "Video2EnvCompileInput",
        "Video2EnvCompileOutput",
        "Video2EnvReplayInput",
        "Video2EnvReplayOutput",
        "Video2EnvValidateInput",
        "Video2EnvValidateOutput",
    }
    for name in model_names:
        assert getattr(harness, name) is getattr(schemas, name)
        assert name in harness.__all__
        assert name in schemas.__all__
    assert harness.X2ENV_SKILL_CONTRACTS is X2ENV_SKILL_CONTRACTS
    assert harness.x2env_skill_contract is x2env_skill_contract
    assert harness.build_x2env_skill_descriptor is build_x2env_skill_descriptor


def test_legacy_gujie_mapping_fixtures_preserve_the_sample_only_gap() -> None:
    fixture_root = Path(__file__).resolve().parents[2] / "fixtures"
    image = json.loads(
        (fixture_root / "legacy_genenv_image_media_manifest.json").read_text(encoding="utf-8")
    )
    video = json.loads(
        (fixture_root / "legacy_genenv_video_media_manifest.json").read_text(encoding="utf-8")
    )

    assert image["schema_version"] == video["schema_version"] == (
        "genenv.media_reconstruction.v1"
    )
    assert image["mode"] == "image"
    assert image["decoded_frame_count"] == image["unique_frame_count"] == 1
    assert video["mode"] == "video"
    assert video["decoded_frame_count"] == 300
    assert video["sampled_frame_count"] == len(video["sampled_frame_indices"]) == 15
    assert "frame_hashes" not in video
    assert "frame_sequence_sha256" not in video


def test_remaining_cross_field_attacks_fail_closed() -> None:
    bad_uri = image_media()
    bad_uri["source"]["uri"] = "file:///legacy/input/scene.jpg"  # type: ignore[index]
    with pytest.raises(ValidationError, match="content-addressed"):
        ImageSourceMedia.model_validate(bad_uri)

    bad_video_hash = video_media()
    bad_video_hash["source_media_sha256"] = SHA_D
    with pytest.raises(ValidationError, match="source_media_sha256"):
        VideoSourceMedia.model_validate(bad_video_hash)

    generated_claims_prior_qualification = decision("fresh_asset_generation")
    generated_claims_prior_qualification["source_qualification"] = artifact(
        "qualification", "harness.asset_qualification.v1"
    )
    with pytest.raises(ValidationError, match="cannot claim"):
        X2EnvAssetDecision.model_validate(generated_claims_prior_qualification)

    reused_claims_generation = decision()
    reused_claims_generation["generation_receipt"] = artifact(
        "generation", "harness.asset_generation_receipt.v1"
    )
    with pytest.raises(ValidationError, match="only valid"):
        X2EnvAssetDecision.model_validate(reused_claims_generation)

    for route_id, wrong_source in (
        ("image2env", video_media()),
        ("video2env", image_media()),
    ):
        mismatched = package(route_id)
        mismatched["source_media"] = wrong_source
        with pytest.raises(ValidationError, match="requires"):
            X2EnvEnvironmentPackage.model_validate(mismatched)

    with_optional_refs = compile_input("image2env")
    with_optional_refs["predecessor_package"] = artifact(
        "predecessor", "harness.x2env_environment_package.v1"
    )
    with_optional_refs["repair_evidence_refs"] = [
        artifact("repair", "harness.repair_evidence.v1", sha256=SHA_D)
    ]
    assert Image2EnvCompileInput.model_validate(with_optional_refs).predecessor_package

    wrong_image_compile = compile_output("video2env")
    wrong_image_compile["schema_version"] = "harness.image2env_compile_output.v1"
    with pytest.raises(ValidationError, match="image2env output"):
        Image2EnvCompileOutput.model_validate(wrong_image_compile)
    wrong_video_compile = compile_output("image2env")
    wrong_video_compile["schema_version"] = "harness.video2env_compile_output.v1"
    with pytest.raises(ValidationError, match="video2env output"):
        Video2EnvCompileOutput.model_validate(wrong_video_compile)

    replay_profile_mismatch = replay_input("image2env")
    replay_profile_mismatch["requested_profile"] = "genesis.robot_policy@1"
    with pytest.raises(ValidationError, match="requested_profile"):
        Image2EnvReplayInput.model_validate(replay_profile_mismatch)

    wrong_image_replay_input = replay_input("video2env")
    wrong_image_replay_input["schema_version"] = "harness.image2env_replay_input.v1"
    with pytest.raises(ValidationError, match="image2env replay"):
        Image2EnvReplayInput.model_validate(wrong_image_replay_input)
    wrong_video_replay_input = replay_input("image2env")
    wrong_video_replay_input["schema_version"] = "harness.video2env_replay_input.v1"
    with pytest.raises(ValidationError, match="video2env replay"):
        Video2EnvReplayInput.model_validate(wrong_video_replay_input)

    replay_result_profile_mismatch = replay_output("image2env")
    replay_result_profile_mismatch["requested_profile"] = "genesis.robot_policy@1"
    with pytest.raises(ValidationError, match="requested_profile"):
        Image2EnvReplayOutput.model_validate(replay_result_profile_mismatch)

    wrong_image_replay_output = replay_output("video2env")
    wrong_image_replay_output["schema_version"] = "harness.image2env_replay_output.v1"
    with pytest.raises(ValidationError, match="image2env replay output"):
        Image2EnvReplayOutput.model_validate(wrong_image_replay_output)
    wrong_video_replay_output = replay_output("image2env")
    wrong_video_replay_output["schema_version"] = "harness.video2env_replay_output.v1"
    with pytest.raises(ValidationError, match="video2env replay output"):
        Video2EnvReplayOutput.model_validate(wrong_video_replay_output)

    wrong_image_validate = validate_input("video2env")
    wrong_image_validate["schema_version"] = "harness.image2env_validate_input.v1"
    with pytest.raises(ValidationError, match="image2env validate"):
        Image2EnvValidateInput.model_validate(wrong_image_validate)
    wrong_video_validate = validate_input("image2env")
    wrong_video_validate["schema_version"] = "harness.video2env_validate_input.v1"
    with pytest.raises(ValidationError, match="video2env validate"):
        Video2EnvValidateInput.model_validate(wrong_video_validate)
