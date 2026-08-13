#!/usr/bin/env python3
"""Train and execute a bounded pose-conditioned trajectory policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


GRIPPER_INDICES = (6, 13)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def infer_source_target_ids(placement: dict[str, Any]) -> tuple[str, str]:
    variation = placement.get("robustness_variation", {})
    source_id = variation.get("source_id")
    target_id = variation.get("target_id")
    if source_id and target_id:
        return str(source_id), str(target_id)

    by_role: dict[str, str] = {}
    for obj in placement.get("objects", []):
        role = str(obj.get("role", ""))
        if role:
            by_role[role] = str(obj["id"])
    source_id = by_role.get("manipuland_candidate") or by_role.get("scene_object")
    target_id = by_role.get("support_or_target_candidate") or by_role.get("container_candidate")
    if not source_id or not target_id:
        raise ValueError("Could not infer source and target ids from placement")
    return source_id, target_id


def object_by_id(placement: dict[str, Any], object_id: str) -> dict[str, Any]:
    for obj in placement.get("objects", []):
        if str(obj.get("id")) == object_id:
            return obj
    raise KeyError(f"Placement has no object {object_id!r}")


def placement_feature(placement: dict[str, Any]) -> np.ndarray:
    source_id, target_id = infer_source_target_ids(placement)
    source_xyz = object_by_id(placement, source_id)["pose"]["xyz"]
    target_xyz = object_by_id(placement, target_id)["pose"]["xyz"]
    return np.asarray(
        [source_xyz[0], source_xyz[1], target_xyz[0], target_xyz[1]],
        dtype=np.float64,
    )


def runtime_feature(actors: dict[str, Any], source_id: str, target_id: str) -> np.ndarray:
    source = np.asarray(actors[source_id].get_pose().p, dtype=np.float64)
    target = np.asarray(actors[target_id].get_pose().p, dtype=np.float64)
    return np.asarray([source[0], source[1], target[0], target[1]], dtype=np.float64)


def gripper_transitions(actions: np.ndarray, index: int) -> list[int]:
    state = np.asarray(actions[:, index] > 0.5, dtype=bool)
    return (np.flatnonzero(state[1:] != state[:-1]) + 1).astype(int).tolist()


def active_gripper_index(trajectories: Iterable[np.ndarray]) -> int:
    counts = {
        index: sum(min(len(gripper_transitions(actions, index)), 2) for actions in trajectories)
        for index in GRIPPER_INDICES
    }
    selected = max(GRIPPER_INDICES, key=lambda index: (counts[index], -index))
    if counts[selected] == 0:
        raise ValueError("No gripper transition was found in the demonstrations")
    return selected


def phase_slices(actions: np.ndarray, gripper_index: int) -> list[slice]:
    transitions = gripper_transitions(actions, gripper_index)
    if len(transitions) < 2:
        raise ValueError(
            f"Expected at least two gripper transitions at index {gripper_index}; found {transitions}"
        )
    first, second = transitions[:2]
    if not (0 < first < second < len(actions)):
        raise ValueError(f"Invalid phase boundaries {(first, second)} for trajectory length {len(actions)}")
    return [slice(0, first), slice(first, second), slice(second, len(actions))]


def infer_phase_lengths(trajectories: Iterable[np.ndarray], gripper_index: int) -> tuple[int, int, int]:
    observed = []
    for actions in trajectories:
        observed.append([phase.stop - phase.start for phase in phase_slices(actions, gripper_index)])
    lengths = np.rint(np.median(np.asarray(observed, dtype=np.float64), axis=0)).astype(int)
    lengths = np.maximum(lengths, 2)
    return tuple(int(value) for value in lengths)


def resample_rows(values: np.ndarray, output_length: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or len(values) < 1:
        raise ValueError("Trajectory phase must be a non-empty rank-2 array")
    if output_length < 1:
        raise ValueError("output_length must be positive")
    if len(values) == 1:
        return np.repeat(values, output_length, axis=0)
    source_t = np.linspace(0.0, 1.0, len(values))
    output_t = np.linspace(0.0, 1.0, output_length)
    return np.stack(
        [np.interp(output_t, source_t, values[:, column]) for column in range(values.shape[1])],
        axis=1,
    )


def canonicalize_trajectory(
    actions: np.ndarray,
    gripper_index: int,
    phase_lengths: tuple[int, int, int],
) -> np.ndarray:
    phases = phase_slices(actions, gripper_index)
    canonical = np.concatenate(
        [resample_rows(actions[phase], length) for phase, length in zip(phases, phase_lengths, strict=True)],
        axis=0,
    )
    for index in GRIPPER_INDICES:
        canonical[:, index] = (canonical[:, index] > 0.5).astype(np.float64)
    return canonical


@dataclass(frozen=True)
class Demonstration:
    hdf5_path: Path
    placement_path: Path
    pose_signature: str
    seed: int
    feature: np.ndarray
    actions: np.ndarray


def _resolve_recorded_path(value: str, repository_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = repository_root / path
    return path.resolve()


def load_demonstrations(dataset_dir: Path, repository_root: Path) -> list[Demonstration]:
    import h5py

    demonstrations: list[Demonstration] = []
    paths = sorted(dataset_dir.glob("*.hdf5"), key=lambda path: int(path.stem.rsplit("_", 1)[-1]))
    if not paths:
        raise FileNotFoundError(f"No episode_*.hdf5 files found in {dataset_dir}")
    for path in paths:
        with h5py.File(path, "r") as root:
            actions = np.asarray(root["action"], dtype=np.float64)
            placement_value = str(root.attrs.get("source_placement_path", ""))
            signature = str(root.attrs.get("source_pose_signature", ""))
            seed = int(root.attrs.get("source_seed", -1))
        if actions.ndim != 2 or actions.shape[1] != 14:
            raise ValueError(f"Expected 14-D action trajectory in {path}, found {actions.shape}")
        if not placement_value:
            raise ValueError(f"HDF5 episode has no source_placement_path attribute: {path}")
        placement_path = _resolve_recorded_path(placement_value, repository_root)
        if not placement_path.is_file():
            raise FileNotFoundError(f"Recorded placement does not exist: {placement_path}")
        placement = read_json(placement_path)
        feature = placement_feature(placement)
        if not signature:
            signature = hashlib.sha256(feature.tobytes()).hexdigest()
        demonstrations.append(
            Demonstration(path, placement_path, signature, seed, feature, actions)
        )
    return demonstrations


def rbf_kernel(first: np.ndarray, second: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    squared = np.sum((first[:, None, :] - second[None, :, :]) ** 2, axis=2)
    return np.exp(-squared / (2.0 * sigma * sigma))


def fit_affine(features: np.ndarray, targets: np.ndarray, ridge: float) -> np.ndarray:
    design = np.column_stack([np.ones(len(features)), features])
    penalty = np.eye(design.shape[1], dtype=np.float64) * ridge
    penalty[0, 0] = 0.0
    return np.linalg.solve(design.T @ design + penalty, design.T @ targets)


def predict_affine(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(features)), features])
    return design @ weights


def fit_rbf(features: np.ndarray, targets: np.ndarray, sigma: float, ridge: float) -> np.ndarray:
    kernel = rbf_kernel(features, features, sigma)
    return np.linalg.solve(kernel + ridge * np.eye(len(features)), targets)


def leave_one_out_scores(
    features: np.ndarray,
    targets: np.ndarray,
    ridge: float,
    sigma_candidates: tuple[float, ...],
) -> dict[str, Any]:
    if len(features) < 3:
        return {
            "affine_mse": None,
            "rbf": [],
            "selected_rbf_sigma": sigma_candidates[0],
            "selection_reason": "fewer_than_three_unique_placements",
        }

    affine_errors: list[float] = []
    rbf_errors: dict[float, list[float]] = {sigma: [] for sigma in sigma_candidates}
    for held_out in range(len(features)):
        mask = np.arange(len(features)) != held_out
        affine_weights = fit_affine(features[mask], targets[mask], ridge)
        affine_prediction = predict_affine(features[held_out : held_out + 1], affine_weights)[0]
        affine_errors.append(float(np.mean((affine_prediction - targets[held_out]) ** 2)))
        for sigma in sigma_candidates:
            weights = fit_rbf(features[mask], targets[mask], sigma, ridge)
            prediction = rbf_kernel(features[held_out : held_out + 1], features[mask], sigma) @ weights
            rbf_errors[sigma].append(float(np.mean((prediction[0] - targets[held_out]) ** 2)))

    rbf_rows = [
        {"sigma": sigma, "mse": float(np.mean(errors))}
        for sigma, errors in rbf_errors.items()
    ]
    selected = min(rbf_rows, key=lambda row: (row["mse"], row["sigma"]))
    return {
        "affine_mse": float(np.mean(affine_errors)),
        "rbf": rbf_rows,
        "selected_rbf_sigma": float(selected["sigma"]),
        "selection_reason": "minimum_leave_one_unique_placement_out_trajectory_mse",
    }


def train_checkpoint(
    demonstrations: list[Demonstration],
    checkpoint_path: Path,
    metadata_path: Path,
    ridge: float = 1e-5,
    sigma_candidates: tuple[float, ...] = (0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0),
) -> dict[str, Any]:
    if len(demonstrations) < 2:
        raise ValueError("At least two demonstrations are required")
    gripper_index = active_gripper_index(demo.actions for demo in demonstrations)
    phase_lengths = infer_phase_lengths((demo.actions for demo in demonstrations), gripper_index)
    canonical = [
        canonicalize_trajectory(demo.actions, gripper_index, phase_lengths)
        for demo in demonstrations
    ]

    grouped: dict[str, list[int]] = {}
    for index, demo in enumerate(demonstrations):
        grouped.setdefault(demo.pose_signature, []).append(index)
    signatures = sorted(grouped)
    features = np.stack([demonstrations[grouped[signature][0]].feature for signature in signatures])
    trajectories = np.stack(
        [np.mean([canonical[index] for index in grouped[signature]], axis=0) for signature in signatures]
    )
    target_shape = trajectories.shape[1:]
    targets = trajectories.reshape(len(trajectories), -1)
    feature_mean = np.mean(features, axis=0)
    feature_scale = np.std(features, axis=0)
    feature_scale = np.where(feature_scale < 1e-6, 1.0, feature_scale)
    normalized = (features - feature_mean) / feature_scale

    cv = leave_one_out_scores(normalized, targets, ridge, sigma_candidates)
    sigma = float(cv["selected_rbf_sigma"])
    affine_weights = fit_affine(normalized, targets, ridge)
    rbf_weights = fit_rbf(normalized, targets, sigma, ridge)
    action_min = np.min(trajectories, axis=(0, 1))
    action_max = np.max(trajectories, axis=(0, 1))

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        checkpoint_path,
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        train_features=features,
        train_features_normalized=normalized,
        train_trajectories=trajectories,
        affine_weights=affine_weights,
        rbf_weights=rbf_weights,
        action_min=action_min,
        action_max=action_max,
    )
    metadata = {
        "schema_version": "alchedata.pose_conditioned_trajectory_policy.v0",
        "status": "pass_pose_conditioned_trajectory_training",
        "policy_type": "supervised_pose_conditioned_phase_aligned_trajectory_regression",
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "demonstration_count": len(demonstrations),
        "unique_placement_count": len(signatures),
        "training_pose_signatures": signatures,
        "training_features_xy_m": features.tolist(),
        "training_episodes": [
            {
                "hdf5": str(demo.hdf5_path),
                "hdf5_sha256": sha256_file(demo.hdf5_path),
                "placement": str(demo.placement_path),
                "placement_sha256": sha256_file(demo.placement_path),
                "pose_signature": demo.pose_signature,
                "seed": demo.seed,
                "action_count": len(demo.actions),
            }
            for demo in demonstrations
        ],
        "action_dim": int(target_shape[1]),
        "canonical_action_count": int(target_shape[0]),
        "active_gripper_index": gripper_index,
        "phase_lengths": list(phase_lengths),
        "feature_definition": ["source_x_m", "source_y_m", "target_x_m", "target_y_m"],
        "ridge": ridge,
        "rbf_sigma": sigma,
        "cross_validation": cv,
        "available_predictors": ["affine", "rbf", "blend", "nearest"],
        "claim_boundary": (
            "This checkpoint is learned from successful RoboTwin demonstrations, but it is privileged and open-loop: "
            "it conditions once on simulator source/target XY poses and emits a full joint trajectory. It is not a "
            "vision policy, language-conditioned policy, closed-loop controller, or broad task-generalization result."
        ),
    }
    write_json(metadata_path, metadata)
    return metadata


class PoseConditionedTrajectoryPolicy:
    def __init__(self, checkpoint_path: Path, metadata_path: Path):
        self.checkpoint_path = checkpoint_path.expanduser().resolve()
        self.metadata_path = metadata_path.expanduser().resolve()
        self.metadata = read_json(self.metadata_path)
        if sha256_file(self.checkpoint_path) != self.metadata["checkpoint_sha256"]:
            raise ValueError("Checkpoint SHA-256 does not match metadata")
        arrays = np.load(self.checkpoint_path, allow_pickle=False)
        self.feature_mean = arrays["feature_mean"]
        self.feature_scale = arrays["feature_scale"]
        self.train_features = arrays["train_features"]
        self.train_features_normalized = arrays["train_features_normalized"]
        self.train_trajectories = arrays["train_trajectories"]
        self.affine_weights = arrays["affine_weights"]
        self.rbf_weights = arrays["rbf_weights"]
        self.action_min = arrays["action_min"]
        self.action_max = arrays["action_max"]

    def predict(
        self,
        feature: np.ndarray,
        predictor: str = "affine",
        extrapolation_margin: float = 0.35,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        feature = np.asarray(feature, dtype=np.float64).reshape(1, 4)
        normalized = (feature - self.feature_mean) / self.feature_scale
        affine = predict_affine(normalized, self.affine_weights)[0]
        sigma = float(self.metadata["rbf_sigma"])
        rbf = (rbf_kernel(normalized, self.train_features_normalized, sigma) @ self.rbf_weights)[0]
        distances = np.linalg.norm(normalized - self.train_features_normalized, axis=1)
        nearest_index = int(np.argmin(distances))
        nearest = self.train_trajectories[nearest_index].reshape(-1)
        if predictor == "affine":
            flat = affine
        elif predictor == "rbf":
            flat = rbf
        elif predictor == "blend":
            flat = 0.5 * affine + 0.5 * rbf
        elif predictor == "nearest":
            flat = nearest
        else:
            raise ValueError(f"Unsupported predictor {predictor!r}")
        actions = flat.reshape(self.train_trajectories.shape[1:])

        action_range = self.action_max - self.action_min
        margin = np.maximum(0.05, extrapolation_margin * action_range)
        actions = np.clip(actions, self.action_min - margin, self.action_max + margin)
        for index in GRIPPER_INDICES:
            actions[:, index] = (actions[:, index] > 0.5).astype(np.float64)
        return actions, {
            "predictor": predictor,
            "input_feature_xy_m": feature[0].tolist(),
            "normalized_feature": normalized[0].tolist(),
            "nearest_training_index": nearest_index,
            "nearest_training_feature_xy_m": self.train_features[nearest_index].tolist(),
            "nearest_training_distance_normalized": float(distances[nearest_index]),
            "rbf_max_similarity": float(np.max(rbf_kernel(normalized, self.train_features_normalized, sigma))),
            "action_count": len(actions),
            "action_dim": actions.shape[1],
            "finite": bool(np.isfinite(actions).all()),
            "extrapolation_margin": extrapolation_margin,
        }
