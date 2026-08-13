from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from scripts.pose_conditioned_trajectory_policy import (
    Demonstration,
    PoseConditionedTrajectoryPolicy,
    canonicalize_trajectory,
    gripper_transitions,
    placement_feature,
    train_checkpoint,
)


def synthetic_actions(feature: np.ndarray, lengths: tuple[int, int, int]) -> np.ndarray:
    total = sum(lengths)
    time = np.linspace(0.0, 1.0, total)
    actions = np.zeros((total, 14), dtype=np.float64)
    actions[:, 0] = 1.5 * feature[0] - 0.5 * feature[2] + time
    actions[:, 1] = -0.75 * feature[1] + 0.25 * feature[3] + time**2
    actions[:, 6] = 1.0
    actions[lengths[0] : lengths[0] + lengths[1], 6] = 0.0
    actions[:, 13] = 1.0
    return actions


def test_phase_alignment_preserves_binary_gripper() -> None:
    actions = synthetic_actions(np.asarray([1.0, 2.0, 3.0, 4.0]), (5, 7, 4))
    assert gripper_transitions(actions, 6) == [5, 12]
    aligned = canonicalize_trajectory(actions, 6, (8, 9, 6))
    assert aligned.shape == (23, 14)
    assert gripper_transitions(aligned, 6) == [8, 17]
    assert set(np.unique(aligned[:, 6])) == {0.0, 1.0}


def test_placement_feature_uses_source_and_target_xy() -> None:
    placement = {
        "objects": [
            {"id": "source", "role": "manipuland_candidate", "pose": {"xyz": [1, 2, 3]}},
            {"id": "target", "role": "support_or_target_candidate", "pose": {"xyz": [4, 5, 6]}},
        ]
    }
    np.testing.assert_allclose(placement_feature(placement), [1, 2, 4, 5])


def test_affine_predictor_recovers_held_out_synthetic_feature(tmp_path: Path) -> None:
    demonstrations = []
    features = [
        np.asarray([-1.0, -1.0, -1.0, -1.0]),
        np.asarray([1.0, -1.0, -1.0, 1.0]),
        np.asarray([-1.0, 1.0, 1.0, -1.0]),
        np.asarray([1.0, 1.0, 1.0, 1.0]),
        np.asarray([0.0, 0.0, 0.0, 0.0]),
        np.asarray([0.5, -0.5, 0.5, -0.5]),
    ]
    for index, feature in enumerate(features):
        hdf5_path = tmp_path / f"episode_{index}.hdf5"
        placement_path = tmp_path / f"placement_{index}.json"
        hdf5_path.write_bytes(f"episode-{index}".encode())
        placement_path.write_text(json.dumps({"index": index}), encoding="utf-8")
        demonstrations.append(
            Demonstration(
                hdf5_path=hdf5_path,
                placement_path=placement_path,
                pose_signature=f"signature-{index}",
                seed=index,
                feature=feature,
                actions=synthetic_actions(feature, (5 + index % 2, 7, 4)),
            )
        )

    checkpoint = tmp_path / "policy.npz"
    metadata = tmp_path / "training_report.json"
    train_checkpoint(demonstrations, checkpoint, metadata, ridge=1e-8)
    policy = PoseConditionedTrajectoryPolicy(checkpoint, metadata)
    held_out = np.asarray([0.25, -0.25, -0.5, 0.5])
    prediction, record = policy.predict(held_out, predictor="affine")
    expected = canonicalize_trajectory(synthetic_actions(held_out, (5, 7, 4)), 6, (6, 7, 4))
    # The demonstrations use alternating phase lengths, so canonicalization adds
    # a small interpolation error even though the feature mapping is affine.
    np.testing.assert_allclose(prediction[:, :2], expected[:, :2], atol=2.5e-2)
    assert record["finite"] is True
    assert record["action_count"] == len(expected)
