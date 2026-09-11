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


def test_real_v2_scene_timestamps_come_from_actual_capture():
    import json
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    import pytest

    from self_improving.harness.genesis_scene_cpu_backend import run_genesis_scene_cpu_v2

    configured = os.environ.get("GOLDEN_SCENE_CPU_V2_JOB")
    if not configured:
        pytest.skip("requires a real fixed software renderer deployment")
    job = json.loads(Path(configured).read_bytes())
    started = datetime.now(timezone.utc)
    run_genesis_scene_cpu_v2(
        job["scene_spec"], Path(job["urdf_path"]), Path(job["output_dir"]), profile=job["profile"]
    )
    finished = datetime.now(timezone.utc)
    media = json.loads((Path(job["output_dir"]) / "media.json").read_bytes())
    times = [datetime.fromisoformat(frame["captured_at"]) for frame in media["frames"]]
    assert len(times) == 41 and times == sorted(times)
    assert started <= times[0] <= times[-1] <= finished
    assert media["lossless_pixels_equal"] is True
