from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_placement_robustness_splits import build_split
from scripts.placement_manifest_utils import load_placement_cases


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runs" / "probe_static_apple_plate_action_repair" / "final_placement.json"


class PlacementRobustnessSplitTest(unittest.TestCase):
    def build(self, out_dir: Path) -> dict:
        return build_split(
            source_path=SOURCE,
            out_dir=out_dir,
            task_id="task_apple_plate",
            train_count=4,
            eval_count=2,
            seed=17,
            train_seed_start=100,
            eval_seed_start=200,
            source_jitter=(0.06, 0.05),
            target_jitter=(0.04, 0.05),
            region_margin=0.015,
            min_object_distance=0.12,
            min_pose_distance=0.018,
            min_eval_train_distance=0.035,
        )

    def test_split_is_unique_disjoint_and_resolvable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            manifest = self.build(out_dir)
            self.assertEqual(manifest["status"], "pass_placement_robustness_split")
            self.assertEqual(manifest["validation"]["unique_pose_signature_count"], 6)
            self.assertEqual(manifest["validation"]["train_eval_signature_overlap"], [])
            self.assertGreaterEqual(
                manifest["validation"]["minimum_observed_eval_to_train_pose_vector_distance_m"],
                0.035,
            )
            _, train_cases = load_placement_cases(out_dir / "placement_manifest.json", "train")
            _, eval_cases = load_placement_cases(out_dir / "placement_manifest.json", "eval")
            self.assertEqual([case["seed"] for case in train_cases], [100, 101, 102, 103])
            self.assertEqual([case["seed"] for case in eval_cases], [200, 201])
            self.assertTrue(all(case["placement_path"].is_file() for case in train_cases + eval_cases))

    def test_same_seed_produces_same_pose_entries(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_manifest = self.build(Path(first))
            second_manifest = self.build(Path(second))
            self.assertEqual(first_manifest["splits"], second_manifest["splits"])


if __name__ == "__main__":
    unittest.main()
