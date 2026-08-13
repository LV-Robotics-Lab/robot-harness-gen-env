#!/usr/bin/env python3
"""Resolve explicit per-episode placement manifests for collection and evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_placement_cases(
    manifest_path: Path,
    split: str,
    seed_overrides: list[int] | None = None,
    placement_ids: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = manifest_path.expanduser().resolve()
    manifest = read_json(manifest_path)
    entries = manifest.get("splits", {}).get(split)
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Placement manifest has no non-empty split {split!r}: {manifest_path}")
    if placement_ids:
        if len(placement_ids) != len(set(placement_ids)):
            raise ValueError("Placement ID filter contains duplicates")
        entries_by_id = {str(entry.get("placement_id")): entry for entry in entries}
        missing = [placement_id for placement_id in placement_ids if placement_id not in entries_by_id]
        if missing:
            raise ValueError(f"Placement IDs are absent from split {split!r}: {missing}")
        entries = [entries_by_id[placement_id] for placement_id in placement_ids]
    if seed_overrides is not None and len(seed_overrides) != len(entries):
        raise ValueError(
            f"Seed override count {len(seed_overrides)} does not match placement count {len(entries)} for split {split!r}"
        )

    cases: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        placement_value = entry.get("placement")
        if not placement_value:
            raise ValueError(f"Placement manifest entry {split}[{index}] has no placement path")
        placement_path = Path(placement_value).expanduser()
        if not placement_path.is_absolute():
            placement_path = manifest_path.parent / placement_path
        placement_path = placement_path.resolve()
        if not placement_path.is_file():
            raise FileNotFoundError(f"Placement file does not exist: {placement_path}")
        episode_seed = (
            seed_overrides[index]
            if seed_overrides is not None
            else int(entry.get("episode_seed", index))
        )
        cases.append(
            {
                "placement_id": str(entry.get("placement_id", f"{split}_{index:03d}")),
                "candidate_placement_id": entry.get("candidate_placement_id"),
                "placement_path": placement_path,
                "pose_signature": entry.get("pose_signature"),
                "pose_vector": entry.get("pose_vector"),
                "split": split,
                "seed": episode_seed,
            }
        )
    return manifest, cases


def fixed_placement_cases(placement_path: Path, seeds: list[int]) -> list[dict[str, Any]]:
    placement_path = placement_path.expanduser().resolve()
    if not placement_path.is_file():
        raise FileNotFoundError(f"Placement file does not exist: {placement_path}")
    return [
        {
            "placement_id": "fixed",
            "placement_path": placement_path,
            "pose_signature": None,
            "pose_vector": None,
            "split": "fixed",
            "seed": seed,
        }
        for seed in seeds
    ]


def passed_training_placement_signatures(report_paths: list[Path]) -> set[str]:
    signatures: set[str] = set()
    for report_path in report_paths:
        if not report_path.exists():
            continue
        report = read_json(report_path)
        for episode in report.get("episodes", []):
            source_passed = str(episode.get("status", "")).startswith("pass_") and episode.get("check_success") is True
            native = episode.get("native_synchronized_data", {})
            native_passed = not native or native.get("status") == "pass_native_synchronized_recording"
            signature = episode.get("pose_signature")
            if source_passed and native_passed and signature:
                signatures.add(str(signature))
    return signatures
