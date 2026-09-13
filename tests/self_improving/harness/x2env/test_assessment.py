"""Frozen physical assertions: analytical fixtures are not simulation evidence."""

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from self_improving.harness.x2env.assessment import assess_scene, evaluate_physics


def analytic():
    import copy

    scene = {
        "schema_version": "x2env.runtime_scene.v1",
        "scene_ir_sha256": "a" * 64,
        "seed": 0,
        "entities": [
            {
                "id": "table",
                "kind": "structural_box",
                "category": "table",
                "position_m": [0.0, 0.0, 0.35],
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "size_m": [1.0, 1.0, 0.1],
                "friction": 0.6,
            },
            {
                "id": "item",
                "kind": "rigid",
                "category": "cube",
                "position_m": [0.0, 0.0, 0.4],
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "urdf_path": "asset.urdf",
                "physics_path": "physics.json",
                "version_sha256": "b" * 64,
            },
        ],
        "relations": [{"source": "item", "target": "table", "relation": "on"}],
        "members": [],
    }
    vertices = [[-0.05, -0.05, 0.0], [0.05, -0.05, 0.0], [-0.05, 0.05, 0.0], [0.05, 0.05, 0.1]]
    loaded = {
        "item": {
            "mass_kg": 0.1,
            "authored_mass_kg": 0.1,
            "friction": [0.5],
            "supplied_friction": 0.5,
            "dofs": 6,
            "fixed": False,
            "collision_shapes": 1,
            "position_m": [0.0, 0.0, 0.4],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "geometry_basis": "actual_genesis_vertices_inverse_world_pose",
            "local_visual_vertices_m": vertices,
            "local_collision_vertices_m": vertices,
        },
        "table": {
            "fixed": True,
            "collision_shapes": 1,
            "friction": [0.6],
            "position_m": [0.0, 0.0, 0.35],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
        },
    }

    def rows(dt, steps):
        output = []
        for step in range(steps + 1):
            objects = {
                name: {
                    "position": pos,
                    "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "velocity": [0.0, 0.0, 0.0],
                    "angular_velocity": [0.0, 0.0, 0.0],
                }
                for name, pos in [
                    ("item", [0.0, 0.0, 0.4]),
                    ("table", [0.0, 0.0, 0.35]),
                    ("ground", [0.0, 0.0, 0.0]),
                ]
            }
            contact = {
                "a": "item",
                "b": "table",
                "position": [0.0, 0.0, 0.4],
                "normal": [0.0, 0.0, 1.0],
                "penetration": 0.0,
                "force_a": [0.0, 0.0, 0.981] if step else None,
                "force_b": [0.0, 0.0, -0.981] if step else None,
            }
            output.append(
                {
                    "step": step,
                    "time_s": step * dt,
                    "objects": objects,
                    "contacts": [contact],
                    "contact_phase": "solved_step" if step else "initial_detection",
                    "net_contact_forces": {"item": [0.0, 0.0, 0.981] if step else None},
                }
            )
        return output

    return (
        scene,
        rows(0.004, 1000),
        rows(0.002, 2000),
        {"baseline": loaded, "half_dt": copy.deepcopy(loaded)},
    )


def test_single_short_smoke_is_not_physical_qualification(tmp_path):
    result = assess_scene(
        {},
        package_root=tmp_path,
        scene_ir_bytes=b"{}",
        input_sha256="a" * 64,
        profiles={"load_step_smoke": {"root": str(tmp_path), "files": []}},
    )
    assert result["physical_status"] == "not_run"
    assert result["error_code"] == "incomplete_dual_profile"
    assert result["visual_status"] == "not_run"
    assert result["robot_policy_evaluated"] is False


def test_complete_analytic_support_obeys_frozen_metrics_without_execution_authority():
    report = evaluate_physics(*analytic())
    assert report["physical_status"] == "passed"
    assert report["authority"] == "trace_consistency_only"
    assert report["simulator_execution_proven"] is False
    assert any(c["name"] == "support_geometry" and c["observed"] == 0.45 for c in report["checks"])


