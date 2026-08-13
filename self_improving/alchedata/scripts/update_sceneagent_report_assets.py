#!/usr/bin/env python3
"""Bundle the current bounded SceneAgent policy-promotion evidence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from report_delivery import BUNDLES, write_manifest


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "sceneagent_selection2env"
EVIDENCE_PATHS = (
    Path("artifacts/sceneagent_policy_promotion"),
    Path("runs/pose_conditioned_train_apple_plate_v1"),
    Path("runs/pose_conditioned_eval_apple_plate_heldout_v1"),
    Path("runs/pose_conditioned_eval_apple_plate_domain_randomized_v2"),
    Path("runs/pose_conditioned_train_can_basket_v1"),
    Path("runs/pose_conditioned_eval_can_basket_heldout_seeds_v1"),
    Path("runs/act_eval_apple_plate_recovery_new_holdout_chunk175_v4"),
)
CONTINUOUS_VIDEOS = {
    Path("reports/openxsim_command_loop/assets/benchmark_videos/open_laptop.mp4"): "open_laptop.mp4",
    Path("reports/openxsim_command_loop/assets/benchmark_videos/place_mouse_pad.mp4"): "place_mouse_pad.mp4",
    Path("reports/openxsim_command_loop/assets/benchmark_videos/place_container_plate.mp4"): "place_container_plate.mp4",
}


def bundle() -> dict[str, object]:
    copied = []
    for relative in EVIDENCE_PATHS:
        source = ROOT / relative
        if not source.is_dir():
            raise FileNotFoundError(source)
        destination = REPORT / "assets" / "evidence" / relative
        if destination.is_dir():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        copied.append(relative.as_posix())
    continuous_destination = REPORT / "assets/evidence/runs/official_rollout_continuous"
    if continuous_destination.is_dir():
        shutil.rmtree(continuous_destination)
    continuous_destination.mkdir(parents=True)
    for source, name in CONTINUOUS_VIDEOS.items():
        full_source = ROOT / source
        if not full_source.is_file():
            raise FileNotFoundError(full_source)
        shutil.copy2(full_source, continuous_destination / name)
        copied.append(source.as_posix())
    manifest = write_manifest(REPORT, BUNDLES["sceneagent"]["schema_version"])
    return {
        "status": "pass_sceneagent_policy_evidence_bundle",
        "copied_roots": copied,
        "report_file_count": manifest["file_count"],
    }


def main() -> int:
    print(json.dumps(bundle(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
