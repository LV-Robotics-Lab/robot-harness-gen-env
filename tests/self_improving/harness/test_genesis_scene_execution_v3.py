"""Revised compile lineage stays explicit without changing frozen scene runners."""

import pytest

from self_improving.harness.schemas.genesis_scene_execution_v3 import (
    GenesisSceneCompileBindingV2,
    GenesisSceneExecutionRequestV3,
)
from tests.self_improving.harness.test_genesis_scene_execution import reference
from tests.self_improving.harness.test_genesis_scene_replay import replay  # noqa: F401
from tests.self_improving.harness.test_gujie_scene_adapter import candidate  # noqa: F401


def test_revised_execution_requires_new_compile_receipt_and_request():
    payload = dict(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=reference("harness.genesis_scene_spec.v1"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        runtime_manifest=reference("harness.genesis_runtime_manifest.v1"),
        asset_qualification=reference("harness.asset_qualification.v1"),
    )
    request = GenesisSceneExecutionRequestV3.model_validate(payload)
    assert request.compile_receipt.schema_version.endswith(".v2")
    payload["compile_receipt"] = reference("harness.image2env_genesis_compile_receipt.v1")
    with pytest.raises(ValueError):
        GenesisSceneExecutionRequestV3.model_validate(payload)


def test_compile_binding_declares_registered_v3_lineage_without_claiming_authority():
    from uuid import uuid4

    binding = GenesisSceneCompileBindingV2(
        schema_version="harness.genesis_scene_compile_binding.v2",
        compile_skill_ref="image2env.compile@3.0.0",
        compile_run_id=uuid4(),
        compile_request=reference("harness.image2env_genesis_compile_request.v2"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        execution_request=reference("harness.genesis_scene_execution_request.v3"),
    )
    assert binding.compile_skill_ref == "image2env.compile@3.0.0"
    with pytest.raises(ValueError):
        GenesisSceneCompileBindingV2.model_validate(
            {**binding.model_dump(mode="json"), "compile_skill_ref": "image2env.compile@2.0.0"}
        )


@pytest.fixture
def saved_run(replay, tmp_path):  # noqa: F811
    """Synthetic process facts and analytic traces; never a simulator/live fixture."""
    import hashlib
    import json
    import shutil
    import subprocess

    from PIL import Image

    from self_improving.harness.genesis_runtime_manifest import capture_runtime_manifest
    from self_improving.harness.schemas.genesis_runtime_manifest import RuntimeProcessIdentityV1

    store, _, value, publish = replay
    roots = {
        role: tmp_path / role
        for role in ("backend", "interpreter", "stdlib", "distributions", "genesis", "native")
    }
    child = roots["backend"] / "self_improving/harness/_genesis_scene_child.py"
    child.parent.mkdir(parents=True)
    child.write_bytes(b"fixture only, never executed")
    identity = RuntimeProcessIdentityV1(
        implementation="cpython",
        version="fixture",
        abi="fixture",
        platform="fixture",
        environment=(
            ("LD_LIBRARY_PATH", "${NATIVE}/usr/lib/x86_64-linux-gnu"),
            ("TMPDIR", "${RUN}/tmp"),
        ),
    )
    manifest_ref = capture_runtime_manifest(store, roots=roots, process_identity=identity)
    value["runtime_manifest"] = manifest_ref.model_dump(mode="json")
    replay_ref = publish(value, "replay-v3-fixture.json", value["schema_version"])
    request = GenesisSceneExecutionRequestV3(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=value["scene"],
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        runtime_manifest=manifest_ref,
        asset_qualification=reference("harness.asset_qualification.v1"),
    )
    request_ref = publish(
        request.model_dump(mode="json"), "request-v3.json", request.schema_version
    )
    output = tmp_path / "saved-output"
    output.mkdir()
    native = roots["native"] / "member"
    for profile in ("baseline", "half_dt"):
        directory = output / profile
        (directory / "frames").mkdir(parents=True)
        evidence = value[profile]
        from self_improving.harness.schemas.common import ArtifactRef

        for field, name in (
            ("rows", "trace.ndjson"),
            ("loaded_scene", "loaded.json"),
            ("video", "simulation.mkv"),
        ):
            shutil.copyfile(
                store.resolve(ArtifactRef.model_validate(evidence[field])).path, directory / name
            )
        captures = []
        pixels = []
        for frame in evidence["frames"]:
            ref = ArtifactRef.model_validate(frame["image"])
            path = directory / "frames" / ref.name
            shutil.copyfile(store.resolve(ref).path, path)
            with Image.open(path) as image:
                raw_pixels = image.tobytes()
                pixels.append(raw_pixels)
                captures.append(
                    dict(
                        step=frame["step"],
                        time_s=frame["step"] * (0.004 if profile == "baseline" else 0.002),
                        captured_at="2026-09-11T00:00:00Z",
                        pixels_sha256=hashlib.sha256(raw_pixels).hexdigest(),
                        width=image.width,
                        height=image.height,
                        image_path="frames/" + ref.name,
                    )
                )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                "8x6",
                "-r",
                "10",
                "-i",
                "-",
                "-vf",
                "scale=640:480",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(directory / "preview.mp4"),
            ],
            input=b"".join(pixels),
            check=True,
        )
        environment = dict(
            LD_LIBRARY_PATH=str(roots["native"] / "usr/lib/x86_64-linux-gnu"),
            TMPDIR=str(output / "tmp"),
        )
        raw = dict(
            schema_version="harness.genesis_scene_raw_observation.v2",
            deployment_roots={role: str(root) for role, root in roots.items()},
            process_identity={
                key: val
                for key, val in identity.model_dump(mode="json").items()
                if key != "environment"
            },
            environment=environment,
            startup_environment=environment,
            modules=[dict(path=str(child), sha256=hashlib.sha256(child.read_bytes()).hexdigest())],
            native_files=[
                dict(path=str(native), sha256=hashlib.sha256(native.read_bytes()).hexdigest())
            ],
            landlock_abi=8,
            network_denied=True,
            runtime_write_denied=True,
            torch_threads=[1, 1],
            capture_events=captures,
        )
        (directory / "renderer-runtime.json").write_text(
            json.dumps(raw, sort_keys=True, separators=(",", ":"))
        )
        for stream in ("stdout", "stderr"):
            (output / (profile + "-" + stream + ".txt")).write_bytes(
                b"fixture, no process executed"
            )
    return store, request_ref, replay_ref, output, publish


