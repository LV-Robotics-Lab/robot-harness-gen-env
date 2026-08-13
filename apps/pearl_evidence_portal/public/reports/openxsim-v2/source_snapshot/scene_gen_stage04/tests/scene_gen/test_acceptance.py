from __future__ import annotations

import pytest

from scene_gen.acceptance import summarize_acceptance


def test_acceptance_requires_at_least_95_percent_passes() -> None:
    passing = [{"status": "pass"} for _ in range(95)] + [{"status": "fail"} for _ in range(5)]
    report = summarize_acceptance(passing)
    assert report["status"] == "pass"
    assert report["pass_rate"] == pytest.approx(0.95)

    failing = [{"status": "pass"} for _ in range(94)] + [{"status": "fail"} for _ in range(6)]
    assert summarize_acceptance(failing)["status"] == "fail"


def test_acceptance_rejects_empty_and_invalid_threshold() -> None:
    assert summarize_acceptance([])["status"] == "fail"
    with pytest.raises(ValueError, match="minimum_pass_rate"):
        summarize_acceptance([], minimum_pass_rate=1.1)
