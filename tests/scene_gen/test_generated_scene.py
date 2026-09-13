from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from scene_gen.catalog import load_catalog
from scene_gen.envs.generated_scene import (
    _apply_physical_material,
    _runtime_modelname,
    load_resolved_scene,
)
from scene_gen.parser import parse_rule_based
from scene_gen.solver import solve_scene

ROOT = Path(__file__).resolve().parents[2]


class FakeActor:
    def __init__(self) -> None:
        self.name: str | None = None
        self.mass_kg: float | None = None
        self.qpos: tuple[float, ...] = ()
        self.material = types.SimpleNamespace(base_color=[1.0, 1.0, 1.0, 1.0])
        shape = types.SimpleNamespace(material=self.material, parts=[])
        component = types.SimpleNamespace(render_shapes=[shape])
        self.actor = types.SimpleNamespace(components=[component])

    def set_name(self, name: str) -> None:
        self.name = name

    def set_mass(self, mass_kg: float) -> None:
        self.mass_kg = mass_kg

    def set_qpos(self, qpos) -> None:
        self.qpos = tuple(qpos)


def test_runtime_modelname_uses_valid_external_asset_directory(tmp_path: Path) -> None:
    asset_directory = tmp_path / "901_robolab_corn_can"
    source_paths = (
        asset_directory / "collision" / "base0.glb",
        asset_directory / "model_data0.json",
        asset_directory / "visual" / "base0.glb",
    )
    for path in source_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    item = types.SimpleNamespace(
        asset_id="901_robolab_corn_can",
        model_id=0,
        source_files=tuple(str(path) for path in source_paths),
    )

    assert _runtime_modelname(item) == str(asset_directory.resolve())


def test_apply_physical_material_updates_all_collision_shapes() -> None:
    class FakeCollisionShape:
        def __init__(self) -> None:
            self.physical_material = None

        def set_physical_material(self, material) -> None:
            self.physical_material = material

    shapes = [FakeCollisionShape(), FakeCollisionShape()]
    physics_component = types.SimpleNamespace(get_collision_shapes=lambda: shapes)
    actor = types.SimpleNamespace(actor=types.SimpleNamespace(components=[physics_component]))

    created_materials = []

    class FakeScene:
        def create_physical_material(
            self,
            static_friction,
            dynamic_friction,
            restitution,
        ):
            material = types.SimpleNamespace(
                static_friction=static_friction,
                dynamic_friction=dynamic_friction,
                restitution=restitution,
            )
            created_materials.append(material)
            return material

    task = types.SimpleNamespace(scene=FakeScene())
    item = types.SimpleNamespace(
        object_id="scissors_1",
        static_friction=2.0,
        dynamic_friction=1.5,
        restitution=0.1,
    )

    applied_count = _apply_physical_material(task, actor, item)

    assert applied_count == 2
    assert len(created_materials) == 1
    material = created_materials[0]
    assert material.static_friction == 2.0
    assert material.dynamic_friction == 1.5
    assert material.restitution == 0.1
    assert all(shape.physical_material is material for shape in shapes)


@pytest.mark.parametrize("attack", ["material_factory", "collision_shapes"])
def test_physical_material_application_rejects_missing_runtime_capability(attack: str) -> None:
    item = types.SimpleNamespace(
        object_id="can_1",
        static_friction=1.0,
        dynamic_friction=0.8,
        restitution=0.1,
    )
    if attack == "material_factory":
        task = types.SimpleNamespace(scene=types.SimpleNamespace())
        actor = types.SimpleNamespace()
        message = "cannot create physical material"
    else:
        task = types.SimpleNamespace(
            scene=types.SimpleNamespace(create_physical_material=lambda *_: object())
        )
        actor = types.SimpleNamespace(actor=types.SimpleNamespace(components=[]))
        message = "exposes no collision shapes"

    with pytest.raises(RuntimeError, match=message):
        _apply_physical_material(task, actor, item)


def test_generated_scene_rejects_mass_override_when_actor_has_no_mass_setter(monkeypatch) -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    resolved = solve_scene(
        parse_rule_based("A red can is left of a plastic basket near the center.", seed=41),
        catalog,
    )
    first = resolved.objects[0]
    resolved = resolved.model_copy(
        update={
            "objects": (
                first.model_copy(update={"mass_kg": 1.0}),
                *resolved.objects[1:],
            )
        }
    )
    actor = FakeActor()
    actor.set_mass = None  # type: ignore[method-assign]

    class FakePose:
        def __init__(self, *_args) -> None:
            pass

    sapien_package = types.ModuleType("sapien")
    sapien_core = types.ModuleType("sapien.core")
    sapien_core.Pose = FakePose
    sapien_package.core = sapien_core
    envs_package = types.ModuleType("envs")
    envs_package.__path__ = []
    envs_utils = types.ModuleType("envs.utils")
    envs_utils.create_actor = lambda *_args, **_kwargs: actor
    envs_utils.create_sapien_urdf_obj = lambda *_args, **_kwargs: actor
    monkeypatch.setitem(sys.modules, "sapien", sapien_package)
    monkeypatch.setitem(sys.modules, "sapien.core", sapien_core)
    monkeypatch.setitem(sys.modules, "envs", envs_package)
    monkeypatch.setitem(sys.modules, "envs.utils", envs_utils)

    with pytest.raises(RuntimeError, match="does not expose set_mass"):
        load_resolved_scene(types.SimpleNamespace(), resolved)


