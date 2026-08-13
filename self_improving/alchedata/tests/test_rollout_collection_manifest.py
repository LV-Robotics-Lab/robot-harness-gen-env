from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.build_placement_robustness_splits import build_split


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs" / "probe_static_apple_plate_action_repair" / "final_placement.json"
COLLECTOR = ROOT / "scripts" / "run_generated_rollout_collection.py"


class RolloutCollectionManifestTest(unittest.TestCase):
    def test_collection_routes_each_manifest_placement_to_its_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            split_dir = root / "split"
            build_split(
                source_path=SOURCE,
                out_dir=split_dir,
                task_id="task_apple_plate",
                train_count=3,
                eval_count=1,
                seed=23,
                train_seed_start=100,
                eval_seed_start=200,
                source_jitter=(0.06, 0.05),
                target_jitter=(0.04, 0.05),
                region_margin=0.015,
                min_object_distance=0.12,
                min_pose_distance=0.018,
                min_eval_train_distance=0.035,
            )
            runner = root / "fake_runner.py"
            runner.write_text(
                textwrap.dedent(
                    """
                    import argparse
                    import json
                    from pathlib import Path

                    parser = argparse.ArgumentParser()
                    parser.add_argument("--placement", required=True)
                    parser.add_argument("--out-dir", required=True)
                    parser.add_argument("--seed", required=True, type=int)
                    args, _ = parser.parse_known_args()
                    out = Path(args.out_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    report = {
                        "status": "pass_generated_action_rollout",
                        "check_success": True,
                        "plan_success": True,
                        "placement": args.placement,
                        "seed": args.seed,
                        "native_synchronized_data": {"status": "not_requested"},
                    }
                    (out / "rollout_report.json").write_text(json.dumps(report), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            out_dir = root / "collection"
            result = subprocess.run(
                [
                    sys.executable,
                    str(COLLECTOR),
                    "--robotwin-root",
                    str(root / "robotwin-unused"),
                    "--placement-manifest",
                    str(split_dir / "placement_manifest.json"),
                    "--placement-split",
                    "train",
                    "--placement-id",
                    "train_002",
                    "--placement-id",
                    "train_000",
                    "--seed-list",
                    "901,902",
                    "--task-id",
                    "task_apple_plate",
                    "--out-dir",
                    str(out_dir),
                    "--runner",
                    str(runner),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((out_dir / "collection_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass_generated_rollout_collection")
            self.assertEqual(report["unique_pose_signature_count"], 2)
            self.assertEqual([episode["placement_id"] for episode in report["episodes"]], ["train_002", "train_000"])
            self.assertEqual([episode["seed"] for episode in report["episodes"]], [901, 902])
            self.assertNotEqual(report["episodes"][0]["placement"], report["episodes"][1]["placement"])


if __name__ == "__main__":
    unittest.main()