def dynamic_analytic():
    """Synthetic two-rigid trace and authored square surface, not Genesis evidence."""
    import copy

    import trimesh

    from self_improving.harness.x2env.measured_support import MeasuredSupportSurface

    scene, a, b, loaded = analytic()
    target = copy.deepcopy(scene["entities"][1])
    target.update(id="plate", category="plate", version_sha256="c" * 64)
    scene["entities"].append(target)
    scene["entities"][1]["position_m"] = [0.0, 0.0, 0.5]
    scene["relations"] = [
        {"source": "item", "target": "plate", "relation": "on"},
        {"source": "plate", "target": "table", "relation": "on"},
    ]
    surface = MeasuredSupportSurface(
        version_sha256="c" * 64,
        plane_z_m=0.1,
        polygons=((((-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2), (-0.2, -0.2)),),),
        member_bindings=(),
        face_sources=(),
        shapely_version="synthetic",
        geos_version="synthetic",
    )
    surface_sha = hashlib.sha256(surface.model_dump_json().encode()).hexdigest()
    scene.update(
        schema_version="x2env.runtime_scene.v2",
        scene_ir_path="scene.json",
        support_profile="measured_single_support_dag.v1",
    )
    paths = {
        "scene.json": "a" * 64,
        "surface.json": surface_sha,
        "item-record.json": "d" * 64,
        "plate-record.json": "e" * 64,
        "item-selection.json": "f" * 64,
        "plate-selection.json": "1" * 64,
    }
    scene["members"] = [{"path": p, "sha256": sha, "size_bytes": 1} for p, sha in paths.items()]
    scene["support_bindings"] = [
        {
            "source_id": "item",
            "target_id": "plate",
            "kind": "measured_surface",
            "source_version_sha256": "b" * 64,
            "source_asset_record_path": "item-record.json",
            "target_version_sha256": "c" * 64,
            "target_asset_record_path": "plate-record.json",
            "surface_path": "surface.json",
            "surface_sha256": surface_sha,
            "selection_receipt_path": "item-selection.json",
        },
        {
            "source_id": "plate",
            "target_id": "table",
            "kind": "structural_top",
            "source_version_sha256": "c" * 64,
            "source_asset_record_path": "plate-record.json",
            "target_version_sha256": None,
            "target_asset_record_path": None,
            "surface_path": None,
            "surface_sha256": None,
            "selection_receipt_path": "plate-selection.json",
        },
    ]
    for values in loaded.values():
        values["plate"] = copy.deepcopy(values["item"])
        values["item"]["position_m"] = [0.0, 0.0, 0.5]
        for name, size in [("item", 0.1), ("plate", 0.4)]:
            mesh = trimesh.creation.box(extents=[size, size, 0.1])
            mesh.apply_translation([0, 0, 0.05])
            part = {"local_vertices_m": mesh.vertices.tolist(), "faces": mesh.faces.tolist()}
            values[name]["geometry_parts"] = {"visual": [part], "collision": [copy.deepcopy(part)]}
            values[name]["local_visual_vertices_m"] = mesh.vertices.tolist()
            values[name]["local_collision_vertices_m"] = mesh.vertices.tolist()
    for rows in [a, b]:
        for row in rows:
            row["objects"]["plate"] = copy.deepcopy(row["objects"]["item"])
            row["objects"]["item"]["position"] = [0.0, 0.0, 0.5]
            pair = copy.deepcopy(row["contacts"][0])
            pair["a"] = "plate"
            row["contacts"][0]["b"] = "plate"
            row["contacts"][0]["position"] = [0.0, 0.0, 0.5]
            if row["step"]:
                pair["force_a"] = [0.0, 0.0, 1.962]
                pair["force_b"] = [0.0, 0.0, -1.962]
            row["contacts"].append(pair)
            row["net_contact_forces"]["plate"] = [0.0, 0.0, 0.981] if row["step"] else None
    return scene, a, b, loaded, {"item": surface}


def test_dynamic_measured_surface_uses_shared_frozen_physical_evaluator():
    scene, a, b, loaded, surfaces = dynamic_analytic()
    report = evaluate_physics(scene, a, b, loaded, support_surfaces=surfaces)
    assert report["physical_status"] == "passed", report
    assert report["simulator_execution_proven"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "hole",
        "moving_target",
        "wrong_pair",
        "missing_surface",
        "wrong_surface",
        "missing_faces",
        "tilt",
    ],
)
def test_dynamic_support_attacks_cannot_pass(attack):
    scene, a, b, loaded, surfaces = dynamic_analytic()
    if attack == "hole":
        data = surfaces["item"].model_dump(mode="json")
        data["polygons"][0].append(
            [[-0.01, -0.01], [-0.01, 0.01], [0.01, 0.01], [0.01, -0.01], [-0.01, -0.01]]
        )
        surfaces["item"] = type(surfaces["item"]).model_validate_json(json.dumps(data))
        digest = hashlib.sha256(surfaces["item"].model_dump_json().encode()).hexdigest()
        scene["support_bindings"][0]["surface_sha256"] = digest
        next(m for m in scene["members"] if m["path"] == "surface.json")["sha256"] = digest
        for rows in [a, b]:
            for row in rows:
                row["contacts"][0]["position"] = [0.1, 0.0, 0.5]
    elif attack == "moving_target":
        for rows in [a, b]:
            for row in rows[1:]:
                row["objects"]["plate"]["position"] = [0.19, 0.0, 0.4]
    elif attack == "tilt":
        for row in a[1:]:
            row["objects"]["plate"]["orientation_wxyz"] = [0.9238795325, 0.3826834324, 0.0, 0.0]
    elif attack == "wrong_pair":
        for rows in [a, b]:
            for row in rows:
                row["contacts"][0]["b"] = "table"
                if row["step"]:
                    row["net_contact_forces"]["plate"] = [0.0, 0.0, 1.962]
    elif attack == "missing_surface":
        surfaces = {}
    elif attack == "wrong_surface":
        surfaces["item"] = surfaces["item"].model_copy(update={"version_sha256": "f" * 64})
    elif attack == "missing_faces":
        del loaded["baseline"]["item"]["geometry_parts"]["visual"][0]["faces"]
    report = evaluate_physics(scene, a, b, loaded, support_surfaces=surfaces)
    assert report["physical_status"] == "failed", report
    if attack in {"hole", "moving_target"}:
        assert any(c["name"] == "support_geometry" and not c["passed"] for c in report["checks"])
    if attack == "wrong_pair":
        assert any(c["name"] == "contact_dropout_max" and not c["passed"] for c in report["checks"])


