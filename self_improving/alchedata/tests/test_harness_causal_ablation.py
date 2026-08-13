import json
from pathlib import Path

import numpy as np
import pytest

from scripts.build_harness_causal_ablation import build_ablation
from scripts.harness_observation_adapter import apply_runtime_color_adapter


def evaluation_report(adapter: str, successes: int) -> dict:
    episodes = [
        {
            "policy_success": index < successes,
            "execution_complete": True,
            "infrastructure_error": None,
        }
        for index in range(3)
    ]
    return {
        "status": "pass_generated_act_evaluate_execution",
        "task_id": "task_apple_plate",
        "task_config": "demo_clean",
        "held_out_seeds": [4, 5, 6],
        "source_training_seeds": [0, 1, 2],
        "eval_placements": [
            {
                "placement_id": f"fixed_seed_{seed}",
                "placement_sha256": "a" * 64,
                "pose_signature": None,
                "seed": seed,
            }
            for seed in (4, 5, 6)
        ],
        "model": {
            "checkpoint_sha256": "b" * 64,
            "dataset_stats_sha256": "c" * 64,
            "config": {"chunk_size": 161},
        },
        "camera_adapter": {
            "training_key": "cam_high",
            "runtime_source": "head_camera",
            "runtime_color_adapter": adapter,
        },
        "evaluation_scope": {
            "placement_randomization": "fixed_action_repair_placement",
            "domain_randomization": False,
            "action_selection": "execute_full_161_action_chunk_before_replan",
        },
        "episode_count": 3,
        "execution_count": 3,
        "success_count": successes,
        "policy_success_rate": successes / 3,
        "episodes": episodes,
    }


def write_report(path: Path, report: dict) -> Path:
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_runtime_color_adapter_is_explicit_and_reversible() -> None:
    rgb = np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)

    assert np.array_equal(apply_runtime_color_adapter(rgb, "identity"), rgb)
    assert np.array_equal(
        apply_runtime_color_adapter(rgb, "swap_red_blue"),
        np.array([[[3, 2, 1], [6, 5, 4]]], dtype=np.uint8),
    )
    with pytest.raises(ValueError, match="Unsupported"):
        apply_runtime_color_adapter(rgb, "unknown")


def test_matched_ablation_promotes_candidate_at_threshold(tmp_path: Path) -> None:
    baseline = write_report(tmp_path / "baseline.json", evaluation_report("swap_red_blue", 0))
    candidate = write_report(tmp_path / "candidate.json", evaluation_report("identity", 3))

    report = build_ablation(baseline, candidate)

    assert report["status"] == "pass_matched_harness_ablation_candidate_promoted"
    assert report["outcomes"]["success_delta"] == 3
    assert report["promotion"]["decision"] == "accept"
    assert report["experiment"]["intervention"]["only_declared_difference"] is True


def test_matched_ablation_rejects_protocol_mismatch(tmp_path: Path) -> None:
    baseline_report = evaluation_report("swap_red_blue", 0)
    candidate_report = evaluation_report("identity", 3)
    candidate_report["held_out_seeds"] = [7, 8, 9]
    baseline = write_report(tmp_path / "baseline.json", baseline_report)
    candidate = write_report(tmp_path / "candidate.json", candidate_report)

    with pytest.raises(AssertionError, match="differs outside"):
        build_ablation(baseline, candidate)
