from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from scene_gen.schema import SceneSpec, SceneSpecError, scene_spec_json_schema


def valid_payload() -> dict:
    return {
        "schema_version": "robotwin.scene_spec.v1",
        "scene_id": "can_left_of_basket",
        "request": "Place a can left of a basket.",
        "language": "en",
        "frame": {
            "name": "robotwin_world",
            "x_axis": "right",
            "y_axis": "front",
            "z_axis": "up",
            "handedness": "right_handed",
        },
        "unit": "m",
        "seed": 7,
        "objects": [
            {"object_id": "can_1", "category": "can", "region": "center"},
            {"object_id": "basket_1", "category": "basket", "region": "center"},
        ],
        "relations": [
            {"relation": "on_table", "source": "can_1", "target": "table"},
            {"relation": "on_table", "source": "basket_1", "target": "table"},
            {"relation": "left_of", "source": "can_1", "target": "basket_1"},
            {"relation": "near", "source": "can_1", "target": "basket_1", "max_distance_m": 0.18},
        ],
    }


def test_scene_spec_is_deterministic_and_fixes_the_world_frame() -> None:
    first = SceneSpec.model_validate(valid_payload())
    second = SceneSpec.model_validate(copy.deepcopy(valid_payload()))
    assert first.digest() == second.digest()
    assert first.frame.x_axis == "right"
    assert first.frame.y_axis == "front"
    assert first.frame.z_axis == "up"
    assert first.unit == "m"


@pytest.mark.parametrize("forbidden", ["asset_id", "model_id", "xyz", "quaternion", "python"])
def test_scene_spec_rejects_backend_fields(forbidden: str) -> None:
    payload = valid_payload()
    payload["objects"][0][forbidden] = "not allowed"
    with pytest.raises((ValidationError, SceneSpecError), match="cannot contain"):
        SceneSpec.model_validate(payload)


def test_scene_spec_rejects_unknown_and_self_references() -> None:
    unknown = valid_payload()
    unknown["relations"][2]["target"] = "missing_1"
    with pytest.raises((ValidationError, SceneSpecError), match="unknown relation target"):
        SceneSpec.model_validate(unknown)

    self_relation = valid_payload()
    self_relation["relations"][2]["target"] = "can_1"
    with pytest.raises((ValidationError, SceneSpecError), match="must differ"):
        SceneSpec.model_validate(self_relation)


def test_scene_spec_rejects_missing_support_and_relation_cycles() -> None:
    missing_support = valid_payload()
    missing_support["relations"] = missing_support["relations"][1:]
    with pytest.raises((ValidationError, SceneSpecError), match="requires exactly one support relation"):
        SceneSpec.model_validate(missing_support)

    cycle = valid_payload()
    cycle["relations"].append(
        {"relation": "left_of", "source": "basket_1", "target": "can_1"}
    )
    with pytest.raises((ValidationError, SceneSpecError), match="left/right relation cycle"):
        SceneSpec.model_validate(cycle)


def test_scene_spec_accepts_nested_support_and_rejects_support_cycles() -> None:
    payload = valid_payload()
    payload["relations"] = [
        {"relation": "on_table", "source": "basket_1", "target": "table"},
        {"relation": "inside", "source": "can_1", "target": "basket_1"},
    ]
    spec = SceneSpec.model_validate(payload)
    assert [item.relation.value for item in spec.relations] == ["on_table", "inside"]

    payload["relations"] = [
        {"relation": "on_top_of", "source": "basket_1", "target": "can_1"},
        {"relation": "inside", "source": "can_1", "target": "basket_1"},
    ]
    with pytest.raises((ValidationError, SceneSpecError), match="support relation cycle"):
        SceneSpec.model_validate(payload)


def test_scene_spec_keeps_articulation_semantic_and_backend_agnostic() -> None:
    payload = valid_payload()
    payload["objects"][0]["articulation"] = {
        "state": "partially_open",
        "open_fraction": 0.4,
        "joint_selector": "all_movable",
    }
    spec = SceneSpec.model_validate(payload)
    assert spec.objects[0].articulation is not None
    assert spec.objects[0].articulation.open_fraction == 0.4


def test_scene_spec_rejects_contradictory_distances() -> None:
    payload = valid_payload()
    payload["relations"].append(
        {
            "relation": "distance_at_least",
            "source": "can_1",
            "target": "basket_1",
            "min_distance_m": 0.25,
        }
    )
    with pytest.raises((ValidationError, SceneSpecError), match="distance constraints contradict"):
        SceneSpec.model_validate(payload)


def test_json_schema_exposes_only_semantic_scene_fields() -> None:
    schema = scene_spec_json_schema()
    encoded = str(schema)
    assert schema["title"] == "SceneSpec"
    assert "SceneObjectSpec" in schema["$defs"]
    assert "asset_id" not in encoded
    assert "position_m" not in encoded
    assert "orientation_wxyz" not in encoded
