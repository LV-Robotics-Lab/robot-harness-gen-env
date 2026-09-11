import json
import os
from pathlib import Path

import pytest


def test_unknown_scene_profile_is_rejected_before_runtime_or_output(tmp_path):
    from self_improving.harness.genesis_scene_cpu_backend import run_genesis_scene_cpu

    output = tmp_path / "run"
    with pytest.raises(ValueError, match="profile"):
        run_genesis_scene_cpu({}, Path("missing.urdf"), output, profile="fast")
    assert not output.exists()


@pytest.mark.skipif(
    not os.environ.get("GOLDEN_SCENE_CPU_JOB"), reason="real Genesis scene job required"
)
def test_real_scene_keeps_full_trace_and_sequential_lossless_media():
    from self_improving.harness.genesis_scene_cpu_backend import run_genesis_scene_cpu

    job = json.loads(Path(os.environ["GOLDEN_SCENE_CPU_JOB"]).read_text())
    output = Path(job["output_dir"])
    result = run_genesis_scene_cpu(
        job["scene_spec"], Path(job["urdf_path"]), output, profile=job["profile"]
    )
    assert result["status"] == "complete"
    assert result["rows"] == (1001 if job["profile"] == "baseline" else 2001)
    assert result["media"]["total_frames"] == 41
    assert result["media"]["lossless_pixels_equal"] is True
    assert (output / "preview.mp4").stat().st_size > 0
    assert (output / "simulation.mkv").stat().st_size > 0