@pytest.mark.parametrize("position", [[0.3, 0.0, 0.5], [0.0, 0.0, 0.55]])
def test_dynamic_contact_outside_selected_domain_cannot_supply_support(position):
    scene, a, b, loaded, surfaces = dynamic_analytic()
    for rows in [a, b]:
        for row in rows:
            row["contacts"][0]["position"] = position
    report = evaluate_physics(scene, a, b, loaded, support_surfaces=surfaces)
    assert report["physical_status"] == "failed", report


def test_common_dynamic_translation_uses_target_current_frame_not_initial_pose():
    scene, a, b, loaded, surfaces = dynamic_analytic()
    for rows in [a, b]:
        for row in rows[1:]:
            row["objects"]["item"]["position"] = [0.19, 0.0, 0.5]
            row["objects"]["plate"]["position"] = [0.19, 0.0, 0.4]
            row["contacts"][0]["position"] = [0.19, 0.0, 0.5]
            row["contacts"][1]["position"] = [0.19, 0.0, 0.4]
    report = evaluate_physics(scene, a, b, loaded, support_surfaces=surfaces)
    assert report["physical_status"] == "passed", report


@pytest.mark.parametrize("attack", [None, "missing_topology", "wrong_faces", "missing_surface"])
def test_bound_dynamic_assessment_rechecks_compiled_members_and_loaded_triangles(tmp_path, attack):
    """Real compiler/asset bytes, synthetic process and trace producer; not simulation."""
    import copy
    from pathlib import Path

    import trimesh

    from self_improving.harness.x2env.compile import StructuralPolicy, compile_scene
    from tests.self_improving.harness.x2env.test_compile import dynamic_stack_inputs

    kwargs = bound_fixture(tmp_path)
    source = tmp_path / "dynamic"
    source.mkdir()
    store, registry, _, ref, assets = dynamic_stack_inputs(source)
    compiled = compile_scene(
        ref,
        assets,
        registry=registry,
        store=store,
        output_root=source / "compiled",
        seed=11,
        policy=StructuralPolicy(thickness_m=0.04, surface_height_m=0.75, friction=0.5),
    )
    scene = compiled.runtime_scene
    package = source / "compiled"
    payload = scene.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    kwargs.update(
        scene=scene,
        package_root=package,
        scene_ir_bytes=(package / scene.scene_ir_path).read_bytes(),
        input_sha256="a" * 64,
    )
    _, a, b, _ = analytic()
    for name, rows in [("baseline", a), ("half_dt", b)]:
        profile = kwargs["profiles"][name]
        root = Path(profile["root"])
        loaded = {}
        objects = {}
        for entity in scene.entities:
            objects[entity.id] = {
                "position": list(entity.position_m),
                "orientation_wxyz": list(entity.orientation_wxyz),
                "velocity": [0.0, 0.0, 0.0],
                "angular_velocity": [0.0, 0.0, 0.0],
            }
            item = {
                "position_m": list(entity.position_m),
                "orientation_wxyz": list(entity.orientation_wxyz),
                "fixed": entity.kind == "structural_box",
                "collision_shapes": 1,
                "friction": [0.5],
            }
            if entity.kind == "rigid":
                physics = json.loads((package / entity.physics_path).read_bytes())
                parts = {}
                for kind, filename in [("visual", "visual.glb"), ("collision", "collision.obj")]:
                    mesh = trimesh.load(
                        (package / entity.urdf_path).parent / filename, force="scene", process=False
                    ).to_geometry()
                    parts[kind] = [
                        {
                            "geom_id": 0,
                            "local_vertices_m": mesh.vertices.tolist(),
                            "faces": mesh.faces.tolist(),
                        }
                    ]
                    item[f"local_{kind}_vertices_m"] = mesh.vertices.tolist()
                item.update(
                    mass_kg=0.2,
                    authored_mass_kg=0.2,
                    supplied_friction=0.5,
                    dofs=6,
                    geometry_basis="actual_genesis_vertices_inverse_world_pose",
                    geometry_parts=parts,
                    com_link_m=physics["center_of_mass_m"],
                    inertia_link_kg_m2=physics["inertia_kg_m2"],
                )
                if attack == "missing_topology":
                    item.pop("geometry_parts")
                if attack == "wrong_faces":
                    parts["visual"][0]["faces"][0].reverse()
            loaded[entity.id] = item
        for row in rows:
            ground = row["objects"]["ground"]
            row["objects"] = {**copy.deepcopy(objects), "ground": ground}
            row["net_contact_forces"] = {
                key: [0.0, 0.0, 1.962] if row["step"] else None for key in ["box", "plate"]
            }
            contacts = []
            for src, tgt, force in [("box", "plate", 1.962), ("plate", "table", 3.924)]:
                contacts.append(
                    {
                        "a": src,
                        "b": tgt,
                        "position": row["objects"][src]["position"],
                        "normal": [0.0, 0.0, 1.0],
                        "penetration": 0.0,
                        "force_a": [0.0, 0.0, force] if row["step"] else None,
                        "force_b": [0.0, 0.0, -force] if row["step"] else None,
                    }
                )
            row["contacts"] = contacts
        job = json.loads((root / "job.json").read_bytes())
        job.update(scene=payload, scene_sha256=digest)
        execution = json.loads((root / "result.json").read_bytes())
        execution["scene_sha256"] = digest
        for filename, value in [
            ("job.json", job),
            ("result.json", execution),
            ("loaded.json", loaded),
        ]:
            (root / filename).write_text(json.dumps(value))
        (root / "trace.ndjson").write_text("\n".join(json.dumps(row) for row in rows))
        for member in profile["files"]:
            raw = (root / member["path"]).read_bytes()
            member.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    if attack == "missing_surface":
        binding = next(b for b in scene.support_bindings if b.kind == "measured_surface")
        (package / binding.surface_path).unlink()
    report = assess_scene(**kwargs)
    if attack is None:
        assert report["physical_status"] == "passed", report
        assert report["topology_status"] == "passed"
    else:
        assert report["physical_status"] == "failed", report


