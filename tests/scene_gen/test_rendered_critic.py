from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scene_gen.catalog import load_catalog
from scene_gen.parser import parse_rule_based
from scene_gen.rendered_critic import REQUIRED_CHECKS, review_rendered_scene
from scene_gen.solver import solve_scene

ROOT = Path(__file__).resolve().parents[2]


def solved_case():
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    return solve_scene(parse_rule_based("A can is left of a basket.", seed=5), catalog)


def test_rendered_critic_requires_complete_machine_readable_vlm_checks(tmp_path: Path) -> None:
    image_path = tmp_path / "preview.png"
    Image.new("RGB", (64, 64), (120, 130, 140)).save(image_path)

    def infer(**_kwargs):
        return json.dumps(
            {
                "status": "pass",
                "summary": "Both requested objects are visible and supported.",
                "checks": [
                    {"name": name, "status": "not_applicable" if name == "articulation_state" else "pass", "evidence": "visible"}
                    for name in sorted(REQUIRED_CHECKS)
                ],
                "issues": [],
            }
        )

    review = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[image_path],
        infer=infer,
        model_name="test-vlm",
    )
    assert review["status"] == "pass"
    assert {item["name"] for item in review["checks"]} == REQUIRED_CHECKS
    assert review["images"][0]["sha256"]
    assert review["raw_response"]


def test_rendered_critic_fails_closed_on_missing_checks_or_images(tmp_path: Path) -> None:
    image_path = tmp_path / "preview.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(image_path)
    incomplete = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[image_path],
        infer=lambda **_kwargs: '{"status":"pass","checks":[],"issues":[]}',
        model_name="test-vlm",
    )
    assert incomplete["status"] == "fail"
    assert incomplete["missing_required_checks"]

    missing = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[tmp_path / "missing.png"],
        infer=lambda **_kwargs: "{}",
        model_name="test-vlm",
    )
    assert missing["status"] == "fail"
    assert missing["missing_images"]


def test_rendered_critic_repairs_one_incomplete_response(tmp_path: Path) -> None:
    image_path = tmp_path / "preview.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(image_path)
    calls = 0

    def infer(**_kwargs):
        nonlocal calls
        calls += 1
        names = sorted(REQUIRED_CHECKS - ({"overall_prompt_match"} if calls == 1 else (REQUIRED_CHECKS - {"overall_prompt_match"})))
        return json.dumps(
            {
                "status": "pass",
                "summary": "visible",
                "checks": [
                    {
                        "name": name,
                        "status": "not_applicable" if name == "articulation_state" else "pass",
                        "evidence": "visible",
                    }
                    for name in names
                ],
                "issues": [],
            }
        )

    review = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[image_path],
        infer=infer,
        model_name="test-vlm",
    )
    assert calls == 2
    assert review["status"] == "pass"
    assert not review["missing_required_checks"]
    assert len(review["repair_raw_responses"]) == 1


def test_rendered_critic_cannot_pass_with_major_issue(tmp_path: Path) -> None:
    image_path = tmp_path / "preview.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(image_path)

    def infer(**_kwargs):
        return json.dumps(
            {
                "status": "pass",
                "summary": "Contradictory response.",
                "checks": [
                    {
                        "name": name,
                        "status": "not_applicable" if name == "articulation_state" else "pass",
                        "evidence": "visible",
                    }
                    for name in sorted(REQUIRED_CHECKS)
                ],
                "issues": [
                    {"severity": "major", "target": "scene", "message": "visible mismatch"}
                ],
            }
        )

    review = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[image_path],
        infer=infer,
        model_name="test-vlm",
    )
    assert review["status"] == "fail"
    assert review["issues"] == [
        {"severity": "major", "target": "scene", "message": "visible mismatch"}
    ]


def test_rendered_critic_normalizes_deterministic_check_applicability(tmp_path: Path) -> None:
    image_path = tmp_path / "preview.png"
    Image.new("RGB", (16, 16), (0, 0, 0)).save(image_path)

    def infer(**_kwargs):
        return json.dumps(
            {
                "status": "pass",
                "summary": "All visible checks pass.",
                "checks": [
                    {"name": name, "status": "pass", "evidence": "visible"}
                    for name in sorted(REQUIRED_CHECKS)
                ],
                "issues": [],
            }
        )

    review = review_rendered_scene(
        resolved=solved_case(),
        image_paths=[image_path],
        infer=infer,
        model_name="test-vlm",
    )
    assert review["status"] == "pass"
    articulation = next(
        item for item in review["checks"] if item["name"] == "articulation_state"
    )
    assert articulation["status"] == "not_applicable"
    assert review["contract_normalizations"][0]["model_status"] == "pass"
