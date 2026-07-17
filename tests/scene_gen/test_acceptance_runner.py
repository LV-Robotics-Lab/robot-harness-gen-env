from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "script" / "run_100_seed_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_100_seed_acceptance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_seed_scene_dir_retains_each_seed_independently(tmp_path: Path) -> None:
    first = MODULE.seed_scene_dir(tmp_path, 0, "same_scene")
    second = MODULE.seed_scene_dir(tmp_path, 1, "same_scene")

    assert first == tmp_path / "seed_000000" / "same_scene"
    assert second == tmp_path / "seed_000001" / "same_scene"
    assert first != second


def test_batch_summary_requires_retained_evidence() -> None:
    outcomes = [
        {
            "status": "pass",
            "scene_dir_relative": "seed_000000/same_scene",
            "resolved_scene_sha256": "digest-0",
            "evidence_retained": False,
        }
    ]

    report = MODULE.batch_summary(
        outcomes,
        prompt="test",
        seed_start=0,
        seed_count=1,
        catalog_sha256="catalog",
        runtime_required=True,
        minimum_pass_rate=0.95,
        generator_commit="generator",
        robotwin_commit="robotwin",
        complete=True,
    )

    assert report["status"] == "pass"
    assert report["retained_scene_count"] == 1
    assert report["unique_resolved_scene_count"] == 1
    assert report["passing_evidence_retained"] is False
