"""Dual-profile orchestration with an explicit subprocess boundary double."""

import json

from self_improving.harness.x2env.genesis_runtime import RuntimeEntity, RuntimeScene
from self_improving.harness.x2env.store import Store


def test_replay_binds_both_profiles_and_keeps_partial_failure(tmp_path, monkeypatch):
    from self_improving.harness.x2env.replay import GenesisReplayExecutor

    store = Store(tmp_path / "state")
    scene = RuntimeScene(
        seed=23,
        scene_ir_sha256="a" * 64,
        entities=(
            RuntimeEntity(
                id="table",
                category="table",
                kind="structural_box",
                position_m=(0.0, 0.0, 0.7),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                size_m=(1.0, 1.0, 0.04),
                friction=0.5,
            ),
        ),
    )
    package = tmp_path / "package"
    package.mkdir()
    calls = []

    def runtime_double(value, **kwargs):
        assert value == scene
        calls.append(kwargs["profile"])
        out = kwargs["output_dir"]
        out.mkdir(parents=True)
        (out / "stdout.log").write_text("explicit test runtime double")
        result = {
            "status": "passed" if len(calls) == 1 else "failed",
            "simulator_executed": True,
            "physical_profile": "not_run",
        }
        (out / "result.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr("self_improving.harness.x2env.replay.run_scene", runtime_double)
    result = GenesisReplayExecutor(store, runtime_roots={}).replay(
        scene, package_root=package, output_root=tmp_path / "replay", timeout=30
    )
    assert calls == ["baseline", "half_dt"]
    assert result.status == "failed"
    assert len(result.profiles) == 2
    assert all(profile.files for profile in result.profiles)
    assert result.physical_evaluated is False
    for profile in result.profiles:
        assert any(
            store.read_artifact(member.artifact) == b"explicit test runtime double"
            for member in profile.files
        )


def test_runtime_error_retains_partial_logs_in_replay_receipt(tmp_path, monkeypatch):
    from self_improving.harness.x2env.replay import GenesisReplayExecutor

    scene = RuntimeScene(
        seed=11,
        scene_ir_sha256="b" * 64,
        entities=(
            RuntimeEntity(
                id="table",
                category="table",
                kind="structural_box",
                position_m=(0.0, 0.0, 0.7),
                orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
                size_m=(1.0, 1.0, 0.04),
                friction=0.5,
            ),
        ),
    )
    store = Store(tmp_path / "state")

    def runtime_double(value, **kwargs):
        out = kwargs["output_dir"]
        out.mkdir()
        (out / "stderr.log").write_text("runtime failed after writing diagnostics")
        raise OSError("runtime unavailable")

    monkeypatch.setattr("self_improving.harness.x2env.replay.run_scene", runtime_double)
    result = GenesisReplayExecutor(store, runtime_roots={}).replay(
        scene, package_root=tmp_path, output_root=tmp_path / "replay", timeout=30
    )
    assert result.status == "failed"
    assert result.error_code == "runtime_execution_error"
    assert len(result.profiles) == 1
    assert any(
        store.read_artifact(item.artifact) == b"runtime failed after writing diagnostics"
        for item in result.profiles[0].files
    )
    assert (
        json.loads(store.read_artifact(result.receipt))["error_code"] == "runtime_execution_error"
    )
