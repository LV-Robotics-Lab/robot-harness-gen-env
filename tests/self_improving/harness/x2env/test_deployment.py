"""Deployment seam: real configuration parsing; external execution is explicitly absent."""

import json

import pytest

from self_improving.harness.x2env.contracts import X2EnvRequest
from self_improving.harness.x2env.deployment import build_harness, load_deployment


def test_deployment_builds_one_real_harness_without_inventing_missing_model(tmp_path):
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"state_dir": str(tmp_path / "state")}))
    config = load_deployment(path)
    harness = build_harness(config)
    handle = harness.submit(
        X2EnvRequest(
            text="a table", seed=19, idempotency_key="one", output_dir=str(tmp_path / "result")
        )
    )
    result = harness.resume(handle.workflow_id)
    assert result.status == "blocked" and result.required_resources == ("managed_codex_backend",)
    assert result.scene_ir is None
    path.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "model_proposal": {}}))
    with pytest.raises(ValueError):
        load_deployment(path)


def test_declared_missing_codex_is_structured_block_and_bad_pin_is_rejected(tmp_path):
    path = tmp_path / "deployment.json"
    data = {
        "state_dir": str(tmp_path / "state"),
        "codex": {
            "executable": str(tmp_path / "missing-codex"),
            "sha256": "a" * 64,
            "model": "gpt-6-astra",
        },
    }
    path.write_text(json.dumps(data))
    harness = build_harness(load_deployment(path))
    handle = harness.submit(
        X2EnvRequest(
            text="a table", seed=19, idempotency_key="one", output_dir=str(tmp_path / "out")
        )
    )
    result = harness.resume(handle.workflow_id)
    assert result.status == "blocked" and result.stop_reason == "blocked_external_resource"
    assert result.proposal is not None
    data["codex"]["sha256"] = "not-a-pin"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_deployment(path)


def test_contextual_assembly_preserves_seed_and_compiles_model_double_scene(tmp_path):
    import hashlib

    from self_improving.harness.x2env.compile import CompiledScene
    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.store import Store
    from tests.self_improving.harness.x2env.test_codex import executable

    request = X2EnvRequest(
        text="one table", seed=29, idempotency_key="table", output_dir=str(tmp_path / "out")
    )
    bundle = ingest(request, Store(tmp_path / "fixture-input"))
    records = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
    response = {
        "unknowns": [],
        "scene": {
            "revision": 0,
            "input_sha256": bundle.request_sha256,
            "entities": [
                {
                    "id": "table",
                    "category": "table",
                    "role": "structural_support",
                    "color": None,
                    "dimensions": [0.9, 0.7, None],
                    "material": None,
                    "articulation_state": None,
                    "pose": {"frame": "world", "position": [0, 0, None], "yaw_degrees": 0},
                    "provenance": {
                        key: records
                        for key in (
                            "category",
                            "color",
                            "dimensions",
                            "material",
                            "pose",
                            "articulation_state",
                        )
                    },
                }
            ],
            "relations": [],
        },
    }
    model = executable(tmp_path, response)
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps(
            {
                "state_dir": str(tmp_path / "state"),
                "codex": {
                    "executable": str(model),
                    "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                },
                "local_enabled": False,
                "compile_policy": {"thickness_m": 0.04, "surface_height_m": 0.75, "friction": 0.5},
            }
        )
    )
    harness = build_harness(load_deployment(config))
    handle = harness.submit(request)
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked" and snapshot.required_resources == (
        "genesis_replay_executor",
    )
    compiled = CompiledScene.model_validate_json(
        Store(tmp_path / "state").read_artifact(snapshot.compiled_scene)
    )
    assert compiled.runtime_scene.seed == 29 and compiled.runtime_scene.entities[0].size_m == (
        0.9,
        0.7,
        0.04,
    )


def test_runtime_and_provider_configuration_is_pinned_and_scoped(tmp_path):
    path = tmp_path / "deployment.json"
    data = {
        "state_dir": str(tmp_path / "state"),
        "genesis": {
            "runtime_roots": {
                name: str(tmp_path / name)
                for name in ("interpreter", "stdlib", "distributions", "native", "genesis")
            }
        },
        "web": {"provider_config": {"path": str(tmp_path / "provider.json"), "sha256": "a" * 64}},
    }
    path.write_text(json.dumps(data))
    config = load_deployment(path)
    assert config.web.provider_config.sha256 == "a" * 64
    assert config.genesis.runtime_roots.native == str(tmp_path / "native")
    data["genesis"]["runtime_roots"]["native"] = "/"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="scoped"):
        load_deployment(path)