@pytest.mark.parametrize(
    "attack",
    [
        "static",
        "no_contact",
        "fake_force",
        "penetration",
        "wrong_ground",
        "missing_step",
        "overflow_footprint",
        "moving_root",
    ],
)
def test_false_positive_attacks_never_pass(attack):
    scene, a, b, loaded = analytic()
    if attack == "static":
        loaded["baseline"]["item"]["fixed"] = True
    elif attack == "no_contact":
        for row in a:
            row["contacts"] = []
            row["net_contact_forces"]["item"] = [0.0, 0.0, 0.0] if row["step"] else None
    elif attack == "fake_force":
        a[-1]["net_contact_forces"]["item"] = [0.0, 0.0, 0.0]
    elif attack == "penetration":
        a[300]["contacts"][0]["penetration"] = 0.002
    elif attack == "wrong_ground":
        for row in a:
            row["contacts"][0]["b"] = "ground"
    elif attack == "missing_step":
        a[300]["step"] = 299
    elif attack == "overflow_footprint":
        for row in a[1:]:
            row["objects"]["item"]["position"][0] = 0.46
    elif attack == "moving_root":
        a[-1]["objects"]["table"]["position"][2] = 0.36
    assert evaluate_physics(scene, a, b, loaded)["physical_status"] == "failed"


def test_missing_full_second_profile_is_not_run():
    scene, a, b, loaded = analytic()
    assert evaluate_physics(scene, a, b[:-1], loaded)["physical_status"] == "not_run"


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (("objects", "item", "velocity"), [True, 0, 0], "invalid finite numeric"),
        (("objects", "item", "velocity"), [float("nan"), 0, 0], "invalid finite numeric"),
        (("objects", "item", "velocity"), [0, 0], "invalid finite numeric"),
        (("objects", "item", "orientation_wxyz"), [2, 0, 0, 0], "nonunit quaternion"),
        (("contact_phase",), "initial_detection", "invalid contact phase"),
        (("time_s",), float("inf"), "nonsequential trajectory"),
        (("contacts", 0, "a"), "table", "invalid contact pair"),
        (("contacts", 0, "b"), "unknown", "invalid contact pair"),
        (("contacts", 0, "normal"), [0, 0, 2], "invalid contact normal"),
        (("contacts", 0, "penetration"), -0.001, "invalid penetration"),
        (("contacts", 0, "force_b"), [0, 0, 0.981], "contact forces are not opposite"),
        (("net_contact_forces",), {}, "missing net force evidence"),
        (("objects",), {}, "incomplete object trace"),
    ],
)
def test_malformed_solved_trace_cannot_be_physical_evidence(path, value, reason):
    scene, baseline, half_dt, loaded = analytic()
    target = baseline[1]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "failed"
    assert report["error_code"] == "invalid_physical_evidence"
    assert reason in report["reason"]
    assert report["simulator_execution_proven"] is False


@pytest.mark.parametrize(
    "entity,field,value,reason",
    [
        ("item", "dofs", 0, "actual loaded physics differs"),
        ("item", "mass_kg", 0.2, "actual loaded physics differs"),
        ("table", "friction", [0.2], "loaded support friction differs"),
        ("item", "geometry_basis", "authored_aabb", "missing actual loaded geometry"),
        ("item", "local_visual_vertices_m", [[0, 0, 0]], "incomplete actual footprint"),
    ],
)
def test_loaded_runtime_properties_cannot_be_replaced_by_authored_claims(
    entity, field, value, reason
):
    scene, baseline, half_dt, loaded = analytic()
    loaded["baseline"][entity][field] = value
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "failed"
    assert reason in report["reason"]


