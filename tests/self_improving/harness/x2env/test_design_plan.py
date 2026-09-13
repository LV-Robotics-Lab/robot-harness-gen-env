"""Public policy classification, no model or physical success claim."""

import json

import pytest

from tests.self_improving.harness.x2env.test_grounding import setup


def intent(tmp_path):
    store, _, _, proposal, _ = setup(tmp_path)
    value = json.loads(store.read_artifact(proposal))["proposal"]
    support, obj = value["scene"]["entities"]
    support["dimensions"] = [0.8, 0.6, None]
    support["pose"]["position"] = [None, None, None]
    obj["dimensions"] = [0.05, 0.06, 0.07]
    obj["pose"] = {"frame": "support", "position": [-0.15, 0.1, None], "yaw_degrees": 45}
    template = value["unknowns"][0]
    value["unknowns"] = [
        {**template, "field": path, "reason_kind": kind}
        for path, kind in [
            ("scene.entities.support.dimensions[2]", "unspecified"),
            ("scene.entities.support.pose", "unspecified"),
            ("scene.entities.object.pose.position[2]", "pose_unobservable"),
        ]
    ]
    from self_improving.harness.x2env.contracts import SceneIntentProposal

    return SceneIntentProposal.model_validate_json(json.dumps(value))


def policy():
    from self_improving.harness.x2env.grounding import SceneDesignPolicy

    return SceneDesignPolicy(
        enabled=True,
        structural_defaults_enabled=True,
        world_anchor_xy=(0, 0),
        world_anchor_yaw_degrees=0,
    )


@pytest.mark.parametrize("height_reason", ["pose_unobservable", "unspecified"])
def test_exact_structural_defaults_and_on_height_are_classified(tmp_path, height_reason):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    proposal = intent(tmp_path)
    proposal = proposal.model_copy(
        update={
            "unknowns": (
                *proposal.unknowns[:2],
                proposal.unknowns[2].model_copy(update={"reason_kind": height_reason}),
            )
        }
    )
    plan = classify_design_unknowns(
        proposal, policy(), StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    )
    assert plan.resolved_unknown_indices == (0, 1, 2)
    assert not plan.requires_media
    assert {rule.basis for rule in plan.rules} == {
        "deployment_structural_default",
        "on_geometry_derived",
    }
    assert proposal.unknowns[0].critical


def test_unknown_foreground_dimension_axis_is_geometry_design_not_media(tmp_path):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.contracts import SceneIntentProposal
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    raw = intent(tmp_path).model_dump(mode="json")
    raw["scene"]["entities"][1]["dimensions"][1] = None
    raw["unknowns"].append({**raw["unknowns"][0], "field": "scene.entities.object.dimensions[1]"})
    proposal = SceneIntentProposal.model_validate_json(json.dumps(raw))
    plan = classify_design_unknowns(
        proposal, policy(), StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    )
    assert not plan.requires_media
    anchors = [rule for rule in plan.rules if rule.basis == "asset_anchor"]
    assert [(rule.entity_id, rule.path, rule.value) for rule in anchors] == [
        ("object", "dimensions[1]", None)
    ]
    assert proposal.unknowns[-1].reason_kind == "unspecified" and proposal.unknowns[-1].critical


@pytest.mark.parametrize(
    "fault", ["conflict", "category", "frame", "known_axis", "multiple_support", "unknown_xy"]
)
def test_unrelated_or_conflicting_unknown_is_not_design_authority(tmp_path, fault):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    raw = intent(tmp_path).model_dump(mode="json")
    if fault == "conflict":
        raw["unknowns"][0]["reason_kind"] = "conflict"
    if fault == "category":
        raw["unknowns"][0]["field"] = "scene.entities.support.category"
    if fault == "frame":
        raw["scene"]["entities"][1]["pose"]["frame"] = "world"
    if fault == "known_axis":
        raw["scene"]["entities"][0]["dimensions"][2] = 0.1
    if fault == "multiple_support":
        raw["scene"]["relations"].append(raw["scene"]["relations"][0])
    if fault == "unknown_xy":
        raw["scene"]["entities"][1]["pose"]["position"][0] = None
        raw["unknowns"][2].update(field="scene.entities.object.pose", reason_kind="unspecified")
    from self_improving.harness.x2env.contracts import SceneIntentProposal

    with pytest.raises(ValueError):
        proposal = SceneIntentProposal.model_validate_json(json.dumps(raw))
        classify_design_unknowns(
            proposal,
            policy(),
            StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"world_anchor_xy": (float("nan"), 0), "world_anchor_yaw_degrees": 0},
        {"world_anchor_xy": (0, 0), "world_anchor_yaw_degrees": float("inf")},
    ],
)
def test_defaults_require_explicit_finite_anchor(values):
    from self_improving.harness.x2env.grounding import SceneDesignPolicy

    with pytest.raises(ValueError):
        SceneDesignPolicy(enabled=True, structural_defaults_enabled=True, **values)
