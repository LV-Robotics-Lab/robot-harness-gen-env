from __future__ import annotations

import sys
import json
import os
import subprocess
import types
from pathlib import Path

from scene_gen.catalog import load_catalog
from scene_gen.envs.generated_scene import load_resolved_scene
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene

ROOT = Path(__file__).resolve().parents[2]


class FakeActor:
    def __init__(self) -> None:
        self.name: str | None = None
        self.qpos: tuple[float, ...] = ()

    def set_name(self, name: str) -> None:
        self.name = name

    def set_qpos(self, qpos) -> None:
        self.qpos = tuple(qpos)


def test_generated_scene_loads_only_resolved_assets_and_registers_footprints(monkeypatch) -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("A red can is left of a plastic basket near the center.", seed=11)
    resolved = solve_scene(spec, catalog)
    calls: list[dict] = []

    class FakePose:
        def __init__(self, position, orientation) -> None:
            self.position = tuple(position)
            self.orientation = tuple(orientation)

    def make_actor(task, *, pose, modelname, model_id, convex, is_static):
        calls.append(
            {
                "kind": "rigid",
                "pose": pose,
                "modelname": modelname,
                "model_id": model_id,
                "convex": convex,
                "is_static": is_static,
            }
        )
        return FakeActor()

    def make_urdf(task, *, pose, modelname, modelid, fix_root_link):
        calls.append(
            {
                "kind": "urdf",
                "pose": pose,
                "modelname": modelname,
                "model_id": modelid,
                "is_static": fix_root_link,
            }
        )
        return FakeActor()

    sapien_package = types.ModuleType("sapien")
    sapien_core = types.ModuleType("sapien.core")
    sapien_core.Pose = FakePose
    sapien_package.core = sapien_core
    envs_package = types.ModuleType("envs")
    envs_package.__path__ = []
    envs_utils = types.ModuleType("envs.utils")
    envs_utils.create_actor = make_actor
    envs_utils.create_sapien_urdf_obj = make_urdf
    monkeypatch.setitem(sys.modules, "sapien", sapien_package)
    monkeypatch.setitem(sys.modules, "sapien.core", sapien_core)
    monkeypatch.setitem(sys.modules, "envs", envs_package)
    monkeypatch.setitem(sys.modules, "envs.utils", envs_utils)

    task = types.SimpleNamespace(prohibited_area=[])
    actors = load_resolved_scene(task, resolved)

    assert set(actors) == {item.object_id for item in resolved.objects}
    assert len(calls) == len(resolved.objects)
    assert len(task.prohibited_area) == len(resolved.objects)
    for item, call in zip(resolved.objects, calls):
        assert call["modelname"] == item.asset_id
        assert call["model_id"] == item.model_id
        assert call["is_static"] == item.is_static
        assert call["pose"].position == item.pose.position_m
        assert call["pose"].orientation == item.pose.orientation_wxyz
        assert actors[item.object_id].name == item.object_id


def test_generate_scene_cli_writes_structured_input_failure(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "script" / "generate_scene.py"),
            "--prompt",
            "Use asset_id 071_can and model_id 0.",
            "--seed",
            "9",
            "--asset-catalog",
            str(ROOT / "tests" / "fixtures" / "asset_catalog.json"),
            "--out-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    reports = list((tmp_path / "_failures").glob("*/failure_report.json"))
    assert completed.returncode == 2
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert report["stage"] == "scene_spec_validation"
    assert report["blocker"] == "request rejected before grounding"
    assert report["seed"] == 9