@pytest.mark.parametrize("initial", ["pair_force", "net_force"])
def test_initial_detection_cannot_claim_a_solved_force(initial):
    scene, baseline, half_dt, loaded = analytic()
    if initial == "pair_force":
        baseline[0]["contacts"][0]["force_a"] = [0, 0, 0.981]
    else:
        baseline[0]["net_contact_forces"]["item"] = [0, 0, 0.981]
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "failed"
    assert report["error_code"] == "invalid_physical_evidence"


def test_loaded_entity_omission_cannot_pass_complete_scene():
    scene, baseline, half_dt, loaded = analytic()
    del loaded["baseline"]["table"]
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "failed"
    assert report["reason"] == "loaded entity set differs"


def test_missing_declared_support_is_not_an_implicit_ground_support():
    scene, baseline, half_dt, loaded = analytic()
    scene["relations"] = []
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "not_run"
    assert report["error_code"] == "unsupported_physical_profile"


def test_tilted_support_does_not_inherit_horizontal_profile():
    import math

    scene, baseline, half_dt, loaded = analytic()
    tilt = [math.cos(math.pi / 12), math.sin(math.pi / 12), 0.0, 0.0]
    scene["entities"][0]["orientation_wxyz"] = tilt
    for name, rows in (("baseline", baseline), ("half_dt", half_dt)):
        loaded[name]["table"]["orientation_wxyz"] = tilt
        for row in rows:
            row["objects"]["table"]["orientation_wxyz"] = tilt
    result = evaluate_physics(scene, baseline, half_dt, loaded)
    assert result["physical_status"] == "not_run"
    assert result["error_code"] == "unsupported_tilted_support_profile"


def test_dense_measured_footprint_keeps_same_support_assertions():
    scene, baseline, half_dt, loaded = analytic()
    sparse = evaluate_physics(scene, baseline, half_dt, loaded)
    assert sparse["physical_status"] == "passed"
    for profile in loaded.values():
        for key in ("local_visual_vertices_m", "local_collision_vertices_m"):
            profile["item"][key] = profile["item"][key] * 10
    dense = evaluate_physics(scene, baseline, half_dt, loaded)
    assert dense == sparse


def test_reversed_contact_pair_preserves_measured_support_force():
    scene, baseline, half_dt, loaded = analytic()
    for rows in (baseline, half_dt):
        for row in rows:
            contact = row["contacts"][0]
            contact["a"], contact["b"] = contact["b"], contact["a"]
            contact["force_a"], contact["force_b"] = contact["force_b"], contact["force_a"]
            contact["normal"] = [0, 0, -1]
    report = evaluate_physics(scene, baseline, half_dt, loaded)
    assert report["physical_status"] == "passed"
    assert report["authority"] == "trace_consistency_only"
    assert report["simulator_execution_proven"] is False


def bound_fixture(tmp_path):
    """Synthetic artifact producer: never a real process/simulation qualification."""
    import numpy as np
    import trimesh
    from PIL import Image

    from self_improving.harness.x2env.genesis_runtime import RuntimeScene
    from self_improving.harness.x2env.normalization import normalize_mesh

    scene, a, b, loaded = analytic()
    package = tmp_path / "package"
    source = tmp_path / "unit.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    normalization = normalize_mesh(
        source, package, dimensions_m=(0.1, 0.1, 0.1), up_axis="Z", mass_kg=0.1, friction=0.5
    )
    scene["members"] = normalization["files"]
    physics = json.loads((package / "physics.json").read_bytes())
    for data in loaded.values():
        data["item"].update(
            com_link_m=physics["center_of_mass_m"], inertia_link_kg_m2=physics["inertia_kg_m2"]
        )
        vertices = [[x, y, z] for x in [-0.05, 0.05] for y in [-0.05, 0.05] for z in [0.0, 0.1]]
        data["item"].update(local_visual_vertices_m=vertices, local_collision_vertices_m=vertices)
    ir = json.dumps({"input_sha256": "c" * 64}).encode()
    scene["scene_ir_sha256"] = hashlib.sha256(ir).hexdigest()
    scene = RuntimeScene.model_validate(scene).model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(scene, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    def write(path, body):
        path.write_text(json.dumps(body))

    profiles = {}
    for index, (name, rows, stride) in enumerate([("baseline", a, 25), ("half_dt", b, 50)]):
        root = tmp_path / name
        root.mkdir()
        (root / "frames").mkdir()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        images = []
        for i in range(41):
            target = root / "frames" / f"{i:04}.png"
            Image.fromarray(frame).save(target)
            images.append(
                {
                    "step": i * stride,
                    "time_s": i * 0.1,
                    "path": target.relative_to(root).as_posix(),
                    "captured_at": (
                        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
                    ).isoformat(),
                    "png_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "rgb_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                }
            )
        for filename, codec, pixfmt in [
            ("simulation.mkv", "ffv1", "bgr0"),
            ("preview.mp4", "libx264", "yuv420p"),
        ]:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-s",
                    "32x32",
                    "-r",
                    "10",
                    "-i",
                    "-",
                    "-c:v",
                    codec,
                    "-pix_fmt",
                    pixfmt,
                    str(root / filename),
                ],
                input=frame.tobytes() * 41,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        write(
            root / "media.json",
            {
                "frames": images,
                "total_frames": 41,
                "unique_frames": 1,
                "fps": 10,
                "width": 32,
                "height": 32,
            },
        )
        write(root / "job.json", {"scene": scene, "scene_sha256": digest, "profile": name})
        write(
            root / "result.json",
            {
                "scene_sha256": digest,
                "profile": name,
                "status": "passed",
                "simulator_executed": True,
                "steps": len(rows) - 1,
                "dt": 0.004 if not index else 0.002,
            },
        )
        write(
            root / "process.json",
            {
                "pid": 100 + index,
                "pgid": 100 + index,
                "exit_code": 0,
                "started_monotonic": 1.0,
                "ended_monotonic": 2.0,
            },
        )
        (root / "genesis_child.py").write_text("# synthetic fixture identity, not executed\n")
        write(
            root / "invocation.json",
            {"child_sha256": hashlib.sha256((root / "genesis_child.py").read_bytes()).hexdigest()},
        )
        write(root / "loaded.json", loaded[name])
        (root / "trace.ndjson").write_text("\n".join(json.dumps(row) for row in rows))
        files = [
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "size_bytes": p.stat().st_size,
            }
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ]
        profiles[name] = {"root": str(root), "files": files}
    return dict(
        scene=scene,
        package_root=package,
        scene_ir_bytes=ir,
        input_sha256="c" * 64,
        profiles=profiles,
    )


