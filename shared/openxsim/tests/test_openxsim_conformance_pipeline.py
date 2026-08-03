from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from agenticsim.openxsim.backends import compile_package
from agenticsim.openxsim.conformance import FAIL, NOT_EVALUATED, PASS, evaluate_conformance
from agenticsim.openxsim.importers import import_mjcf
from agenticsim.openxsim.pipeline import OpenXSimPipeline
from agenticsim.openxsim.text2env import compile_text


def make_package(tmp_path: Path):
    return compile_text("Move the red block onto the blue zone.", repo_root=tmp_path)


def runtime_evidence(package, offset: float = 0.0, *, contact: str = "red_block:blue_zone") -> dict:
    contract_hash = hashlib.sha256(
        json.dumps(package.task.semantic_contract(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": "agenticsim.runtime_evidence.v1",
        "backend": "test",
        "reset_ok": True,
        "step_ok": True,
        "action_interface_bound": True,
        "success_evaluator_bound": True,
        "task_contract_hash": contract_hash,
        "observation_keys": ["contact", "object_pose", "robot_joint_position"],
        "trajectory": [
            {"step": 0, "objects": {"red_block": [-0.2 + offset, 0.0, 0.766]}, "contacts": []},
            {"step": 1, "objects": {"red_block": [0.05 + offset, -0.1, 0.766]}, "contacts": [contact]},
        ],
    }


def test_static_conformance_stops_at_l1_without_runtime_evidence(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]
    report = evaluate_conformance(package, result, source_backend="sapien")

    assert [check.status for check in report.checks] == [PASS, PASS, NOT_EVALUATED, NOT_EVALUATED, NOT_EVALUATED]
    assert report.highest_consecutive_level == "L1"


def test_runtime_and_trajectory_evidence_reaches_l3(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]
    source_runtime = runtime_evidence(package)
    target_runtime = runtime_evidence(package, offset=0.004)
    report = evaluate_conformance(
        package,
        result,
        source_backend="sapien",
        source_runtime=source_runtime,
        target_runtime=target_runtime,
        state_tolerance_m=0.01,
    )

    assert [check.status for check in report.checks[:4]] == [PASS, PASS, PASS, PASS]
    assert report.checks[4].status == NOT_EVALUATED
    assert report.highest_consecutive_level == "L3"
    assert report.checks[3].metrics["max_state_error_m"] > 0.0


def test_trajectory_contact_mismatch_fails_l3(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]
    report = evaluate_conformance(
        package,
        result,
        source_backend="sapien",
        source_runtime=runtime_evidence(package),
        target_runtime=runtime_evidence(package, contact="red_block:ground"),
    )

    assert report.checks[2].status == PASS
    assert report.checks[3].status == FAIL
    assert "contact sets differ" in report.checks[3].details
    assert report.highest_consecutive_level == "L2"


def test_runtime_evidence_cannot_omit_declared_observation_keys(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]
    incomplete = runtime_evidence(package)
    incomplete["observation_keys"] = ["object_pose", "robot_joint_position"]
    report = evaluate_conformance(
        package,
        result,
        source_backend="sapien",
        source_runtime=incomplete,
        target_runtime=incomplete,
    )

    assert report.checks[2].status == FAIL
    assert report.highest_consecutive_level == "L1"
    assert report.checks[2].metrics["missing_observation_keys"] == {
        "source": ["contact"],
        "target": ["contact"],
    }


def test_policy_evidence_can_reach_l4_only_with_enough_episodes(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]
    report = evaluate_conformance(
        package,
        result,
        source_backend="sapien",
        source_runtime=runtime_evidence(package),
        target_runtime=runtime_evidence(package, offset=0.001),
        source_policy={"episodes": 50, "success_rate": 0.82},
        target_policy={"episodes": 50, "success_rate": 0.76},
    )

    assert report.checks[4].status == PASS
    assert report.highest_consecutive_level == "L4"


def test_native_mjcf_without_embedded_task_is_marked_unbound(tmp_path: Path) -> None:
    path = tmp_path / "existing.xml"
    path.write_text(
        """<mujoco model="existing_scene">
  <worldbody>
    <body name="cube" pos="0 0 0.2"><freejoint/><geom type="box" size="0.05 0.05 0.05"/></body>
  </worldbody>
</mujoco>
""",
        encoding="utf-8",
    )
    package = import_mjcf(path)

    assert package.source["limitations"] == ["task_semantics_unbound"]
    assert package.task.success[0]["requires_binding"] is True
    assert package.env.objects[0].instance_id == "cube"


def test_openxsim_transfer_imports_existing_mjcf_and_compiles_three_targets(tmp_path: Path) -> None:
    source_package = make_package(tmp_path)
    source_result = compile_package(source_package, tmp_path / "source", ("mujoco",), strict=True)["mujoco"]
    pipeline = OpenXSimPipeline(tmp_path / "runs")
    imported, results, reports = pipeline.transfer(
        source_result.artifact_path,
        source_backend="mujoco",
        target_backends=("isaacsim", "sapien", "metasim"),
        strict=True,
    )

    assert imported.source["backend"] == "mujoco"
    assert set(results) == {"isaacsim", "sapien", "metasim"}
    assert all(result.status == "compiled" for result in results.values())
    assert all(report.highest_consecutive_level == "L1" for report in reports.values())
    assert all(report.checks[2].status == NOT_EVALUATED for report in reports.values())


def test_openxsim_transfer_imports_native_sapien_scene_and_compiles_three_targets(tmp_path: Path) -> None:
    source_package = make_package(tmp_path)
    source_result = compile_package(source_package, tmp_path / "source", ("sapien",), strict=True)["sapien"]
    native = tmp_path / "native_sapien/scene.json"
    native.parent.mkdir()
    shutil.copy2(source_result.artifact_path, native)
    pipeline = OpenXSimPipeline(tmp_path / "runs")

    imported, results, reports = pipeline.transfer(
        native,
        source_backend="sapien",
        target_backends=("isaacsim", "mujoco", "metasim"),
        strict=True,
    )

    assert imported.source["backend"] == "sapien"
    assert imported.task.semantic_contract() == source_package.task.semantic_contract()
    assert set(results) == {"isaacsim", "mujoco", "metasim"}
    assert all(result.status == "compiled" for result in results.values())
    assert all(report.highest_consecutive_level == "L1" for report in reports.values())


def test_cli_text2env_writes_executable_workflow_artifacts(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    output = tmp_path / "cli"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "openxsim.py"),
            "--output",
            str(output),
            "text2env",
            "--instruction",
            "Move the red block onto the blue zone.",
            "--repo-root",
            str(tmp_path),
            "--backends",
            "mujoco,metasim",
            "--strict",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert set(payload["results"]) == {"mujoco", "metasim"}
    run_dir = output / payload["package_id"] / "text2env"
    assert (run_dir / "environment_package.json").is_file()
    assert (run_dir / "workflow_manifest.json").is_file()
