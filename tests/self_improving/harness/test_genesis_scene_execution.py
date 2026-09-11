"""Public scene execution contracts preserve the separate asset/runtime scopes."""

import pytest

from self_improving.harness.schemas.genesis_scene_execution import GenesisSceneExecutionRequestV1


def reference(schema):
    return dict(
        name="record.json",
        uri="artifact://sha256/" + "a" * 64,
        sha256="a" * 64,
        bytes=1,
        media_type="application/json",
        schema_version=schema,
    )


def request():
    return dict(
        schema_version="harness.genesis_scene_execution_request.v1",
        scene=reference("harness.genesis_scene_spec.v1"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v1"),
        runtime_manifest=reference("harness.genesis_runtime_manifest.v1"),
        asset_qualification=reference("harness.asset_qualification.v1"),
    )


def test_scene_request_keeps_asset_qualification_separate_from_scene_runtime():
    value = GenesisSceneExecutionRequestV1.model_validate(request())
    assert value.runtime_manifest != value.asset_qualification
    invalid = request()
    invalid["runtime_manifest"] = invalid["asset_qualification"]
    with pytest.raises(ValueError, match="exact JSON schema"):
        GenesisSceneExecutionRequestV1.model_validate(invalid)


def test_live_scene_capability_cannot_be_constructed_from_caller_json():
    from self_improving.harness.genesis_scene_execution import LiveGenesisSceneExecution

    with pytest.raises(TypeError, match="fixed producer"):
        LiveGenesisSceneExecution(reference(None), object())


def test_request_reader_rejects_noncanonical_bytes_before_compiler_use(tmp_path):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution import GenesisSceneExecutionVerifier

    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request(), indent=2))
    ref = store.put_file(
        path,
        name="request.json",
        media_type="application/json",
        schema_version="harness.genesis_scene_execution_request.v1",
    )
    verifier = GenesisSceneExecutionVerifier(store, None)
    with pytest.raises(ValueError, match="noncanonical"):
        verifier.validate_request(ref)


@pytest.mark.parametrize("field,value", [("uri", "file:///tmp/x"), ("media_type", "text/plain")])
def test_request_rejects_untyped_or_noncas_input(field, value):
    payload = request()
    payload["scene"][field] = value
    with pytest.raises(ValueError, match="exact JSON schema"):
        GenesisSceneExecutionRequestV1.model_validate(payload)


def test_producer_failure_retains_request_and_error_without_live_authority(tmp_path):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution import (
        GenesisSceneExecutionError,
        GenesisSceneExecutionProducer,
    )

    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request(), sort_keys=True, separators=(",", ":")))
    ref = store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version="harness.genesis_scene_execution_request.v1",
    )
    producer = GenesisSceneExecutionProducer(store, None, None)
    with pytest.raises(GenesisSceneExecutionError, match="trusted native compiler") as failure:
        producer.produce(ref, roots={})
    diagnostic = json.loads(store.resolve(failure.value.diagnostic_ref).path.read_bytes())
    assert diagnostic["request"] == ref.model_dump(mode="json")
    assert diagnostic["error_type"] == "TypeError"
    assert diagnostic["failure_artifacts"] == []
    assert diagnostic["cause_diagnostic"] is None


def observation():
    member_ref = reference(None)
    member_ref.update(name="backend/member", media_type="application/octet-stream")
    member = dict(root="backend", path="member", artifact=member_ref)
    return dict(
        schema_version="harness.genesis_scene_process_observation.v1",
        request=reference("harness.genesis_scene_execution_request.v1"),
        profile="baseline",
        process_identity=dict(
            implementation="cpython",
            version="3.12.3",
            abi="cpython-312",
            platform="linux-x86_64",
            environment=[],
        ),
        environment_delta={},
        modules=[member],
        native_files=[member],
        external_native_files=[],
        torch_threads=[1, 1],
        landlock_abi=8,
        network_denied=True,
        runtime_write_denied=True,
        kernel_devices=["/dev/null", "/dev/urandom"],
        network_policy="socket_syscalls_denied",
        filesystem_policy="runtime_roots_readonly_kernel_devices_run_output_write_inherited_children",
        stdout=reference(None),
        stderr=reference(None),
        raw_observation=reference(None),
    )


@pytest.mark.parametrize("changed", [None, "network_denied", "runtime_write_denied"])
def test_typed_observation_requires_both_actual_denial_results(changed):
    from self_improving.harness.schemas.genesis_scene_execution import (
        GenesisSceneProcessObservationV1,
    )

    payload = observation()
    if changed is None:
        assert GenesisSceneProcessObservationV1.model_validate(payload).landlock_abi == 8
    else:
        payload[changed] = False
        with pytest.raises(ValueError, match="both actual"):
            GenesisSceneProcessObservationV1.model_validate(payload)


def test_launch_and_receipt_require_exact_versioned_closure():
    from self_improving.harness.schemas.genesis_scene_execution import (
        GenesisSceneExecutionReceiptV1,
        GenesisSceneLaunchResultV1,
    )

    launch = GenesisSceneLaunchResultV1.model_validate(
        dict(
            schema_version="harness.genesis_scene_launch_result.v1",
            request=reference("harness.genesis_scene_execution_request.v1"),
            replay=reference("harness.genesis_scene_replay.v1"),
            baseline_observation=reference("harness.genesis_scene_process_observation.v1"),
            half_dt_observation=reference("harness.genesis_scene_process_observation.v1"),
        )
    )
    receipt = GenesisSceneExecutionReceiptV1.model_validate(
        dict(
            schema_version="harness.genesis_scene_execution_receipt.v1",
            authority="observed_execution_only",
            request=launch.request,
            launch_result=reference("harness.genesis_scene_launch_result.v1"),
            replay=launch.replay,
            report=reference("harness.genesis_scene_report.v1"),
        )
    )
    assert receipt.authority == "observed_execution_only"
    payload = receipt.model_dump(mode="json")
    payload["report"]["schema_version"] = "harness.genesis_scene_replay.v1"
    with pytest.raises(ValueError, match="exact JSON schema"):
        GenesisSceneExecutionReceiptV1.model_validate(payload)