def test_generated_scene_loads_only_resolved_assets_and_registers_footprints(monkeypatch) -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    spec = parse_rule_based("A red can is left of a plastic basket near the center.", seed=11)
    resolved = solve_scene(spec, catalog)
    resolved = resolved.model_copy(
        update={
            "objects": tuple(
                item.model_copy(update={"mass_kg": 1.5}) if item.asset_id == "071_can" else item
                for item in resolved.objects
            )
        }
    )
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
    assert actors["can_1"].material.base_color == [0.82, 0.10, 0.12, 1.0]
    assert actors["can_1"].mass_kg == 1.5
    assert actors["basket_1"].mass_kg is None


def test_snapshot_asset_roots_override_native_rigid_and_urdf_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    resolved = solve_scene(
        parse_rule_based("A red can is left of a plastic basket near the center.", seed=23),
        catalog,
    )
    resolved = resolved.model_copy(
        update={
            "objects": tuple(
                item.model_copy(update={"load_type": "urdf"})
                if item.object_id == "basket_1"
                else item
                for item in resolved.objects
            )
        }
    )
    snapshot_roots = {}
    for item in resolved.objects:
        root = (
            tmp_path / "snapshot" / item.asset_id / "selected-model"
            if item.load_type == "urdf"
            else tmp_path / "snapshot" / item.asset_id
        )
        root.mkdir(parents=True)
        if item.load_type == "urdf":
            (root / "mobility.urdf").write_text('<robot name="fixture"/>\n', encoding="utf-8")
        snapshot_roots[item.object_id] = root.absolute()
        (tmp_path / "assets" / "objects" / item.asset_id).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    calls: list[dict[str, Any]] = []

    class FakePose:
        def __init__(self, *_args) -> None:
            pass

    def make_actor(_task, **kwargs):
        calls.append({"kind": "rigid", **kwargs})
        return FakeActor()

    def make_urdf(_task, **kwargs):
        calls.append({"kind": "urdf", **kwargs})
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

    load_resolved_scene(
        types.SimpleNamespace(prohibited_area=[]),
        resolved,
        asset_roots=snapshot_roots,
    )

    assert {(call["kind"], call["modelname"]) for call in calls} == {
        ("rigid", str(snapshot_roots["can_1"])),
        ("urdf", str(snapshot_roots["basket_1"])),
    }
    assert next(call for call in calls if call["kind"] == "urdf")["modelid"] is None


@pytest.mark.parametrize(
    "attack",
    ["not_mapping", "missing", "extra", "relative", "unavailable", "wrong_name", "symlink"],
)
def test_snapshot_asset_root_mapping_is_closed_and_canonical(
    tmp_path: Path,
    attack: str,
) -> None:
    catalog = load_catalog(ROOT / "tests" / "fixtures" / "asset_catalog.json")
    resolved = solve_scene(
        parse_rule_based("A red can is left of a plastic basket near the center.", seed=29),
        catalog,
    )
    roots: Any = {}
    for item in resolved.objects:
        root = tmp_path / item.asset_id
        root.mkdir()
        roots[item.object_id] = root.absolute()
    if attack == "not_mapping":
        roots = []
    elif attack == "missing":
        roots.pop(next(iter(roots)))
    elif attack == "extra":
        roots["not_in_scene"] = tmp_path
    elif attack == "relative":
        roots[next(iter(roots))] = Path("relative")
    elif attack == "unavailable":
        first = next(iter(roots))
        roots[first].rmdir()
    elif attack == "wrong_name":
        wrong = tmp_path / "wrong_asset"
        wrong.mkdir()
        roots[next(iter(roots))] = wrong
    else:
        first = next(iter(roots))
        target = roots[first]
        link = tmp_path / "linked"
        link.symlink_to(target, target_is_directory=True)
        roots[first] = link

    with pytest.raises(ValueError, match="asset_roots|snapshot asset root"):
        load_resolved_scene(types.SimpleNamespace(), resolved, asset_roots=roots)


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