def foreground_deployment(tmp_path, source, extra):
    import hashlib

    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.store import Store
    from tests.self_improving.harness.x2env.test_codex import executable

    request = X2EnvRequest(
        text="one box",
        seed=37,
        allowed_sources=(source,),
        idempotency_key="one",
        output_dir=str(tmp_path / "out"),
    )
    bundle = ingest(request, Store(tmp_path / "fixture"))
    evidence = [{"source": "text", "input_sha256": bundle.text.sha256, "kind": "explicit"}]
    response = {
        "unknowns": [],
        "scene": {
            "revision": 0,
            "input_sha256": bundle.request_sha256,
            "relations": [],
            "entities": [
                {
                    "id": "box",
                    "category": "box",
                    "role": "foreground",
                    "dimensions": [0.1, 0.1, 0.1],
                    "material": None,
                    "color": None,
                    "articulation_state": None,
                    "pose": {"frame": "world", "position": [0, 0, 0], "yaw_degrees": 0},
                    "provenance": {
                        key: evidence
                        for key in (
                            "category",
                            "color",
                            "dimensions",
                            "material",
                            "pose",
                            "articulation_state",
                        )
                    },
                }
            ],
        },
    }
    exe = executable(tmp_path, response)
    config = tmp_path / "deployment.json"
    config.write_text(
        json.dumps(
            {
                "state_dir": str(tmp_path / "state"),
                "codex": {
                    "executable": str(exe),
                    "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
                },
                **extra,
            }
        )
    )
    return load_deployment(config), request


def test_missing_declared_web_config_preserves_workflow_source_failure(tmp_path):
    config, request = foreground_deployment(
        tmp_path,
        "web",
        {
            "web": {
                "provider_config": {
                    "path": str(tmp_path / "missing-provider.json"),
                    "sha256": "a" * 64,
                }
            }
        },
    )
    harness = build_harness(config)
    handle = harness.submit(request)
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked" and snapshot.stop_reason == "blocked_external_resource"
    assert snapshot.asset_resolution is not None and snapshot.compiled_scene is None


def test_reconstruction_text_only_records_missing_image_before_external_work(tmp_path):
    config, request = foreground_deployment(
        tmp_path,
        "reconstruction",
        {
            "reconstruction": {
                "source_root": str(tmp_path / "source"),
                "source_commit": "a" * 40,
                "python": str(tmp_path / "python"),
                "python_sha256": "b" * 64,
                "segmentation_runtime": {"path": str(tmp_path / "sam.json"), "sha256": "c" * 64},
                "reconstruction_runtime": {
                    "path": str(tmp_path / "trellis.json"),
                    "sha256": "d" * 64,
                },
                "model_refs": {},
            }
        },
    )
    harness = build_harness(config)
    handle = harness.submit(request)
    snapshot = harness.resume(handle.workflow_id)
    assert snapshot.status == "blocked"
    from self_improving.harness.x2env.artifacts import artifact_closure
    from self_improving.harness.x2env.store import Store

    store = Store(tmp_path / "state")
    records = artifact_closure(store, (snapshot.asset_resolution,))
    assert any(b"missing_reconstruction_image" in store.read_artifact(ref) for ref in records)


def test_reconstruction_image_selection_reads_cas_not_original_path(tmp_path):
    from PIL import Image

    from self_improving.harness.x2env.contracts import InputMedia
    from self_improving.harness.x2env.deployment import select_reconstruction_image
    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.store import Store

    path = tmp_path / "image.png"
    Image.new("RGB", (8, 6), "red").save(path)
    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            images=(InputMedia(path=str(path)),),
            seed=19,
            idempotency_key="image",
            output_dir=str(tmp_path / "out"),
        ),
        store,
    )
    Image.new("RGB", (8, 6), "blue").save(path)
    scene = store.write_artifact(b"{}", "application/json")
    result = select_reconstruction_image(
        store, bundle, scene, "mouse", output_root=tmp_path / "selection"
    )
    assert result.image == bundle.images[0].canonical
    evidence = json.loads(store.read_artifact(result.provenance))
    assert evidence["scene_ir"] == scene.model_dump() and evidence["entity_id"] == "mouse"
    assert evidence["selection_basis"] == "first_canonical_input_image"


def test_video_selection_verifies_frame_zero_against_complete_ingest(tmp_path):
    import subprocess

    from self_improving.harness.x2env.contracts import InputMedia
    from self_improving.harness.x2env.deployment import select_reconstruction_image
    from self_improving.harness.x2env.input import ingest
    from self_improving.harness.x2env.store import Store

    video = tmp_path / "input.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x24:rate=4:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    store = Store(tmp_path / "state")
    bundle = ingest(
        X2EnvRequest(
            video=InputMedia(path=str(video)),
            seed=19,
            idempotency_key="video",
            output_dir=str(tmp_path / "out"),
        ),
        store,
    )
    scene = store.write_artifact(b"{}", "application/json")
    result = select_reconstruction_image(
        store, bundle, scene, "box", output_root=tmp_path / "selection"
    )
    record = json.loads(store.read_artifact(result.provenance))
    assert record["frame_index"] == 0 and record["full_frame_count"] == 4
    assert result.image.media_type == "image/png"
    sequence = json.loads(store.read_artifact(bundle.video.sequence))
    sequence["frames"][0]["sha256"] = "a" * 64
    bad = bundle.model_copy(
        update={
            "video": bundle.video.model_copy(
                update={
                    "sequence": store.write_artifact(
                        json.dumps(sequence).encode(), "application/json"
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match="decoded_video_frame_mismatch"):
        select_reconstruction_image(
            store, bad, scene, "box", output_root=tmp_path / "bad-selection"
        )


def test_pinned_provider_bytes_and_symlink_ancestors_rejected(tmp_path):
    from self_improving.harness.x2env.deployment import PinnedFile

    path = tmp_path / "config.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="deployment_pin_mismatch"):
        PinnedFile(path=str(path), sha256="a" * 64).read()
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="nonsymbolic"):
        load_deployment(linked / "config.json")
