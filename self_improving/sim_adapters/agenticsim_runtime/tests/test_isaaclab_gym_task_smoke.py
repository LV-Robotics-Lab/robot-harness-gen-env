from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    module_path = ROOT / "scripts" / "smoke_isaaclab_gym_task.py"
    sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location("smoke_isaaclab_gym_task", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_apply_env_cfg_assignments_supports_json_and_raw_strings() -> None:
    module = load_module()
    cfg = SimpleNamespace(
        garment_name=None,
        sim=SimpleNamespace(dt=1 / 60),
        options={"enabled": False},
    )

    applied = module._apply_env_cfg_assignments(
        cfg,
        [
            "garment_name=Top_Long_Unseen_0",
            "sim.dt=0.02",
            "options.enabled=true",
        ],
    )

    assert cfg.garment_name == "Top_Long_Unseen_0"
    assert cfg.sim.dt == 0.02
    assert cfg.options["enabled"] is True
    assert applied == [
        {"path": "garment_name", "previous": None, "value": "Top_Long_Unseen_0"},
        {"path": "sim.dt", "previous": 1 / 60, "value": 0.02},
        {"path": "options.enabled", "previous": False, "value": True},
    ]


@pytest.mark.parametrize("assignment", ["", "missing_equals", "bad-path=1", "__class__=x"])
def test_parse_env_cfg_assignment_rejects_invalid_paths(assignment: str) -> None:
    module = load_module()

    with pytest.raises(ValueError):
        module._parse_env_cfg_assignment(assignment)


def test_apply_env_cfg_assignments_rejects_unknown_fields() -> None:
    module = load_module()

    with pytest.raises(AttributeError, match="does not exist"):
        module._apply_env_cfg_assignments(SimpleNamespace(), ["garment_name=x"])


def test_camera_from_bounds_centers_and_scales_view() -> None:
    module = load_module()

    eye, lookat = module._camera_from_bounds(
        (-1.0, -2.0, 0.0),
        (3.0, 2.0, 2.0),
        padding=2.0,
    )

    assert lookat == (1.0, 0.0, 1.0)
    assert eye == (9.0, 8.0, 5.4)


def test_translate_bounds_uses_runtime_root_offset() -> None:
    module = load_module()

    minimum, maximum = module._translate_bounds(
        (-1.0, -2.0, 0.0),
        (3.0, 2.0, 2.0),
        (0.0, 0.0, 0.4),
        (12.0, -7.0, 0.6),
    )

    assert minimum == (11.0, -9.0, 0.19999999999999996)
    assert maximum == (15.0, -5.0, 2.2)