def test_saved_v3_evidence_is_pure_and_relocates_without_compiler_or_runtime(saved_run, tmp_path):
    import shutil

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import (
        publish_scene_run_evidence_v3,
        read_scene_run_evidence_v3,
    )

    store, request, replay_ref, output, _ = saved_run
    launch = publish_scene_run_evidence_v3(
        store, request_ref=request, replay_ref=replay_ref, output_dir=output
    )
    closure = read_scene_run_evidence_v3(store, request, launch)
    assert closure.report.status == "passed"
    assert closure.execution_authority is closure.registered_compile_authority is False
    assert closure.launch_result.schema_version.endswith(".v3")
    shutil.copytree(store.root, tmp_path / "relocated")
    relocated = LocalArtifactStore(tmp_path / "relocated")
    assert read_scene_run_evidence_v3(relocated, request, launch) == closure


@pytest.mark.parametrize("mode", ["missing", "drift", "noncanonical", "wrong_request"])
def test_saved_v3_evidence_rejects_incomplete_or_mismatched_cas(saved_run, mode):
    import json

    from self_improving.harness.genesis_scene_execution_v3 import (
        publish_scene_run_evidence_v3,
        read_scene_run_evidence_v3,
    )

    store, request, replay_ref, output, publish = saved_run
    launch = publish_scene_run_evidence_v3(
        store, request_ref=request, replay_ref=replay_ref, output_dir=output
    )
    if mode == "wrong_request":
        value = json.loads(store.resolve(launch).path.read_bytes())
        value["request"]["name"] = "another-request.json"
        launch = publish(value, "wrong-launch.json", value["schema_version"])
    else:
        path = store.resolve(launch).path
        if mode == "missing":
            path.unlink()
        elif mode == "drift":
            path.write_bytes(b"changed")
        else:
            value = json.loads(path.read_bytes())
            raw = json.dumps(value, indent=2).encode()
            path = output / "noncanonical.json"
            path.write_bytes(raw)
            launch = store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=value["schema_version"],
            )
    with pytest.raises(ValueError):
        read_scene_run_evidence_v3(store, request, launch)


