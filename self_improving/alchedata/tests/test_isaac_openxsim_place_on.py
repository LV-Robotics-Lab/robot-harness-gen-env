import json
from pathlib import Path

from scripts.run_isaac_openxsim_place_on import build_transfer_record, relation_metrics


def test_relation_metrics_require_support_alignment_and_low_speed() -> None:
    verifier = {
        "horizontal_center_distance_max_m": 0.12,
        "source_bottom_to_target_top_abs_max_m": 0.04,
        "source_speed_max_mps": 0.08,
    }

    passed = relation_metrics([0.1, 0.0, 0.18], [0.1, 0.0, 0.04], [0.2, 0.2, 0.2], [0.65, 0.65, 0.08], 0.01, verifier)
    failed = relation_metrics([0.4, 0.0, 0.18], [0.1, 0.0, 0.04], [0.2, 0.2, 0.2], [0.65, 0.65, 0.08], 0.01, verifier)

    assert passed["success"] is True
    assert all(passed["checks"].values())
    assert failed["success"] is False
    assert failed["checks"]["horizontal_center_distance"] is False


def test_transfer_record_declares_non_transferred_surfaces(tmp_path: Path) -> None:
    contract = {
        "task_id": "openxsim_place_container_plate_v1",
        "source_backend": {"adapter": "RoboTwin/SAPIEN", "execution_type": "official_scripted_robot_expert"},
        "target_backend": {"adapter": "Isaac Sim 5.1", "execution_type": "scripted_object_space_expert"},
    }
    contract_path = tmp_path / "contract.json"
    source_path = tmp_path / "source.json"
    target_path = tmp_path / "target.json"
    for path in (contract_path, source_path, target_path):
        path.write_text(json.dumps({"ready": True}), encoding="utf-8")

    transfer = build_transfer_record(contract_path, contract, source_path, target_path, True)

    assert transfer["status"] == "pass_task_semantic_transfer_with_declared_losses"
    assert transfer["same_normalized_task_contract"] is True
    fidelity = {row["field"]: row["fidelity"] for row in transfer["mappings"]}
    assert fidelity["task relation"] == "exact_relation"
    assert fidelity["action interface"] == "not_transferred"
    assert fidelity["robot embodiment"] == "not_transferred"
    assert transfer["target_backend"]["target_verifier_success"] is True
