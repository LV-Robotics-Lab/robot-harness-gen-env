#!/usr/bin/env python3
"""Train a bounded pose-conditioned trajectory checkpoint from RoboTwin HDF5 demonstrations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pose_conditioned_trajectory_policy import load_demonstrations, train_checkpoint


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--ridge", type=float, default=1e-5)
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    demonstrations = load_demonstrations(dataset_dir, ROOT)
    metadata = train_checkpoint(
        demonstrations,
        out_dir / "pose_conditioned_policy.npz",
        out_dir / "training_report.json",
        ridge=args.ridge,
    )
    print(
        json.dumps(
            {
                "status": metadata["status"],
                "demonstration_count": metadata["demonstration_count"],
                "unique_placement_count": metadata["unique_placement_count"],
                "canonical_action_count": metadata["canonical_action_count"],
                "checkpoint": metadata["checkpoint"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
