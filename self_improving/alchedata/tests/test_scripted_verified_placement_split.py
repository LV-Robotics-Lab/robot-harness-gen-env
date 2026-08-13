from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_placement_robustness_splits import build_split
from scripts.build_scripted_verified_placement_split import build_verified_split


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs" / "probe_static_apple_plate_action_repair" / "final_placement.json"


class ScriptedVerifiedPlacementSplitTest(unittest.TestCase):
    def test_filters_failures_and_keeps_disjoint_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir = root / "candidate"
            candidate = build_split(
                source_path=SOURCE,
                out_dir=candidate_dir,
                task_id="task_apple_plate",
                train_count=4,
                eval_count=2,
                seed=29,
                train_seed_start=100,
                eval_seed_start=200,
                source_jitter=(0.06, 0.05),
                target_jitter=(0.04, 0.05),
                region_margin=0.015,
                min_object_distance=0.12,
                min_pose_distance=0.018,
                min_eval_train_distance=0.035,
            )

            reports = []
            for split in ("train", "eval"):
                episodes = []
                for index, entry in enumerate(candidate["splits"][split]):
                    success = split == "eval" or index < 3
                    episodes.append(
                        {
                            "placement_id": entry["placement_id"],
                            "placement_split": split,
                            "pose_signature": entry["pose_signature"],
                            "status": "pass_generated_action_rollout" if success else "fail_generated_action_rollout",
                            "check_success": success,
                        }
                    )
                report_path = root / f"{split}_report.json"
                report_path.write_text(json.dumps({"placement_split": split, "episodes": episodes}), encoding="utf-8")
                reports.append(report_path)

            verified = build_verified_split(
                candidate_dir / "placement_manifest.json",
                reports,
                root / "verified",
                train_count=3,
                eval_count=2,
            )
            self.assertEqual(verified["status"], "pass_scripted_verified_placement_split")
            self.assertEqual(verified["validation"]["train_count"], 3)
            self.assertEqual(verified["validation"]["eval_count"], 2)
            self.assertEqual(verified["validation"]["unique_pose_signature_count"], 5)
            self.assertEqual(verified["validation"]["train_eval_signature_overlap"], [])
            self.assertTrue(
                all(
                    (root / "verified" / entry["placement"]).is_file()
                    for entries in verified["splits"].values()
                    for entry in entries
                )
            )
            first_train = verified["splits"]["train"][0]
            placement = json.loads(
                (root / "verified" / first_train["placement"]).read_text(encoding="utf-8")
            )
            self.assertEqual(placement["robustness_variation"]["placement_id"], "train_000")
            self.assertEqual(
                placement["robustness_variation"]["candidate_placement_id"],
                first_train["candidate_placement_id"],
            )

            eval_only = build_verified_split(
                candidate_dir / "placement_manifest.json",
                [reports[1]],
                root / "verified_eval_only",
                train_count=0,
                eval_count=2,
            )
            self.assertEqual(eval_only["splits"]["train"], [])
            self.assertEqual(eval_only["validation"]["train_count"], 0)
            self.assertEqual(eval_only["validation"]["eval_count"], 2)
            self.assertIsNone(eval_only["validation"]["minimum_train_pairwise_pose_vector_distance_m"])
            self.assertIsNone(eval_only["validation"]["minimum_eval_to_train_pose_vector_distance_m"])


if __name__ == "__main__":
    unittest.main()