@pytest.mark.parametrize(
    "mode", ["request", "noncanonical", "outside", "duplicate", "unknown", "sha", "capture"]
)
def test_saved_publisher_rejects_inconsistent_raw_output(saved_run, mode):
    import json

    from self_improving.harness.genesis_scene_execution_v3 import publish_scene_run_evidence_v3

    store, request, replay_ref, output, publish = saved_run
    if mode == "request":
        value = json.loads(store.resolve(request).path.read_bytes())
        value["scene"]["name"] = "wrong-scene.json"
        request = publish(value, "wrong-request.json", value["schema_version"])
    else:
        path = output / "baseline/renderer-runtime.json"
        raw = json.loads(path.read_bytes())
        if mode == "outside":
            raw["modules"][0]["path"] = "/outside/member"
        elif mode == "duplicate":
            raw["modules"] *= 2
        elif mode == "unknown":
            raw["modules"][0]["path"] += "-missing"
        elif mode == "sha":
            raw["modules"][0]["sha256"] = "b" * 64
        elif mode == "capture":
            raw["capture_events"][0]["image_path"] = "../outside.png"
        path.write_text(
            json.dumps(
                raw,
                sort_keys=True,
                separators=(",", ":"),
                indent=2 if mode == "noncanonical" else None,
            )
        )
    with pytest.raises(ValueError):
        publish_scene_run_evidence_v3(
            store, request_ref=request, replay_ref=replay_ref, output_dir=output
        )


@pytest.mark.parametrize(
    "mode", ["profile", "runtime", "member", "order", "child", "stream", "raw"]
)
def test_v3_reader_rejects_typed_observation_disagreements(saved_run, mode):
    import json

    from self_improving.harness.genesis_scene_execution_v3 import (
        publish_scene_run_evidence_v3,
        read_scene_run_evidence_v3,
    )

    store, request, replay_ref, output, publish = saved_run
    launch = publish_scene_run_evidence_v3(
        store, request_ref=request, replay_ref=replay_ref, output_dir=output
    )
    value = json.loads(store.resolve(launch).path.read_bytes())
    from self_improving.harness.schemas.common import ArtifactRef

    observation = json.loads(
        store.resolve(ArtifactRef.model_validate(value["baseline_observation"])).path.read_bytes()
    )
    if mode == "profile":
        observation["profile"] = "half_dt"
    elif mode == "runtime":
        observation["process_identity"]["version"] = "changed"
    elif mode == "member":
        observation["modules"][0]["artifact"]["name"] = "other"
    elif mode == "order":
        observation["modules"] *= 2
    elif mode == "child":
        observation["modules"] = observation["native_files"]
    elif mode == "stream":
        observation["stdout"]["media_type"] = "application/json"
    else:
        ref = ArtifactRef.model_validate(observation["raw_observation"])
        raw = json.loads(store.resolve(ref).path.read_bytes())
        path = output / "bad-raw.json"
        path.write_text(json.dumps(raw, indent=2))
        observation["raw_observation"] = store.put_file(
            path, name=path.name, media_type="application/json", schema_version=None
        ).model_dump(mode="json")
    value["baseline_observation"] = publish(
        observation, "changed-observation.json", observation["schema_version"]
    ).model_dump(mode="json")
    launch = publish(value, "changed-launch.json", value["schema_version"])
    with pytest.raises(ValueError):
        read_scene_run_evidence_v3(store, request, launch)


def test_schema_v3_receipt_and_denials_reject_legacy_refs():
    from self_improving.harness.schemas.genesis_scene_execution_v3 import (
        GenesisSceneExecutionReceiptV3,
        GenesisSceneProcessObservationV3,
    )
    from tests.self_improving.harness.test_genesis_scene_execution import observation

    raw = observation()
    raw.update(
        schema_version="harness.genesis_scene_process_observation.v3",
        request=reference("harness.genesis_scene_execution_request.v3"),
    )
    raw["captures"] = [
        dict(
            step=i * 25,
            time_s=i / 10,
            captured_at="2026-09-11T00:00:00Z",
            pixels_sha256="a" * 64,
            width=640,
            height=480,
            image={**reference(None), "media_type": "image/png"},
        )
        for i in range(41)
    ]
    assert GenesisSceneProcessObservationV3.model_validate(raw).network_denied
    raw["network_denied"] = False
    with pytest.raises(ValueError, match="both actual"):
        GenesisSceneProcessObservationV3.model_validate(raw)
    receipt = dict(
        schema_version="harness.genesis_scene_execution_receipt.v3",
        authority="observed_execution_only",
        request=reference("harness.genesis_scene_execution_request.v3"),
        launch_result=reference("harness.genesis_scene_launch_result.v3"),
        replay=reference("harness.genesis_scene_replay.v1"),
        report=reference("harness.genesis_scene_report.v1"),
        baseline_preview={**reference(None), "media_type": "video/mp4"},
        half_dt_preview={**reference(None), "media_type": "video/mp4"},
        retained_index=reference(None),
    )
    assert GenesisSceneExecutionReceiptV3.model_validate(receipt).schema_version.endswith(".v3")
    receipt["request"] = reference("harness.genesis_scene_execution_request.v2")
    with pytest.raises(ValueError):
        GenesisSceneExecutionReceiptV3.model_validate(receipt)


