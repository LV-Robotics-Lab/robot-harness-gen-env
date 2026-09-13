"""Explicit subprocess double; no real model or reconstruction execution."""

import hashlib
import json

import pytest

from self_improving.harness.x2env.codex import CodexBackend
from self_improving.harness.x2env.contracts import SceneIR
from tests.self_improving.harness.x2env.test_codex import executable
from tests.self_improving.harness.x2env.test_resolver import inputs


def test_text_without_source_image_is_blocked_before_model(tmp_path):
    from self_improving.harness.x2env.reconstruction_planning import plan_reconstruction

    store, _, _, scene, _ = inputs(tmp_path)
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    path = executable(tmp_path, {})
    backend = CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store)
    result = plan_reconstruction(
        backend, scene, entity, None, output_root=tmp_path / "plan", timeout=10
    )
    assert result.status == "blocked" and result.error_code == "missing_reconstruction_image"
    assert result.segmentation is None
    assert not (tmp_path / "plan" / "process.json").exists()


def test_actual_image_planning_binds_segmentation_and_estimates(tmp_path):
    from self_improving.harness.x2env.reconstruction_planning import plan_reconstruction

    store, _, _, scene, image = inputs(tmp_path, dimensions=[0.1, None, None])
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    answer = {
        "box_xyxy": [0.0, 0.0, 2.0, 2.0],
        "point_coords": [[1.0, 1.0]],
        "point_labels": [1],
        "dimensions_m": [0.1, 0.1, 0.1],
        "mass_kg": 0.1,
        "friction": 0.6,
        "reason": "unit model double",
    }
    path = executable(tmp_path, answer)
    result = plan_reconstruction(
        CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store),
        scene,
        entity,
        image,
        output_root=tmp_path / "plan",
        timeout=10,
    )
    assert result.status == "completed"
    assert result.segmentation.input_sha256 == image.sha256
    assert result.segmentation.proposal_receipt == result.receipt
    assert result.parameters.dimensions_axis_basis == (
        "input_explicit",
        "codex_estimate",
        "codex_estimate",
    )
    assert result.parameters.mass_basis == "codex_estimate"
    receipt = json.loads(store.read_artifact(result.receipt))
    assert receipt["image_ref"] == image.model_dump(mode="json")
    assert receipt["external_agent_executed"] is True
    assert receipt["registration_authorized"] is False


@pytest.mark.parametrize("fault", [None, "missing", "wrong_name", "out_of_range"])
def test_reconstruction_plan_preserves_requested_color_as_estimate(tmp_path, fault):
    from self_improving.harness.x2env.reconstruction_planning import plan_reconstruction

    store, _, _, scene, image = inputs(tmp_path)
    original = json.loads(store.read_artifact(scene))
    original["entities"][0]["color"] = "pink"
    scene = store.write_artifact(json.dumps(original).encode(), "application/json")
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    answer = {
        "box_xyxy": [0, 0, 2, 2],
        "point_coords": [],
        "point_labels": [],
        "dimensions_m": [0.1, 0.1, 0.1],
        "mass_kg": 0.1,
        "friction": 0.5,
        "reason": "fixture",
        "base_color": [1.0, 0.5, 0.75, 1.0],
        "declared_color": "pink",
    }
    if fault == "missing":
        answer.update(base_color=None, declared_color=None)
    elif fault == "wrong_name":
        answer["declared_color"] = "green"
    elif fault == "out_of_range":
        answer["base_color"] = [2.0, 0.5, 0.75, 1.0]
    path = executable(tmp_path, answer)
    result = plan_reconstruction(
        CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store),
        scene,
        entity,
        image,
        output_root=tmp_path / "plan",
        timeout=10,
    )
    if fault:
        assert result.status == "failed" and result.parameters is None
        return
    assert result.status == "completed"
    assert result.parameters.base_color == (1.0, 0.5, 0.75, 1.0)
    assert result.parameters.declared_color == "pink"
    assert result.parameters.color_basis == "codex_estimate"


@pytest.mark.parametrize(
    "fault", ["outside_box", "outside_point", "negative_only", "dimensions", "license", "tools"]
)
def test_invalid_model_plan_cannot_reach_segmentation_adapter(tmp_path, fault):
    from self_improving.harness.x2env.reconstruction_planning import plan_reconstruction

    store, _, _, scene, image = inputs(tmp_path, dimensions=[0.1, None, None])
    entity = SceneIR.model_validate_json(store.read_artifact(scene)).entities[0]
    answer = {
        "box_xyxy": [0.0, 0.0, 2.0, 2.0],
        "point_coords": [[1.0, 1.0]],
        "point_labels": [1],
        "dimensions_m": [0.1, 0.1, 0.1],
        "mass_kg": 0.1,
        "friction": 0.6,
        "reason": "unit model double",
    }
    if fault == "outside_box":
        answer["box_xyxy"] = [0.0, 0.0, 4.0, 4.0]
    if fault == "outside_point":
        answer["point_coords"] = [[3.0, 3.0]]
    if fault == "negative_only":
        answer.update(box_xyxy=None, point_labels=[0])
    if fault == "dimensions":
        answer["dimensions_m"] = [0.2, 0.1, 0.1]
    if fault == "license":
        answer["registration_authorized"] = True
    path = executable(tmp_path, answer, tool=fault == "tools")
    result = plan_reconstruction(
        CodexBackend(path, hashlib.sha256(path.read_bytes()).hexdigest(), "double", store),
        scene,
        entity,
        image,
        output_root=tmp_path / "plan",
        timeout=10,
    )
    assert result.status == "failed" and result.segmentation is None and result.parameters is None
    assert store.read_artifact(result.receipt)
