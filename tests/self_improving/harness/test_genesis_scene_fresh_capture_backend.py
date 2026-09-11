from pathlib import Path

import pytest


def test_restored_capture_requires_materialized_asset_before_output(tmp_path):
    from self_improving.harness.genesis_scene_fresh_capture_backend import capture_restored_scene

    with pytest.raises(ValueError, match="absolute"):
        capture_restored_scene({}, {}, Path("missing.urdf"), tmp_path / "output")
    assert not (tmp_path / "output").exists()