def reset_fixture(tmp_path):
    """Synthetic lifecycle producer, not an actual Genesis execution."""
    from pathlib import Path

    kwargs = bound_fixture(tmp_path)

    def update(profile, name, body):
        root = Path(kwargs["profiles"][profile]["root"])
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        (root / name).write_bytes(raw)
        files = kwargs["profiles"][profile]["files"]
        files[:] = [item for item in files if item["path"] != name]
        files.append(
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        )

    for profile in kwargs["profiles"]:
        root = Path(kwargs["profiles"][profile]["root"])
        proof = {
            "schema_version": "x2env.initial_scene_reset.v1",
            "status": "passed",
            "reset_invoked": True,
            "post_step_reset_evaluated": False,
            "warmup_steps": 0,
            "events": [
                {
                    "action": action,
                    "status": "completed",
                    "started_monotonic": start,
                    "ended_monotonic": start + 0.1,
                }
                for action, start in [("build", 1.1), ("reset", 1.3)]
            ],
            "objects": {
                name: {"velocity": [0.0, 0.0, 0.0], "angular_velocity": [0.0, 0.0, 0.0]}
                for name in ["item", "table", "ground"]
            },
        }
        update(profile, "reset-lifecycle.json", proof)
        claim = {
            "path": "reset-lifecycle.json",
            "sha256": hashlib.sha256((root / "reset-lifecycle.json").read_bytes()).hexdigest(),
            "status": "passed",
            "post_step_reset_evaluated": False,
        }
        execution = json.loads((root / "result.json").read_bytes())
        execution["initial_reset"] = claim
        update(profile, "result.json", execution)
        update(profile, "child-result.json", execution)
        update(profile, "genesis_child.py", "# synthetic boundary\nINITIAL_RESET_REQUIRED = True\n")
        update(
            profile,
            "invocation.json",
            {"child_sha256": hashlib.sha256((root / "genesis_child.py").read_bytes()).hexdigest()},
        )
        loaded = json.loads((root / "loaded.json").read_bytes())
        for state in loaded.values():
            state.update(initial_velocity=[0.0, 0.0, 0.0], initial_angular_velocity=[0.0, 0.0, 0.0])
        update(profile, "loaded.json", loaded)
    return kwargs, update


@pytest.mark.parametrize("fault", ["winding", "missing", "null", "valid", "legacy"])
def test_assessment_recomputes_topology_despite_producer_claim(tmp_path, fault):
    """Synthetic producer lies about topology; the public consumer must recompute."""
    from pathlib import Path

    import trimesh

    kwargs = bound_fixture(tmp_path)
    for profile in kwargs["profiles"].values():
        path = Path(profile["root"]) / "loaded.json"
        loaded = json.loads(path.read_bytes())
        parts = {}
        for kind, filename in (("visual", "visual.glb"), ("collision", "collision.obj")):
            mesh = trimesh.load(
                kwargs["package_root"] / filename, force="scene", process=False
            ).to_geometry()
            faces = mesh.faces.tolist()
            if fault == "winding":
                faces[0] = faces[0][::-1]
            parts[kind] = [
                {"geom_id": 0, "local_vertices_m": mesh.vertices.tolist(), "faces": faces}
            ]
        loaded["item"].update(geometry_parts=parts, topology_status="passed")
        if fault in {"missing", "legacy"}:
            loaded["item"].pop("geometry_parts")
        if fault == "null":
            loaded["item"]["geometry_parts"] = None
        if fault == "legacy":
            loaded["item"].pop("topology_status")
        path.write_text(json.dumps(loaded))
        member = next(m for m in profile["files"] if m["path"] == "loaded.json")
        member.update(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size
        )
    result = assess_scene(**kwargs)
    if fault in {"valid", "legacy"}:
        assert result["physical_status"] == "passed"
        assert result["topology_status"] == ("passed" if fault == "valid" else "not_run")
        assert result["simulator_execution_proven"] is False
    else:
        assert result["physical_status"] == "failed"
        assert "topology" in result["reason"]


