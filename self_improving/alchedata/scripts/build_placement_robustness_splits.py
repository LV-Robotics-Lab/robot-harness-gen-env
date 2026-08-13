#!/usr/bin/env python3
"""Build deterministic, disjoint train/eval placement splits from a selection2env placement."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def infer_source_target_ids(placement: dict[str, Any]) -> tuple[str, str]:
    by_role = {str(obj.get("role", "")): str(obj["id"]) for obj in placement.get("objects", [])}
    source_id = by_role.get("manipuland_candidate") or by_role.get("scene_object")
    target_id = by_role.get("support_or_target_candidate") or by_role.get("container_candidate")
    if not source_id or not target_id:
        raise ValueError("Could not infer source and target object ids from placement roles")
    return source_id, target_id


def object_by_id(placement: dict[str, Any], object_id: str) -> dict[str, Any]:
    for obj in placement.get("objects", []):
        if obj.get("id") == object_id:
            return obj
    raise KeyError(f"Placement has no object {object_id!r}")


def region_xy_bounds(placement: dict[str, Any], obj: dict[str, Any], margin: float) -> tuple[tuple[float, float], tuple[float, float]]:
    region_name = obj.get("pose", {}).get("region")
    regions = placement.get("workspace", {}).get("spatial_regions", {})
    region = regions.get(region_name)
    if not isinstance(region, dict) or "x" not in region or "y" not in region:
        raise ValueError(f"Object {obj.get('id')} has no usable spatial region {region_name!r}")
    x_bounds = (float(region["x"][0]) + margin, float(region["x"][1]) - margin)
    y_bounds = (float(region["y"][0]) + margin, float(region["y"][1]) - margin)
    if x_bounds[0] >= x_bounds[1] or y_bounds[0] >= y_bounds[1]:
        raise ValueError(f"Region {region_name!r} is smaller than the requested margin {margin}")
    return x_bounds, y_bounds


def clipped_sample(rng: random.Random, center: float, radius: float, bounds: tuple[float, float]) -> float:
    low = max(bounds[0], center - radius)
    high = min(bounds[1], center + radius)
    if low >= high:
        raise ValueError(f"No sampling interval remains around {center} with radius {radius} inside {bounds}")
    return rng.uniform(low, high)


def pose_vector(placement: dict[str, Any], source_id: str, target_id: str) -> list[float]:
    source_xyz = object_by_id(placement, source_id)["pose"]["xyz"]
    target_xyz = object_by_id(placement, target_id)["pose"]["xyz"]
    return [float(source_xyz[0]), float(source_xyz[1]), float(target_xyz[0]), float(target_xyz[1])]


def pose_signature(placement: dict[str, Any], source_id: str, target_id: str) -> str:
    payload = {
        object_id: {
            "xyz": object_by_id(placement, object_id)["pose"]["xyz"],
            "qpos": object_by_id(placement, object_id)["pose"].get("qpos"),
        }
        for object_id in (source_id, target_id)
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def vector_distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second, strict=True)))


def xy_distance(placement: dict[str, Any], source_id: str, target_id: str) -> float:
    vector = pose_vector(placement, source_id, target_id)
    return math.hypot(vector[0] - vector[2], vector[1] - vector[3])


def sample_placement(
    base: dict[str, Any],
    rng: random.Random,
    source_id: str,
    target_id: str,
    source_jitter: tuple[float, float],
    target_jitter: tuple[float, float],
    region_margin: float,
) -> dict[str, Any]:
    result = copy.deepcopy(base)
    base_source = object_by_id(base, source_id)
    base_target = object_by_id(base, target_id)
    source = object_by_id(result, source_id)
    target = object_by_id(result, target_id)
    source_x, source_y = region_xy_bounds(base, base_source, region_margin)
    target_x, target_y = region_xy_bounds(base, base_target, region_margin)
    source["pose"]["xyz"][0] = clipped_sample(rng, float(base_source["pose"]["xyz"][0]), source_jitter[0], source_x)
    source["pose"]["xyz"][1] = clipped_sample(rng, float(base_source["pose"]["xyz"][1]), source_jitter[1], source_y)
    target["pose"]["xyz"][0] = clipped_sample(rng, float(base_target["pose"]["xyz"][0]), target_jitter[0], target_x)
    target["pose"]["xyz"][1] = clipped_sample(rng, float(base_target["pose"]["xyz"][1]), target_jitter[1], target_y)
    return result


def build_split(
    source_path: Path,
    out_dir: Path,
    task_id: str,
    train_count: int,
    eval_count: int,
    seed: int,
    train_seed_start: int,
    eval_seed_start: int,
    source_jitter: tuple[float, float],
    target_jitter: tuple[float, float],
    region_margin: float,
    min_object_distance: float,
    min_pose_distance: float,
    min_eval_train_distance: float,
) -> dict[str, Any]:
    if train_count < 2 or eval_count < 1:
        raise ValueError("train_count must be >= 2 and eval_count must be >= 1")
    source_path = source_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    base = read_json(source_path)
    source_id, target_id = infer_source_target_ids(base)
    rng = random.Random(seed)
    accepted_vectors: list[list[float]] = []
    train_vectors: list[list[float]] = []
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}

    def accept_candidate(candidate: dict[str, Any], split: str) -> bool:
        vector = pose_vector(candidate, source_id, target_id)
        if xy_distance(candidate, source_id, target_id) < min_object_distance:
            return False
        if any(vector_distance(vector, previous) < min_pose_distance for previous in accepted_vectors):
            return False
        if split == "eval" and any(vector_distance(vector, previous) < min_eval_train_distance for previous in train_vectors):
            return False
        return True

    def emit(candidate: dict[str, Any], split: str, index: int, episode_seed: int) -> None:
        placement_id = f"{split}_{index:03d}"
        candidate["placement_name"] = f"{base.get('placement_name', task_id)}_{placement_id}"
        candidate["stage"] = f"robustness_{split}_placement"
        candidate["robustness_variation"] = {
            "schema_version": "alchedata.placement_variation.v0",
            "placement_id": placement_id,
            "split": split,
            "episode_seed": episode_seed,
            "source_id": source_id,
            "target_id": target_id,
            "pose_vector": pose_vector(candidate, source_id, target_id),
            "base_pose_vector": pose_vector(base, source_id, target_id),
            "object_xy_distance_m": xy_distance(candidate, source_id, target_id),
            "declared_randomization": {
                "object_placement": True,
                "lighting": False,
                "background": False,
                "camera_pose": False,
                "table_height": False,
            },
        }
        signature = pose_signature(candidate, source_id, target_id)
        relative_path = Path("placements") / split / f"{placement_id}.json"
        write_json(out_dir / relative_path, candidate)
        vector = pose_vector(candidate, source_id, target_id)
        accepted_vectors.append(vector)
        if split == "train":
            train_vectors.append(vector)
        splits[split].append(
            {
                "placement_id": placement_id,
                "placement": str(relative_path),
                "episode_seed": episode_seed,
                "pose_signature": signature,
                "pose_vector": vector,
                "object_xy_distance_m": xy_distance(candidate, source_id, target_id),
            }
        )

    base_candidate = copy.deepcopy(base)
    if not accept_candidate(base_candidate, "train"):
        raise ValueError("Base placement violates requested robustness split constraints")
    emit(base_candidate, "train", 0, train_seed_start)

    for split, count, seed_start in (
        ("train", train_count, train_seed_start),
        ("eval", eval_count, eval_seed_start),
    ):
        start_index = 1 if split == "train" else 0
        attempts = 0
        while len(splits[split]) < count:
            attempts += 1
            if attempts > 10000:
                raise RuntimeError(f"Could not sample {count} valid {split} placements after {attempts} attempts")
            candidate = sample_placement(
                base,
                rng,
                source_id,
                target_id,
                source_jitter,
                target_jitter,
                region_margin,
            )
            if not accept_candidate(candidate, split):
                continue
            index = start_index + len(splits[split]) - (1 if split == "train" else 0)
            emit(candidate, split, index, seed_start + len(splits[split]))

    signatures = [entry["pose_signature"] for entries in splits.values() for entry in entries]
    eval_to_train = [
        min(vector_distance(entry["pose_vector"], train) for train in train_vectors)
        for entry in splits["eval"]
    ]
    manifest = {
        "schema_version": "alchedata.placement_robustness_split.v0",
        "status": "pass_placement_robustness_split",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "source_placement": str(source_path),
        "source_id": source_id,
        "target_id": target_id,
        "sampling": {
            "seed": seed,
            "source_jitter_xy_m": list(source_jitter),
            "target_jitter_xy_m": list(target_jitter),
            "region_margin_m": region_margin,
            "minimum_initial_object_xy_distance_m": min_object_distance,
            "minimum_any_pose_vector_distance_m": min_pose_distance,
            "minimum_eval_to_train_pose_vector_distance_m": min_eval_train_distance,
            "base_placement_included_in_train": True,
        },
        "splits": splits,
        "validation": {
            "train_count": len(splits["train"]),
            "eval_count": len(splits["eval"]),
            "unique_pose_signature_count": len(set(signatures)),
            "train_eval_signature_overlap": sorted(
                {entry["pose_signature"] for entry in splits["train"]}
                & {entry["pose_signature"] for entry in splits["eval"]}
            ),
            "minimum_observed_eval_to_train_pose_vector_distance_m": min(eval_to_train),
            "all_initial_object_distances_pass": all(
                entry["object_xy_distance_m"] >= min_object_distance
                for entries in splits.values()
                for entry in entries
            ),
        },
        "claim_boundary": (
            "This manifest proves deterministic, explicit, disjoint object-placement splits inside declared placement regions. "
            "It does not prove RoboTwin planner success, learned-policy robustness, or visual/physics domain randomization."
        ),
    }
    if manifest["validation"]["unique_pose_signature_count"] != train_count + eval_count:
        raise AssertionError("Generated placement signatures are not unique")
    if manifest["validation"]["train_eval_signature_overlap"]:
        raise AssertionError("Train and eval placement signatures overlap")
    write_json(out_dir / "placement_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic held-out placement splits.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--task-id", default="task_apple_plate")
    parser.add_argument("--train-count", type=int, default=8)
    parser.add_argument("--eval-count", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument("--train-seed-start", type=int, default=100)
    parser.add_argument("--eval-seed-start", type=int, default=200)
    parser.add_argument("--source-jitter-x", type=float, default=0.06)
    parser.add_argument("--source-jitter-y", type=float, default=0.05)
    parser.add_argument("--target-jitter-x", type=float, default=0.04)
    parser.add_argument("--target-jitter-y", type=float, default=0.05)
    parser.add_argument("--region-margin", type=float, default=0.015)
    parser.add_argument("--min-object-distance", type=float, default=0.12)
    parser.add_argument("--min-pose-distance", type=float, default=0.018)
    parser.add_argument("--min-eval-train-distance", type=float, default=0.035)
    args = parser.parse_args()

    manifest = build_split(
        Path(args.source),
        Path(args.out_dir),
        args.task_id,
        args.train_count,
        args.eval_count,
        args.seed,
        args.train_seed_start,
        args.eval_seed_start,
        (args.source_jitter_x, args.source_jitter_y),
        (args.target_jitter_x, args.target_jitter_y),
        args.region_margin,
        args.min_object_distance,
        args.min_pose_distance,
        args.min_eval_train_distance,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "train_count": manifest["validation"]["train_count"],
                "eval_count": manifest["validation"]["eval_count"],
                "unique_pose_signature_count": manifest["validation"]["unique_pose_signature_count"],
                "manifest": str(Path(args.out_dir).expanduser().resolve() / "placement_manifest.json"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
