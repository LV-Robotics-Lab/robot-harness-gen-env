from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "script" / "run_scene_runtime.py"

spec = importlib.util.spec_from_file_location("run_scene_runtime", SCRIPT)
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_task_config_path_supports_legacy_and_env_cfg_layouts(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "task_config" / "demo_clean.yml"
    modern = tmp_path / "env_cfg" / "task_config" / "demo_clean.yml"

    legacy.parent.mkdir(parents=True)
    modern.parent.mkdir(parents=True)
    legacy.write_text("legacy\n", encoding="utf-8")
    modern.write_text("modern\n", encoding="utf-8")

    assert runtime._robotwin_task_config_path(tmp_path, "demo_clean") == legacy

    legacy.unlink()

    assert runtime._robotwin_task_config_path(tmp_path, "demo_clean") == modern


def test_task_config_path_reports_both_searched_layouts(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError) as error:
        runtime._robotwin_task_config_path(tmp_path, "missing")

    message = str(error.value)
    assert str(tmp_path / "task_config" / "missing.yml") in message
    assert str(tmp_path / "env_cfg" / "task_config" / "missing.yml") in message


def test_adaptive_extension_replaces_only_the_final_video_frame() -> None:
    frames = [np.full((1, 1, 3), fill_value=value, dtype=np.uint8) for value in (1, 2, 3)]
    extended_final_frame = np.full((1, 1, 3), fill_value=9, dtype=np.uint8)
    captures: list[str] = []

    synchronized_frames, synchronized_steps = runtime.synchronize_video_timeline(
        frames,
        (0, 1, 899),
        base_simulation_step_count=900,
        settle_extra_steps=60,
        capture_final_frame=lambda: captures.append("captured") or extended_final_frame,
    )

    assert len(synchronized_frames) == 3
    assert [int(frame[0, 0, 0]) for frame in synchronized_frames] == [1, 2, 9]
    assert synchronized_steps == (0, 1, 959)
    assert captures == ["captured"]


def test_video_timeline_is_untouched_without_an_adaptive_extension() -> None:
    frames = [np.full((1, 1, 3), fill_value=4, dtype=np.uint8)]
    captures: list[str] = []

    synchronized_frames, synchronized_steps = runtime.synchronize_video_timeline(
        frames,
        (899,),
        base_simulation_step_count=900,
        settle_extra_steps=0,
        capture_final_frame=lambda: captures.append("captured"),
    )

    assert synchronized_frames is frames
    assert synchronized_steps == (899,)
    assert int(synchronized_frames[0][0, 0, 0]) == 4
    assert captures == []


def test_adaptive_extension_updates_a_single_frame_video() -> None:
    frames = [np.full((1, 1, 3), fill_value=4, dtype=np.uint8)]
    extended_final_frame = np.full((1, 1, 3), fill_value=8, dtype=np.uint8)

    synchronized_frames, synchronized_steps = runtime.synchronize_video_timeline(
        frames,
        (899,),
        base_simulation_step_count=900,
        settle_extra_steps=30,
        capture_final_frame=lambda: extended_final_frame,
    )

    assert len(synchronized_frames) == 1
    assert int(synchronized_frames[0][0, 0, 0]) == 8
    assert synchronized_steps == (929,)


def test_adaptive_extension_does_not_capture_when_video_is_disabled() -> None:
    captures: list[str] = []

    synchronized_frames, synchronized_steps = runtime.synchronize_video_timeline(
        [],
        (),
        base_simulation_step_count=900,
        settle_extra_steps=30,
        capture_final_frame=lambda: captures.append("captured"),
    )

    assert synchronized_frames == []
    assert synchronized_steps == ()
    assert captures == []


def test_runtime_timeline_evidence_reports_base_and_actual_step_counts() -> None:
    assert runtime.runtime_timeline_evidence(
        base_simulation_step_count=900,
        settle_extra_steps=60,
        video_sample_step_indices=(0, 1, 959),
    ) == {
        "base_simulation_step_count": 900,
        "simulation_step_count": 960,
        "video_sample_step_indices": [0, 1, 959],
    }