def test_bound_reset_lifecycle_is_independently_checked(tmp_path):
    kwargs, _ = reset_fixture(tmp_path)
    result = assess_scene(**kwargs)
    assert result["physical_status"] == "passed", result
    assert result["initial_reset_status"] == "passed"
    assert result["post_step_reset_evaluated"] is False
    assert result["simulator_execution_proven"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "stripped",
        "hash",
        "schema",
        "order",
        "time",
        "entities",
        "nonzero",
        "nonfinite",
        "loaded",
        "claim",
        "raw_corruption",
        "extra_action",
        "boolean_time",
        "warmup",
        "reset_not_invoked",
        "trace_velocity",
        "invalid_child",
    ],
)
def test_rehashed_false_reset_proof_is_rejected(tmp_path, attack):
    from pathlib import Path

    kwargs, update = reset_fixture(tmp_path)
    root = Path(kwargs["profiles"]["baseline"]["root"])
    proof = json.loads((root / "reset-lifecycle.json").read_bytes())
    execution = json.loads((root / "result.json").read_bytes())
    if attack == "raw_corruption":
        (root / "reset-lifecycle.json").write_bytes(b"{}")
    elif attack == "invalid_child":
        update("baseline", "child-result.json", [])
    elif attack == "trace_velocity":
        rows = [json.loads(line) for line in (root / "trace.ndjson").read_text().splitlines()]
        rows[0]["objects"]["ground"]["velocity"] = [1e-8, 0.0, 0.0]
        update("baseline", "trace.ndjson", "\n".join(json.dumps(row) for row in rows))
    elif attack in {"missing", "stripped"}:
        kwargs["profiles"]["baseline"]["files"][:] = [
            item
            for item in kwargs["profiles"]["baseline"]["files"]
            if item["path"] != "reset-lifecycle.json"
        ]
        (root / "reset-lifecycle.json").unlink()
        if attack == "stripped":
            execution.pop("initial_reset")
            update("baseline", "result.json", execution)
            update("baseline", "child-result.json", execution)
    elif attack == "loaded":
        loaded = json.loads((root / "loaded.json").read_bytes())
        loaded["item"]["initial_velocity"] = [1e-8, 0.0, 0.0]
        update("baseline", "loaded.json", loaded)
    elif attack == "claim":
        execution["initial_reset"]["status"] = "failed"
        update("baseline", "child-result.json", execution)
    else:
        if attack == "extra_action":
            proof["events"].append(proof["events"][-1])
        if attack == "boolean_time":
            proof["events"][0]["started_monotonic"] = True
        if attack == "warmup":
            proof["warmup_steps"] = 1
        if attack == "reset_not_invoked":
            proof["reset_invoked"] = False
        if attack == "schema":
            proof["schema_version"] = "untrusted"
        if attack == "order":
            proof["events"].reverse()
        if attack == "time":
            proof["events"][1]["ended_monotonic"] = 3.0
        if attack == "entities":
            proof["objects"].pop("ground")
        if attack == "nonzero":
            proof["objects"]["item"]["velocity"] = [0.1, 0.0, 0.0]
        if attack == "nonfinite":
            proof["objects"]["item"]["velocity"] = [float("nan"), 0.0, 0.0]
        update("baseline", "reset-lifecycle.json", proof)
        execution["initial_reset"]["sha256"] = (
            "0" * 64
            if attack == "hash"
            else hashlib.sha256((root / "reset-lifecycle.json").read_bytes()).hexdigest()
        )
        update("baseline", "result.json", execution)
        update("baseline", "child-result.json", execution)
    result = assess_scene(**kwargs)
    assert result["physical_status"] == "failed", result
    assert result["initial_reset_status"] == "failed"
    assert "reset" in result["reason"], result


def test_bound_analytic_files_do_not_gain_live_execution_authority(tmp_path):
    kwargs = bound_fixture(tmp_path)
    result = assess_scene(**kwargs)
    assert result["physical_status"] == "passed", result
    assert result["status"] == "not_run"  # no fresh Codex visual advisory here
    assert result["simulator_execution_proven"] is False
    assert result["execution_evidence_bound"] is True
    assert result["initial_reset_status"] == "not_run"
    # File corruption and a copied identity must not inherit that result.
    from pathlib import Path

    trace = Path(kwargs["profiles"]["half_dt"]["root"]) / "trace.ndjson"
    trace.write_bytes(trace.read_bytes() + b"\n{}")
    result = assess_scene(**kwargs, visual_status="passed")
    assert result["physical_status"] == "failed"
    assert "artifact identity differs" in result["reason"]


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("total_frames", 40, "incomplete sequential media"),
        ("step", 1, "unbound or nonsequential capture"),
        ("captured_at", "2026-01-01T00:00:00", "invalid camera completion timestamp"),
        ("png_sha256", "0" * 64, "camera PNG differs"),
        ("width", 64, "camera format differs"),
        ("rgb_sha256", "0" * 64, "camera pixels differ"),
        ("unique_frames", 2, "lossless movie differs from ordered camera frames"),
    ],
)
def test_rehashed_media_manifest_cannot_replace_actual_capture(tmp_path, field, value, reason):
    from pathlib import Path

    kwargs = bound_fixture(tmp_path)
    profile = kwargs["profiles"]["baseline"]
    path = Path(profile["root"]) / "media.json"
    media = json.loads(path.read_bytes())
    if field in {"total_frames", "width", "unique_frames"}:
        media[field] = value
    else:
        media["frames"][0][field] = value
    path.write_text(json.dumps(media))
    for member in profile["files"]:
        if member["path"] == "media.json":
            member.update(
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                size_bytes=path.stat().st_size,
            )
    result = assess_scene(**kwargs, visual_status="passed")
    assert result["physical_status"] == "failed"
    assert result["reason"] == reason
    assert result.get("execution_evidence_bound") is not True


