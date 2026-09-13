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


def test_authorized_compile_defaults_and_measured_geometry_do_not_require_model(tmp_path):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import needs_design_grounding

    scene = intent(tmp_path).scene
    support, obj = scene.entities
    scene = scene.model_copy(
        update={
            "entities": (
                support.model_copy(
                    update={"pose": support.pose.model_copy(update={"yaw_degrees": 0})}
                ),
                obj.model_copy(update={"dimensions": None}),
            )
        }
    )
    assert (
        needs_design_grounding(
            scene, StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
        )
        is False
    )


def policy():
    from self_improving.harness.x2env.grounding import SceneDesignPolicy

    return SceneDesignPolicy(
        enabled=True,
        structural_defaults_enabled=True,
        world_anchor_xy=(0, 0),
        world_anchor_yaw_degrees=0,
    )


def test_entity_named_scene_does_not_capture_full_or_indexed_paths(tmp_path):
    from self_improving.harness.x2env.design_plan import canonical_design_field

    original = intent(tmp_path).scene
    value = original.model_dump(mode="json")
    value["entities"][0]["id"] = "scene"
    for entity in value["entities"]:
        if entity["pose"]["frame"] == "support":
            entity["pose"]["frame"] = "scene"
    for relation in value["relations"]:
        for side in ("source", "target"):
            if relation[side] == "support":
                relation[side] = "scene"
    scene = type(original).model_validate_json(json.dumps(value))
    assert (
        canonical_design_field(scene, "scene.entities.object.pose") == "scene.entities.object.pose"
    )
    assert canonical_design_field(scene, "scene.entities[1].pose") == "scene.entities.object.pose"
    assert canonical_design_field(scene, "scene.pose") == "scene.entities.scene.pose"


@pytest.mark.parametrize("bare_id", [False, True])
def test_original_scene_index_paths_resolve_without_rewriting_unknowns(tmp_path, bare_id):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    proposal = intent(tmp_path)
    paths = (
        "scene.entities[0].dimensions",
        "scene.entities[0].pose.position",
        "scene.entities[1].pose.position[2]",
    )
    if bare_id:
        paths = ("support.dimensions", "support.pose.position", "object.pose.position[2]")
    proposal = proposal.model_copy(
        update={
            "unknowns": tuple(
                old.model_copy(update={"field": path})
                for old, path in zip(proposal.unknowns, paths, strict=True)
            )
        }
    )
    original = proposal.model_dump_json()
    plan = classify_design_unknowns(
        proposal, policy(), StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    )
    assert plan.resolved_unknown_indices == (0, 1, 2)
    assert proposal.model_dump_json() == original


@pytest.mark.parametrize(
    "path",
    [
        "scene.entities[-1].pose",
        "scene.entities[2].pose",
        "scene.entities[00].pose",
        "scene.entities[0]suffix.pose",
        "scene.entities[0].category",
        "scene.entities[0].pose.position[3]",
        "scene.entities[0].pose.position[-1]",
        "supporter.pose.position",
        "support.category",
        "support.pose.position[3]",
        "missing.pose.position",
    ],
)
def test_index_paths_do_not_expand_authority(tmp_path, path):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    proposal = intent(tmp_path)
    proposal = proposal.model_copy(
        update={
            "unknowns": (proposal.unknowns[0].model_copy(update={"field": path, "critical": True}),)
        }
    )
    with pytest.raises(ValueError):
        classify_design_unknowns(
            proposal,
            policy(),
            StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        )


@pytest.mark.parametrize("unknown_mode", ["noncritical", "omitted"])
def test_actual_missing_geometry_and_defaults_do_not_depend_on_unknown_flags(
    tmp_path, unknown_mode
):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    proposal = intent(tmp_path)
    proposal = proposal.model_copy(
        update={
            "unknowns": tuple(u.model_copy(update={"critical": False}) for u in proposal.unknowns)
            if unknown_mode == "noncritical"
            else (),
        }
    )
    original = proposal.model_dump_json()
    plan = classify_design_unknowns(
        proposal, policy(), StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5)
    )
    assert plan.rules and not plan.requires_media
    assert plan.resolved_unknown_indices == ((0, 1, 2) if unknown_mode == "noncritical" else ())
    assert proposal.model_dump_json() == original


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


@pytest.mark.parametrize("critical", [False, True])
@pytest.mark.parametrize(
    "field", ["scene.entities.support.dimensions[2]", "scene.entities.support.category"]
)
def test_reported_conflict_never_becomes_design_authority(tmp_path, critical, field):
    from self_improving.harness.x2env.compile import StructuralPolicy
    from self_improving.harness.x2env.design_plan import classify_design_unknowns

    proposal = intent(tmp_path)
    proposal = proposal.model_copy(
        update={
            "unknowns": (
                proposal.unknowns[0].model_copy(
                    update={"critical": critical, "reason_kind": "conflict", "field": field}
                ),
                *proposal.unknowns[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="grounding_requires_clarification"):
        classify_design_unknowns(
            proposal,
            policy(),
            StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
        )


@pytest.mark.parametrize("field", [None, "dimensions", "dimension_axis", "position", "yaw"])
def test_readiness_reads_required_values_instead_of_model_unknown_rows(tmp_path, field):
    from self_improving.harness.x2env.contracts import SceneIR
    from self_improving.harness.x2env.design_plan import needs_design_grounding

    raw = intent(tmp_path).scene.model_dump(mode="json")
    for entity in raw["entities"]:
        entity["dimensions"] = [0.8, 0.6, 0.04]
        entity["pose"]["position"] = [0, 0, 0.75]
        entity["pose"]["yaw_degrees"] = 0
    obj = raw["entities"][1]
    if field == "dimensions":
        obj["dimensions"] = None
    elif field == "dimension_axis":
        obj["dimensions"][1] = None
    elif field == "position":
        obj["pose"]["position"][0] = None
    elif field == "yaw":
        obj["pose"]["yaw_degrees"] = None
    assert needs_design_grounding(SceneIR.model_validate_json(json.dumps(raw))) is (
        field in {"position", "yaw"}
    )
    assert needs_design_grounding(None) is False


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
