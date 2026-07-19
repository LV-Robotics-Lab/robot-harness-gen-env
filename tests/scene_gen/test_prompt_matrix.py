from __future__ import annotations

import json
from pathlib import Path

from scene_gen.parser import parse_rule_based
from scene_gen.runtime_sampling import video_sample_steps

ROOT = Path(__file__).resolve().parents[2]


def test_prompt_matrix_parses_deterministically_for_every_declared_seed() -> None:
    matrix = json.loads(
        (ROOT / "tests" / "fixtures" / "prompt_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    digests: set[str] = set()
    for case in matrix["cases"]:
        for seed in matrix["seeds"]:
            first = parse_rule_based(case["prompt"], seed=seed)
            second = parse_rule_based(case["prompt"], seed=seed)
            assert first.digest() == second.digest()
            assert first.request == case["prompt"]
            digests.add(first.digest())
    assert len(digests) == len(matrix["cases"]) * len(matrix["seeds"])


def test_video_samples_cover_full_settle_window_without_duplicates() -> None:
    samples = video_sample_steps(total_steps=900, requested_frames=120)
    assert len(samples) == 120
    assert len(set(samples)) == 120
    assert samples[0] == 0
    assert samples[-2] == 118
    assert samples[-1] == 899
