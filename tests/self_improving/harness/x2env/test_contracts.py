"""Canonical public input and SceneIR seam; no private dispatch tests."""

import pytest
from pydantic import ValidationError

from self_improving.harness.x2env.contracts import X2EnvRequest


def scene_document():
    fields = ("category", "color", "dimensions", "material", "pose", "articulation_state")
    provenance = {
        field: [{"source": "text", "input_sha256": "a" * 64, "kind": "explicit"}]
        for field in fields
    }
    return {
        "revision": 0,
        "input_sha256": "a" * 64,
        "entities": [
            {
                "id": "table",
                "category": "table",
                "role": "structural_support",
                "color": None,
                "dimensions": [0.9, 0.7, 0.04],
                "material": None,
                "pose": {"frame": "world", "position": [0, 0, 0.4], "yaw_degrees": 0},
                "articulation_state": None,
                "provenance": provenance,
            },
            {
                "id": "mouse",
                "category": "mouse",
                "color": "pink",
                "dimensions": None,
                "material": None,
                "pose": {"frame": "table", "position": [0.1, -0.08, 0], "yaw_degrees": 30},
                "articulation_state": None,
                "provenance": provenance,
            },
        ],
        "relations": [
            {
                "source": "mouse",
                "relation": "on",
                "target": "table",
                "provenance": [{"source": "text", "input_sha256": "a" * 64, "kind": "explicit"}],
            }
        ],
    }


def test_scene_preserves_entity_attributes_reference_frame_and_field_provenance():
    import json

    from self_improving.harness.x2env.contracts import SceneIR

    scene = SceneIR.model_validate_json(json.dumps(scene_document()))
    assert len(scene.entities) == 2
    assert scene.entities[1].color == "pink"
    assert scene.entities[1].pose.frame == "table"
    assert scene.entities[1].pose.position == (0.1, -0.08, 0.0)
    assert scene.entities[1].pose.yaw_degrees == 30
    assert scene.entities[1].provenance["color"][0].input_sha256 == "a" * 64


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "ninth",
        "relation",
        "provenance",
        "dangling",
        "self_support",
        "support_category",
        "cycle",
    ],
)
def test_scene_rejects_invalid_entity_graph_and_missing_provenance(fault):
    import copy
    import json

    from self_improving.harness.x2env.contracts import SceneIR

    doc = copy.deepcopy(scene_document())
    if fault == "duplicate":
        doc["entities"][1]["id"] = "table"
    elif fault == "ninth":
        doc["entities"] += [{**doc["entities"][1], "id": f"m{i}"} for i in range(7)]
    elif fault == "relation":
        doc["relations"][0]["relation"] = "teleport"
    elif fault == "provenance":
        doc["entities"][1]["provenance"] = {}
    elif fault == "dangling":
        doc["entities"][1]["pose"]["frame"] = "missing"
    elif fault == "self_support":
        doc["relations"][0]["target"] = "mouse"
    elif fault == "support_category":
        doc["entities"][0]["category"] = "cabinet"
    else:
        doc["relations"].append({**doc["relations"][0], "source": "table", "target": "mouse"})
    with pytest.raises(ValidationError):
        SceneIR.model_validate_json(json.dumps(doc))


def test_request_accepts_text_and_preserves_seed_and_source_constraints(tmp_path):
    request = X2EnvRequest(
        text="在桌上放一个粉红色鼠标。",
        seed=11,
        allowed_sources=("local",),
        idempotency_key="source-exact",
        output_dir=str(tmp_path / "output"),
    )
    assert request.text == "在桌上放一个粉红色鼠标。"
    assert request.seed == 11
    assert request.allowed_sources == ("local",)


@pytest.mark.parametrize(
    "change",
    [
        {"text": None},
        {"text": "  "},
        {"seed": -1},
        {"seed": True},
        {"allowed_sources": ()},
        {"allowed_sources": ("local", "local")},
        {"allowed_sources": ("generated",)},
        {"output_dir": "relative"},
        {"execution_mode": "qualified"},
    ],
)
def test_request_rejects_empty_input_invalid_source_and_execution_override(tmp_path, change):
    values = dict(text="a block", seed=1, idempotency_key="test", output_dir=str(tmp_path))
    values.update(change)
    with pytest.raises(ValidationError):
        X2EnvRequest(**values)


def test_image_and_video_only_requests_are_valid_but_relative_media_is_not(tmp_path):
    from self_improving.harness.x2env.contracts import InputMedia

    media = InputMedia(path=str(tmp_path / "input.png"))
    for inputs in ({"images": (media,)}, {"video": media}):
        request = X2EnvRequest(**inputs, seed=1, idempotency_key="media", output_dir=str(tmp_path))
        assert request.text is None
    with pytest.raises(ValidationError):
        InputMedia(path="relative.png")


def test_horizontal_relation_has_no_support_cycle_but_requires_known_entities():
    import json

    from self_improving.harness.x2env.contracts import SceneIR

    document = scene_document()
    document["relations"][0]["relation"] = "left_of"
    scene = SceneIR.model_validate_json(json.dumps(document))
    assert scene.relations[0].relation == "left_of"
    document["relations"][0]["target"] = "missing"
    with pytest.raises(ValidationError):
        SceneIR.model_validate_json(json.dumps(document))
