from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_sceneagent_acceptance_audit import build


def test_sceneagent_acceptance_audit_covers_all_dashboard_items() -> None:
    root = Path(__file__).resolve().parents[1]
    if not (root / "runs" / "final_acceptance_20260715").is_dir():
        pytest.skip("full SceneAgent run and media bundle is intentionally external")
    audit = build()

    assert audit["status"] == "pass_all_8_sceneagent_acceptance_items"
    assert audit["acceptance_item_count"] == 8
    assert audit["pass_count"] == 8
    assert [row["acceptance_item"] for row in audit["items"]] == list(range(1, 9))
    assert all(row["status"] == "pass" for row in audit["items"])

    decoupling = audit["items"][-1]["observed"]
    assert decoupling["tasks"][0]["frame_count"] >= 500
    assert decoupling["tasks"][1]["frame_count"] >= 500
    assert all(row["check_success"] for row in decoupling["tasks"])