def test_noncanonical_request_failure_retains_diagnostic_and_cannot_issue_live(tmp_path):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import (
        GenesisSceneExecutionErrorV3,
        GenesisSceneExecutionProducerV3,
        LiveGenesisSceneExecutionV3,
    )

    with pytest.raises(TypeError, match="fixed revised producer"):
        LiveGenesisSceneExecutionV3(None, None)
    store = LocalArtifactStore(tmp_path / "cas")
    payload = dict(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=reference("harness.genesis_scene_spec.v1"),
        compile_receipt=reference("harness.image2env_genesis_compile_receipt.v2"),
        runtime_manifest=reference("harness.genesis_runtime_manifest.v1"),
        asset_qualification=reference("harness.asset_qualification.v1"),
    )
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload, indent=2))
    ref = store.put_file(
        path,
        name=path.name,
        media_type="application/json",
        schema_version=payload["schema_version"],
    )
    with pytest.raises(GenesisSceneExecutionErrorV3, match="noncanonical") as error:
        GenesisSceneExecutionProducerV3(store, None, None).produce(ref, roots={})
    diagnostic = json.loads(store.resolve(error.value.diagnostic_ref).path.read_bytes())
    assert diagnostic["request"] == ref.model_dump(mode="json")
    assert diagnostic["launch_result"] is diagnostic["retained_index"] is None


def test_exact_compiler_required_before_any_saved_run_is_admitted(saved_run):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionVerifierV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler

    store, request, _, _, _ = saved_run
    with pytest.raises(TypeError, match="exact revised compiler"):
        GenesisSceneExecutionVerifierV3(store, object()).validate_request(request)
    other = LocalArtifactStore(store.root)
    compiler = RevisedImage2EnvGenesisCompiler(other, Image2EnvGenesisCompiler(other, object()))
    with pytest.raises(ValueError, match="same CAS store instance"):
        GenesisSceneExecutionVerifierV3(store, compiler).validate_request(request)


def test_formal_revised_compile_request_is_deeply_read_without_genesis(tmp_path):
    import json
    import os
    import time
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_asset_qualification_application import (
        GenesisAssetQualificationApplication,
    )
    from self_improving.harness.genesis_scene_execution_v3 import GenesisSceneExecutionVerifierV3
    from self_improving.harness.image2env_genesis_compile import Image2EnvGenesisCompiler
    from self_improving.harness.revised_image_compiler import RevisedImage2EnvGenesisCompiler
    from self_improving.harness.schemas.common import ArtifactRef

    configured = os.environ.get("REVISED_SCENE_COMPILE_SUMMARY")
    if not configured:
        pytest.skip("requires actual J2 revised compile and durable asset qualification")
    summary = json.loads(Path(configured).read_bytes())
    config = json.loads(Path(summary["config"]).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    qualification = GenesisAssetQualificationApplication(
        Path(config["asset_state_path"]),
        store,
        ArtifactRef.model_validate(config["asset_manifest"]),
    )
    compiler = RevisedImage2EnvGenesisCompiler(
        store, Image2EnvGenesisCompiler(store, qualification)
    )
    ref = ArtifactRef.model_validate(summary["result"]["receipt"])
    compiled = json.loads(store.resolve(ref).path.read_bytes())
    request = GenesisSceneExecutionRequestV3(
        schema_version="harness.genesis_scene_execution_request.v3",
        scene=compiled["scene"],
        compile_receipt=ref,
        runtime_manifest=config["scene_manifest"],
        asset_qualification=compiled["qualification"],
    )
    path = tmp_path / "revised-execution-request.json"
    path.write_text(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    request_ref = store.put_file(
        path, name=path.name, media_type="application/json", schema_version=request.schema_version
    )
    started = time.monotonic()
    actual, manifest = GenesisSceneExecutionVerifierV3(store, compiler).validate_request(
        request_ref
    )
    assert actual == request
    assert len(manifest.members) > 100000
    with pytest.raises(ValueError, match="unexpected scene evidence schema"):
        GenesisSceneExecutionVerifierV3(store, compiler).read_receipt(ref)
    destination = Path(os.environ["REVISED_SCENE_REQUEST_SUMMARY"])
    destination.write_text(
        json.dumps(
            dict(
                request=request_ref.model_dump(mode="json"),
                compiler_receipt=ref.model_dump(mode="json"),
                scene=request.scene.model_dump(mode="json"),
                manifest=request.runtime_manifest.model_dump(mode="json"),
                seconds=time.monotonic() - started,
                simulator_executed=False,
                registered_replay=False,
                scope="revised_static_compile_and_request_deep_read_only",
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
