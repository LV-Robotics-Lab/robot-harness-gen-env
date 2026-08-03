from __future__ import annotations

import ast
import json
import shutil
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from agenticsim.openxsim.backends import BackendCompileError, compile_package
from agenticsim.openxsim.importers import (
    import_compile_manifest,
    import_environment,
    import_isaac_usda,
    import_metasim_scenario,
    import_mjcf,
    import_sapien_scene,
)
from agenticsim.openxsim.ir import EnvironmentPackage, IRValidationError
from agenticsim.openxsim.robotwin import RoboTwinRuntimeEvidenceError, runtime_evidence_from_rollout
from agenticsim.openxsim.text2env import compile_text


def make_package(tmp_path: Path) -> EnvironmentPackage:
    return compile_text(
        "Move the red block onto the blue zone.",
        repo_root=tmp_path,
        target_backends=("sapien", "isaacsim", "mujoco", "metasim"),
    )


def test_environment_package_roundtrip_and_digest(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    path = package.write_json(tmp_path / "package.json")
    recovered = EnvironmentPackage.read_json(path)

    assert recovered == package
    assert recovered.digest() == package.digest()
    assert len(package.digest()) == 64
    assert json.loads(package.canonical_json())["schema_version"] == "agenticsim.environment_package.v1"


def test_environment_package_rejects_missing_asset(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    invalid = replace(package, assets=())

    with pytest.raises(IRValidationError, match="missing assets"):
        invalid.validate()


def test_text2env_is_explicitly_text_only(tmp_path: Path) -> None:
    package = make_package(tmp_path)

    assert package.source["mode"] == "text_only"
    assert package.source["network_used"] is False
    assert package.source["asset_generation_used"] is False
    assert package.anchors == ()
    assert package.task.instruction == "Move the red block onto the blue zone."
    assert package.env.objects[0].instance_id == "red_block"
    assert package.task.success[0]["type"] == "in_region"


def test_all_four_backends_compile_primitive_scene_strictly(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    results = compile_package(
        package,
        tmp_path / "compiled",
        ("isaacsim", "mujoco", "sapien", "metasim"),
        strict=True,
    )

    assert set(results) == {"isaacsim", "mujoco", "sapien", "metasim"}
    assert all(result.status == "compiled" for result in results.values())
    assert all(Path(result.artifact_path).is_file() for result in results.values())
    assert Path(results["isaacsim"].artifact_path).read_text().startswith("#usda 1.0")
    ET.parse(results["mujoco"].artifact_path)
    json.loads(Path(results["sapien"].artifact_path).read_text())
    ast.parse(Path(results["metasim"].artifact_path).read_text())


def test_compile_manifest_roundtrip_preserves_canonical_package(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    results = compile_package(package, tmp_path / "compiled", ("mujoco", "metasim"), strict=True)

    for result in results.values():
        recovered = import_compile_manifest(result.manifest_path)
        assert recovered == package
        assert recovered.digest() == result.package_digest


def test_backend_artifact_uses_digest_bound_sidecar_when_available(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("sapien",), strict=True)["sapien"]

    recovered = import_environment(result.artifact_path)

    assert recovered == package


def test_native_sapien_scene_import_without_agenticsim_sidecar(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("sapien",), strict=True)["sapien"]
    native = tmp_path / "native_sapien/scene.json"
    native.parent.mkdir()
    shutil.copy2(result.artifact_path, native)

    recovered = import_sapien_scene(native)

    assert recovered.source["backend"] == "sapien"
    assert recovered.task.semantic_contract() == package.task.semantic_contract()
    assert [obj.instance_id for obj in recovered.env.objects] == [obj.instance_id for obj in package.env.objects]
    assert recovered.env.objects[0].pose == package.env.objects[0].pose


def test_native_metasim_scenario_import_without_python_execution(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("metasim",), strict=True)["metasim"]
    source_json = Path(result.metadata["scenario_json"])
    native = tmp_path / "native_metasim/scenario.json"
    native.parent.mkdir()
    shutil.copy2(source_json, native)

    recovered = import_metasim_scenario(native)

    assert recovered.source["backend"] == "metasim"
    assert recovered.task.semantic_contract() == package.task.semantic_contract()
    assert recovered.env.objects[0].pose == package.env.objects[0].pose


def test_native_isaac_usda_subset_import_marks_task_unbound(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("isaacsim",), strict=True)["isaacsim"]
    native = tmp_path / "native_isaac/scene.usda"
    native.parent.mkdir()
    shutil.copy2(result.artifact_path, native)

    recovered = import_isaac_usda(native)

    assert recovered.source["backend"] == "isaacsim"
    assert recovered.source["limitations"] == ["task_semantics_unbound"]
    assert recovered.task.success[0]["requires_binding"] is True
    assert recovered.env.objects[0].pose == package.env.objects[0].pose


def test_native_mjcf_import_recovers_structure_and_embedded_contract(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    result = compile_package(package, tmp_path / "compiled", ("mujoco",), strict=True)["mujoco"]

    imported = import_mjcf(result.artifact_path)

    assert imported.source["backend"] == "mujoco"
    assert imported.source["limitations"] == []
    assert {obj.instance_id for obj in imported.env.objects} == {"red_block", "blue_zone"}
    assert imported.task.semantic_contract() == package.task.semantic_contract()


def test_strict_compile_rejects_unresolved_cross_backend_asset(tmp_path: Path) -> None:
    asset_dir = tmp_path / "assets" / "vendor" / "robotwin" / "assets" / "objects" / "071_can"
    basket_dir = tmp_path / "assets" / "vendor" / "robotwin" / "assets" / "objects" / "110_basket"
    asset_dir.mkdir(parents=True)
    basket_dir.mkdir(parents=True)
    package = compile_text("Place the cola can into the basket.", repo_root=tmp_path, target_backends=("isaacsim",))

    with pytest.raises(BackendCompileError, match="no existing USD representation"):
        compile_package(package, tmp_path / "compiled", ("isaacsim",), strict=True)

    result = compile_package(package, tmp_path / "partial", ("isaacsim",), strict=False)["isaacsim"]
    assert result.status == "partial"
    assert len(result.blockers) == 2


def test_robotwin_backend_exports_hash_bound_can_basket_task_program(tmp_path: Path) -> None:
    for modelname in ("071_can", "110_basket"):
        (tmp_path / "assets" / "vendor" / "robotwin" / "assets" / "objects" / modelname).mkdir(parents=True)
    package = compile_text(
        "Place the cola can into the basket.",
        repo_root=tmp_path,
        target_backends=("robotwin",),
    )
    result = compile_package(package, tmp_path / "compiled", ("robotwin",), strict=True)["robotwin"]
    task_program = json.loads(Path(result.artifact_path).read_text())
    placement = json.loads(Path(result.metadata["placement_path"]).read_text())

    assert result.status == "compiled"
    assert task_program["task_binding"] == {
        "template": "place_in",
        "source_object_id": "cola_can",
        "target_object_id": "basket",
    }
    assert task_program["verifier"]["relation"] == "in"
    assert task_program["verifier"]["type"] == "conjunction"
    assert task_program["verifier"]["conditions"] == [dict(item) for item in package.task.success]
    assert task_program["environment_package"]["digest"] == package.digest()
    objects = {item["id"]: item for item in placement["objects"]}
    assert objects["cola_can"]["asset_id"] == "071_can"
    assert objects["cola_can"]["asset_metadata"]["scale"] == [0.05, 0.05, 0.05]
    assert objects["basket"]["physical"]["is_static"] is True


def test_robotwin_rollout_evidence_requires_exact_semantics_and_continuous_video(tmp_path: Path) -> None:
    for modelname in ("071_can", "110_basket"):
        (tmp_path / "assets" / "vendor" / "robotwin" / "assets" / "objects" / modelname).mkdir(parents=True)
    package = compile_text(
        "Place the cola can into the basket.",
        repo_root=tmp_path,
        target_backends=("robotwin",),
    )
    result = compile_package(package, tmp_path / "compiled", ("robotwin",), strict=True)["robotwin"]
    task_program = json.loads(Path(result.artifact_path).read_text())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    video = runtime / "observer_rollout_probe.mp4"
    video.write_bytes(b"continuous-video-evidence")
    report = {
        "status": "pass_generated_action_rollout",
        "task_id": package.package_id,
        "task_binding": {
            "template": "place_in",
            "source_id": "cola_can",
            "target_kind": "object",
            "target_id": "basket",
        },
        "placement_sha256": task_program["placement_sha256"],
        "plan_success": True,
        "check_success": True,
        "move_event_count": 6,
        "left_joint_path_len": 8,
        "right_joint_path_len": 0,
        "initial_objects": {"cola_can": {}, "basket": {}},
        "final_objects": {"cola_can": {}, "basket": {}},
        "observer_video": str(video),
        "video_capture": {"endpoint_only": False, "frame_count": 120, "fps": 12},
        "semantic_verification": {
            "schema": "agenticsim.robotwin_success_verification.v1",
            "conditions": [dict(item) for item in package.task.success],
            "results": [{"type": item["type"], "passed": True} for item in package.task.success],
            "all_passed": True,
        },
    }
    report_path = runtime / "rollout_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    evidence = runtime_evidence_from_rollout(package, result.artifact_path, report_path, minimum_video_frames=24)

    assert evidence["action_interface_bound"] is True
    assert evidence["success_evaluator_bound"] is True
    assert evidence["video"]["frame_count"] == 120
    assert "trajectory" not in evidence

    report["semantic_verification"]["conditions"] = report["semantic_verification"]["conditions"][:-1]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RoboTwinRuntimeEvidenceError, match="semantic conditions differ"):
        runtime_evidence_from_rollout(package, result.artifact_path, report_path)
