#!/usr/bin/env python3
"""Filter candidate placement splits through scripted RoboTwin feasibility evidence."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def vector_distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second, strict=True)))


def select_diverse(entries: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count == 0:
        return []
    if len(entries) < count:
        raise ValueError(f"Need {count} verified placements but only {len(entries)} passed")
    selected = [entries[0]]
    remaining = entries[1:]
    while len(selected) < count:
        best = max(
            enumerate(remaining),
            key=lambda item: (
                min(vector_distance(item[1]["pose_vector"], chosen["pose_vector"]) for chosen in selected),
                -item[0],
            ),
        )
        selected.append(best[1])
        remaining.pop(best[0])
    selected_ids = {entry["placement_id"] for entry in selected}
    return [entry for entry in entries if entry["placement_id"] in selected_ids]


def build_verified_split(
    candidate_manifest_path: Path,
    collection_report_paths: list[Path],
    out_dir: Path,
    train_count: int,
    eval_count: int,
) -> dict[str, Any]:
    candidate_manifest_path = candidate_manifest_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    candidate = read_json(candidate_manifest_path)
    candidate_entries = {
        (split, entry["placement_id"]): entry
        for split, entries in candidate.get("splits", {}).items()
        for entry in entries
    }
    passed: dict[str, dict[str, dict[str, Any]]] = {"train": {}, "eval": {}}
    observed: list[dict[str, Any]] = []

    for report_path in collection_report_paths:
        report_path = report_path.expanduser().resolve()
        report = read_json(report_path)
        for episode in report.get("episodes", []):
            split = str(episode.get("placement_split", report.get("placement_split", "")))
            placement_id = str(episode.get("placement_id", ""))
            key = (split, placement_id)
            candidate_entry = candidate_entries.get(key)
            if candidate_entry is None:
                raise ValueError(f"Collection episode {key} is absent from candidate manifest")
            signature_matches = episode.get("pose_signature") == candidate_entry.get("pose_signature")
            success = (
                episode.get("status") == "pass_generated_action_rollout"
                and episode.get("check_success") is True
                and signature_matches
            )
            observed.append(
                {
                    "collection_report": str(report_path),
                    "split": split,
                    "placement_id": placement_id,
                    "pose_signature": episode.get("pose_signature"),
                    "signature_matches": signature_matches,
                    "status": episode.get("status"),
                    "check_success": episode.get("check_success"),
                    "scripted_feasible": success,
                }
            )
            if success:
                passed[split][placement_id] = candidate_entry

    selected = {
        "train": select_diverse(
            [entry for entry in candidate["splits"]["train"] if entry["placement_id"] in passed["train"]],
            train_count,
        ),
        "eval": select_diverse(
            [entry for entry in candidate["splits"]["eval"] if entry["placement_id"] in passed["eval"]],
            eval_count,
        ),
    }

    output_splits: dict[str, list[dict[str, Any]]] = {"train": [], "eval": []}
    for split, entries in selected.items():
        for new_index, entry in enumerate(entries):
            source_path = Path(entry["placement"])
            if not source_path.is_absolute():
                source_path = candidate_manifest_path.parent / source_path
            relative_path = Path("placements") / split / f"{split}_{new_index:03d}.json"
            destination = out_dir / relative_path
            placement = read_json(source_path)
            variation = placement.setdefault("robustness_variation", {})
            variation["candidate_placement_id"] = entry["placement_id"]
            variation["placement_id"] = f"{split}_{new_index:03d}"
            variation["split"] = split
            variation["episode_seed"] = entry["episode_seed"]
            write_json(destination, placement)
            output_entry = dict(entry)
            output_entry["candidate_placement_id"] = entry["placement_id"]
            output_entry["placement_id"] = f"{split}_{new_index:03d}"
            output_entry["placement"] = str(relative_path)
            output_splits[split].append(output_entry)

    train_vectors = [entry["pose_vector"] for entry in output_splits["train"]]
    eval_vectors = [entry["pose_vector"] for entry in output_splits["eval"]]
    train_signatures = {entry["pose_signature"] for entry in output_splits["train"]}
    eval_signatures = {entry["pose_signature"] for entry in output_splits["eval"]}
    manifest = {
        "schema_version": "alchedata.scripted_verified_placement_split.v0",
        "status": "pass_scripted_verified_placement_split",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_id": candidate.get("task_id"),
        "source_id": candidate.get("source_id"),
        "target_id": candidate.get("target_id"),
        "source_candidate_manifest": str(candidate_manifest_path),
        "sampling": candidate.get("sampling"),
        "splits": output_splits,
        "scripted_verification": {
            "collection_reports": [str(path.expanduser().resolve()) for path in collection_report_paths],
            "candidate_train_pass_count": len(passed["train"]),
            "candidate_eval_pass_count": len(passed["eval"]),
            "observations": observed,
            "selection_method": "greedy_farthest_point_with_manifest_order_tiebreak",
        },
        "validation": {
            "train_count": len(output_splits["train"]),
            "eval_count": len(output_splits["eval"]),
            "unique_pose_signature_count": len(train_signatures | eval_signatures),
            "train_eval_signature_overlap": sorted(train_signatures & eval_signatures),
            "minimum_train_pairwise_pose_vector_distance_m": (
                min(
                    vector_distance(train_vectors[left], train_vectors[right])
                    for left in range(len(train_vectors))
                    for right in range(left + 1, len(train_vectors))
                )
                if len(train_vectors) >= 2
                else None
            ),
            "minimum_eval_to_train_pose_vector_distance_m": (
                min(
                    vector_distance(eval_vector, train_vector)
                    for eval_vector in eval_vectors
                    for train_vector in train_vectors
                )
                if eval_vectors and train_vectors
                else None
            ),
        },
        "claim_boundary": (
            "All selected placements passed the generated scripted RoboTwin task verifier before use. "
            "When both splits are emitted, the eval split is signature-disjoint from the emitted train split. "
            "An eval-only manifest must still be checked against the actual training collection signatures. "
            "Scripted feasibility selection does not constitute learned-policy evaluation or visual/physics domain robustness."
        ),
    }
    if manifest["validation"]["train_eval_signature_overlap"]:
        raise AssertionError("Verified train and eval placement signatures overlap")
    if manifest["validation"]["unique_pose_signature_count"] != train_count + eval_count:
        raise AssertionError("Verified placement signatures are not unique")
    write_json(out_dir / "placement_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a scripted-verified placement split.")
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--collection-report", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--train-count", type=int, default=8)
    parser.add_argument("--eval-count", type=int, default=4)
    args = parser.parse_args()
    manifest = build_verified_split(
        Path(args.candidate_manifest),
        [Path(value) for value in args.collection_report],
        Path(args.out_dir),
        args.train_count,
        args.eval_count,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "train_count": manifest["validation"]["train_count"],
                "eval_count": manifest["validation"]["eval_count"],
                "candidate_train_pass_count": manifest["scripted_verification"]["candidate_train_pass_count"],
                "candidate_eval_pass_count": manifest["scripted_verification"]["candidate_eval_pass_count"],
                "manifest": str(Path(args.out_dir).expanduser().resolve() / "placement_manifest.json"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
