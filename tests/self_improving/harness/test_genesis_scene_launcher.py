import pytest


def test_scene_v2_rejects_a_v1_request_reference_before_execution(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_launcher import execute_genesis_scene_request_v2

    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "request.json"
    source.write_text("{}")
    ref = store.put_file(
        source,
        name=source.name,
        media_type="application/json",
        schema_version="harness.genesis_scene_execution_request.v1",
    )
    with pytest.raises(ValueError, match="v2"):
        execute_genesis_scene_request_v2(store=store, request_ref=ref, roots={})


def test_real_constrained_scene_request_keeps_two_processes(tmp_path):
    import json
    import os
    from pathlib import Path

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_launcher import execute_genesis_scene_request
    from self_improving.harness.schemas.common import ArtifactRef
    from self_improving.harness.schemas.genesis_scene_execution import GenesisSceneLaunchResultV1

    config_path = os.environ.get("GOLDEN_SCENE_EXECUTION_CONFIG")
    if not config_path:
        pytest.skip("requires a freshly declared renderer runtime and real compile request")
    config = json.loads(Path(config_path).read_bytes())
    store = LocalArtifactStore(Path(config["cas_root"]))
    request = ArtifactRef.model_validate(config["request_ref"])
    result = execute_genesis_scene_request(store=store, request_ref=request, roots=config["roots"])
    loaded = GenesisSceneLaunchResultV1.model_validate_json(store.resolve(result).path.read_bytes())
    assert loaded.request == request
    assert loaded.baseline_observation != loaded.half_dt_observation
    Path(config_path).with_name("scene-launch-result-ref.json").write_text(
        result.model_dump_json(indent=2)
    )


def test_scene_request_rejects_untyped_input_before_execution(tmp_path):
    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_launcher import execute_genesis_scene_request

    store = LocalArtifactStore(tmp_path / "cas")
    path = tmp_path / "untyped.json"
    path.write_text("{}")
    ref = store.put_file(path, name=path.name, media_type="application/json", schema_version=None)
    with pytest.raises(ValueError):
        execute_genesis_scene_request(store=store, request_ref=ref, roots={})
    assert sorted(tmp_path.iterdir()) == [tmp_path / "cas", path]


def test_scene_restrictions_are_inherited_by_encoding_child(tmp_path):
    import subprocess
    import sys

    child = tmp_path / "encoder.py"
    child.write_text(
        "import os,socket\n"
        "try: socket.socket()\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('network allowed')\n"
        "try: os.open(__file__,os.O_WRONLY)\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('runtime write allowed')\n"
        "try: os.open('/etc/hostname',os.O_RDONLY)\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('undeclared host read allowed')\n"
        "with open(os.path.join(os.path.dirname(__file__),'run/output'),'w') as f: f.write('ok')\n"
    )
    run = tmp_path / "run"
    run.mkdir()
    script = (
        "import os,socket,subprocess,sys; from pathlib import Path; "
        "from self_improving.harness._genesis_scene_child import restrict_scene_process; "
        "restrict_scene_process({'python':Path(sys.base_prefix),'native':Path('/usr/lib'),"
        f"'backend':Path({str(tmp_path)!r})}},Path({str(run)!r})); "
        f"subprocess.run([str(Path(sys.executable).resolve()),{str(child)!r}],check=True)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (run / "output").read_text() == "ok"


def test_scene_launcher_rejects_missing_manifest_before_creating_output(tmp_path):
    from self_improving.harness.genesis_scene_launcher import launch_genesis_scene_replay

    with pytest.raises(ValueError, match="manifest"):
        launch_genesis_scene_replay(None, None, None, roots={}, output_dir=tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_retained_output_index_uses_logical_posix_string_order(tmp_path):
    import json

    from self_improving.harness.artifacts import LocalArtifactStore
    from self_improving.harness.genesis_scene_launcher import retain_scene_outputs_v2

    output = tmp_path / "outputs"
    names = [
        "baseline-job.json",
        "baseline-observation.json",
        "baseline-stderr.txt",
        "baseline-stdout.txt",
    ]
    names += [f"baseline/frames/step-{step:04}.png" for step in range(0, 1001, 25)]
    names += [
        "half_dt-job.json",
        "half_dt-observation.json",
        "half_dt-stderr.txt",
        "half_dt-stdout.txt",
    ]
    names += [f"half_dt/frames/step-{step:04}.png" for step in range(0, 2001, 50)]
    for profile in ("baseline", "half_dt"):
        names += [
            f"{profile}/{name}"
            for name in (
                "loaded.json",
                "media.json",
                "preview.mp4",
                "renderer-runtime.json",
                "result.json",
                "simulation.mkv",
                "trace.ndjson",
            )
        ]
    from pathlib import PurePosixPath

    assert sum(a != b for a, b in zip(sorted(names), sorted(names, key=PurePosixPath))) == 104
    for name in names:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    store = LocalArtifactStore(tmp_path / "cas")
    ref = retain_scene_outputs_v2(store, output)
    result = json.loads(store.resolve(ref).path.read_bytes())
    assert [item["name"] for item in result["files"]] == sorted(names)
    assert len(result["files"]) == len(set(names))
    assert result["execution_authority"] is False
    assert result["qualified"] is False