@pytest.mark.parametrize(
    "filter_value,reason",
    [
        ("setpts=2*PTS", "video frame timing differs from capture sequence"),
        ("scale=64:32", "video frame size/count differs"),
    ],
)
def test_rehashed_encoded_video_must_match_declared_capture(tmp_path, filter_value, reason):
    from pathlib import Path

    kwargs = bound_fixture(tmp_path)
    profile = kwargs["profiles"]["baseline"]
    root = Path(profile["root"])
    altered = root / "altered.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(root / "preview.mp4"),
            "-vf",
            filter_value,
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            str(altered),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    altered.replace(root / "preview.mp4")
    for member in profile["files"]:
        if member["path"] == "preview.mp4":
            raw = (root / "preview.mp4").read_bytes()
            member.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    result = assess_scene(**kwargs, visual_status="passed")
    assert result["physical_status"] == "failed"
    assert result["reason"] == reason


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_input",
        "reused_process",
        "false_geometry",
        "false_mass",
        "missing_frame",
        "nan_inertia",
        "invalid_visual_status",
        "relative_profile_root",
        "missing_required_artifact",
        "duplicate_artifact",
        "false_execution",
        "false_lifecycle",
        "false_child_identity",
    ],
)
def test_newly_hashed_but_semantically_false_evidence_is_rejected(tmp_path, attack):
    from pathlib import Path

    kwargs = bound_fixture(tmp_path)
    baseline = Path(kwargs["profiles"]["baseline"]["root"])
    half = Path(kwargs["profiles"]["half_dt"]["root"])

    def update(profile, name, body):
        root = Path(kwargs["profiles"][profile]["root"])
        path = root / name
        path.write_text(json.dumps(body))
        for member in kwargs["profiles"][profile]["files"]:
            if member["path"] == name:
                member.update(
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    size_bytes=path.stat().st_size,
                )

    if attack == "wrong_input":
        kwargs["input_sha256"] = "d" * 64
    elif attack == "invalid_visual_status":
        report = assess_scene(**kwargs, visual_status="approved")
        assert report["physical_status"] == "failed"
        assert report["reason"] == "invalid separate visual status"
        return
    elif attack == "relative_profile_root":
        kwargs["profiles"]["baseline"]["root"] = "relative-profile"
    elif attack == "missing_required_artifact":
        kwargs["profiles"]["baseline"]["files"] = [
            m for m in kwargs["profiles"]["baseline"]["files"] if m["path"] != "trace.ndjson"
        ]
        report = assess_scene(**kwargs, visual_status="passed")
        assert report["physical_status"] == "not_run"
        assert report["error_code"] == "missing_profile_evidence"
        assert report["missing_files"] == ["trace.ndjson"]
        return
    elif attack == "duplicate_artifact":
        members = kwargs["profiles"]["baseline"]["files"]
        members.append(dict(members[0]))
    elif attack == "false_execution":
        execution = json.loads((baseline / "result.json").read_bytes())
        execution["simulator_executed"] = False
        update("baseline", "result.json", execution)
    elif attack == "false_lifecycle":
        process = json.loads((baseline / "process.json").read_bytes())
        process["exit_code"] = 1
        update("baseline", "process.json", process)
    elif attack == "false_child_identity":
        invocation = json.loads((baseline / "invocation.json").read_bytes())
        invocation["child_sha256"] = "0" * 64
        update("baseline", "invocation.json", invocation)
    elif attack == "reused_process":
        update("half_dt", "process.json", json.loads((baseline / "process.json").read_bytes()))
    elif attack in ["false_geometry", "false_mass", "nan_inertia"]:
        loaded = json.loads((half / "loaded.json").read_bytes())
        if attack == "false_geometry":
            loaded["item"]["local_collision_vertices_m"][0][0] += 0.01
        elif attack == "nan_inertia":
            loaded["item"]["inertia_link_kg_m2"][0][0] = float("nan")
        else:
            loaded["item"]["mass_kg"] = 1.0
        update("half_dt", "loaded.json", loaded)
    else:
        kwargs["profiles"]["half_dt"]["files"] = [
            m for m in kwargs["profiles"]["half_dt"]["files"] if m["path"] != "frames/0040.png"
        ]
    assert assess_scene(**kwargs, visual_status="passed")["physical_status"] == "failed"