@pytest.mark.parametrize(
    "variation",
    [
        "launch_request",
        "replay_scene",
        "observation_request",
        "observation_profile",
        "process",
        "member_order",
        "member_bytes",
        "missing_child",
        "stream_type",
        "raw_noncanonical",
        "raw_roots",
        "raw_relative_root",
        "raw_member_outside",
        "raw_duplicate",
        "raw_member_sha",
        "raw_identity",
        "raw_environment",
        "raw_thread",
    ],
)
def test_formal_execution_closure_rejects_recorded_evidence_drift(variation, tmp_path):
    """Mutate copies of actual evidence; never manufacture live producer authority."""
    import copy
    import json
    import os
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_execution import GenesisSceneExecutionVerifier
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef

    configured = os.environ.get("GENESIS_SCENE_EXECUTION_CONFIG")
    if not configured:
        pytest.skip("requires an actual fixed-runtime successful candidate closure")
    config_path = Path(configured)
    config = json.loads(config_path.read_bytes())
    native = json.loads(Path(os.environ["GENESIS_NATIVE_COMPILE_CONFIG"]).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    app = GenesisAssetQualificationApplication(
        Path(native["state_path"]),
        store,
        ArtifactRef.model_validate(native["approved_manifest_ref"]),
    )
    verifier = GenesisSceneExecutionVerifier(store, Image2EnvGenesisCompiler(store, app))
    successful = [
        json.loads(path.read_bytes())
        for path in sorted(config_path.parent.glob("scene-candidate-*.json"))
        if json.loads(path.read_bytes())["state"]["status"] == "succeeded"
    ]
    assert successful, "real scene candidate success is required"
    original_ref = ArtifactRef.model_validate(
        next(
            ref
            for ref in successful[-1]["state"]["artifacts"]
            if ref["schema_version"] == "harness.genesis_scene_execution_receipt.v1"
        )
    )

    def read(ref):
        return json.loads(store.resolve(ArtifactRef.model_validate(ref)).path.read_bytes())

    def publish(value, schema, suffix):
        path = tmp_path / (suffix + ".json")
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return store.put_file(
            path, name=path.name, media_type="application/json", schema_version=schema
        ).model_dump(mode="json")

    receipt = read(original_ref)
    launch = read(receipt["launch_result"])
    observation = read(launch["baseline_observation"])
    raw = read(observation["raw_observation"])
    if variation == "launch_request":
        launch["request"]["name"] = "wrong-request.json"
    elif variation == "replay_scene":
        replay = read(launch["replay"])
        replay["scene"]["name"] = "wrong-scene.json"
        launch["replay"] = publish(replay, replay["schema_version"], "replay")
    elif variation == "observation_request":
        observation["request"]["name"] = "wrong-request.json"
    elif variation == "observation_profile":
        observation["profile"] = "half_dt"
    elif variation == "process":
        observation["process_identity"]["abi"] = "different-abi"
    elif variation == "member_order":
        observation["modules"].reverse()
    elif variation == "member_bytes":
        observation["native_files"][0]["artifact"]["bytes"] += 1
    elif variation == "missing_child":
        observation["modules"] = [
            item
            for item in observation["modules"]
            if item["path"] != "self_improving/harness/_genesis_scene_child.py"
        ]
    elif variation == "stream_type":
        observation["stdout"]["media_type"] = "application/octet-stream"
    elif variation == "raw_roots":
        del raw["deployment_roots"]["native"]
    elif variation == "raw_relative_root":
        raw["deployment_roots"]["native"] = "relative"
    elif variation == "raw_member_outside":
        raw["modules"][0]["path"] = "/outside/member.py"
    elif variation == "raw_duplicate":
        raw["modules"].append(copy.deepcopy(raw["modules"][0]))
    elif variation == "raw_member_sha":
        raw["modules"][0]["sha256"] = "a" * 64
    elif variation == "raw_identity":
        raw["process_identity"]["abi"] = "different-abi"
    elif variation == "raw_environment":
        raw["startup_environment"]["OMP_NUM_THREADS"] = "2"
    elif variation == "raw_thread":
        raw["torch_threads"] = [2, 1]
    if variation.startswith("raw_"):
        observation["raw_observation"] = publish(raw, None, "raw")
        if variation == "raw_noncanonical":
            path = tmp_path / "noncanonical.json"
            path.write_text(json.dumps(raw, indent=2))
            observation["raw_observation"] = store.put_file(
                path, name=path.name, media_type="application/json", schema_version=None
            ).model_dump(mode="json")
    launch["baseline_observation"] = publish(
        observation, observation["schema_version"], "observation"
    )
    receipt["launch_result"] = publish(launch, launch["schema_version"], "launch")
    mutated = ArtifactRef.model_validate(publish(receipt, receipt["schema_version"], "receipt"))
    with pytest.raises(ValueError):
        verifier.read_receipt(mutated)
