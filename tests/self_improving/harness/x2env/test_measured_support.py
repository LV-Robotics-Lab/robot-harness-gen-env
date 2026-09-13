"""Authored synthetic geometry, real Registry/CAS; never runtime qualification."""

import hashlib
import json

import numpy as np
import pytest
import trimesh

from self_improving.harness.x2env.assets import AssetLicense, AssetRegistry, AssetSource
from self_improving.harness.x2env.store import Store


def rectangle(x0, y0, x1, y1, z=0.1):
    return trimesh.Trimesh(
        vertices=[[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )


def registered(tmp_path, visual, collision=None, transform=None, urdf=None, file_type="obj"):
    store = Store(tmp_path / "state")
    registry = AssetRegistry(store)
    root = tmp_path / "asset"
    root.mkdir()
    visual.export(root / f"visual.{file_type}")
    (visual if collision is None else collision).export(root / f"collision.{file_type}")
    (root / "asset.urdf").write_text(
        '<robot name="fixture"><link name="body">'
        + "".join(
            f'<{kind}><geometry><mesh filename="{kind}.{file_type}"/></geometry></{kind}>'
            for kind in ("visual", "collision")
        )
        + "</link></robot>"
    )
    if transform:
        field, value = transform
        raw = (root / "asset.urdf").read_text()
        raw = (
            raw.replace("<mesh filename=", f'<mesh scale="{value}" filename=')
            if field == "scale"
            else raw.replace("<geometry>", f'<origin {field}="{value}"/><geometry>')
        )
        (root / "asset.urdf").write_text(raw)
    if urdf is not None:
        (root / "asset.urdf").write_text(urdf)
    files = ("asset.urdf", f"visual.{file_type}", f"collision.{file_type}")
    report = {
        "schema_version": "x2env.normalization.v1",
        "status": "passed",
        "entrypoint": "asset.urdf",
        "fixture_only": True,
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
                "size_bytes": (root / name).stat().st_size,
            }
            for name in files
        ],
    }
    evidence = store.write_artifact(b"synthetic test geometry, not runtime", "text/plain")
    version = registry.register(
        "test",
        "surface",
        root,
        "asset.urdf",
        files=files,
        normalization_report=store.write_artifact(json.dumps(report).encode(), "application/json"),
        license=AssetLicense(
            spdx="CC0-1.0", source_url="https://example.org/fixture", evidence=evidence
        ),
        source=AssetSource(
            kind="local", provider="test_fixture", source_ref="synthetic", evidence=evidence
        ),
        receipt=evidence,
    )
    return registry, store, version


def parts(mesh):
    return {
        kind: [
            {"geom_id": 0, "local_vertices_m": mesh.vertices.tolist(), "faces": mesh.faces.tolist()}
        ]
        for kind in ("visual", "collision")
    }


def pose(x=0.0, y=0.0, z=0.0):
    return {"position_m": [x, y, z], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]}


def test_convex_source_interior_crossing_hole_is_rejected(tmp_path):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    ring = trimesh.util.concatenate(
        [
            rectangle(-0.5, -0.5, 0.5, -0.1),
            rectangle(-0.5, 0.1, 0.5, 0.5),
            rectangle(-0.5, -0.1, -0.1, 0.1),
            rectangle(0.1, -0.1, 0.5, 0.1),
        ]
    )
    registry, store, version = registered(tmp_path, ring)
    surfaces = measure_support_surfaces(version.version_sha256, registry=registry, store=store)
    assert len(surfaces) == 1
    result = evaluate_support_footprint(
        surfaces[0],
        source_geometry_parts=parts(rectangle(-0.2, -0.2, 0.2, 0.2)),
        source_pose=pose(),
        target_pose=pose(),
    )
    assert result["error_code"] == "footprint_outside_support_domain"
    assert result["status"] == "failed"
    assert surfaces[0].basis == "immutable_authored_mesh_geometry"


@pytest.mark.parametrize("shift,expected", [(0.0, "passed"), (0.281, "failed")])
def test_complete_footprint_margin_and_dynamic_target_pose(tmp_path, shift, expected):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    result = evaluate_support_footprint(
        surface,
        source_geometry_parts=parts(rectangle(-0.2, -0.2, 0.2, 0.2)),
        source_pose=pose(1.0 + shift),
        target_pose=pose(1.0),
    )
    assert result["status"] == expected
    assert result["margin_m"] == pytest.approx(0.3 - shift)
    assert result["required_margin_m"] == 0.02
    assert result["physical_evaluated"] is False
    assert surface.version_sha256 == version.version_sha256
    assert surface.actual_loaded_evaluated is False


def test_collision_different_layer_cannot_form_common_surface(tmp_path):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    registry, store, version = registered(
        tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5), rectangle(-0.5, -0.5, 0.5, 0.5, 0.2)
    )
    with pytest.raises(ValueError, match="support_surface_not_found"):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


def test_overhead_surface_removes_covered_floor_and_returns_both_planes(tmp_path):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    mesh = trimesh.util.concatenate(
        [rectangle(-0.5, -0.5, 0.5, 0.5), rectangle(-0.1, -0.1, 0.1, 0.1, 0.3)]
    )
    registry, store, version = registered(tmp_path, mesh)
    surfaces = measure_support_surfaces(version.version_sha256, registry=registry, store=store)
    assert [surface.plane_z_m for surface in surfaces] == pytest.approx([0.1, 0.3])
    result = evaluate_support_footprint(
        surfaces[0],
        source_geometry_parts=parts(rectangle(-0.2, -0.2, 0.2, 0.2)),
        source_pose=pose(),
        target_pose=pose(),
    )
    assert result["error_code"] == "footprint_outside_support_domain"


@pytest.mark.parametrize("attack", ["negative", "nan", "degenerate", "mixed_bool", "tilt"])
def test_invalid_footprint_and_tilt_are_rejected(tmp_path, attack):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    geometry = parts(rectangle(-0.1, -0.1, 0.1, 0.1))
    target = pose()
    if attack == "negative":
        geometry["visual"][0]["faces"][0][0] = -1
    if attack == "nan":
        geometry["visual"][0]["local_vertices_m"][0][0] = float("nan")
    if attack == "degenerate":
        geometry["visual"][0]["faces"][0] = [0, 0, 0]
    if attack == "mixed_bool":
        geometry["visual"][0]["faces"][0][0] = False
    if attack == "tilt":
        target["orientation_wxyz"] = [float(np.cos(0.1)), float(np.sin(0.1)), 0.0, 0.0]
    with pytest.raises(ValueError, match="invalid_support_mesh|unsupported_nonhorizontal_surface"):
        evaluate_support_footprint(
            surface, source_geometry_parts=geometry, source_pose=pose(), target_pose=target
        )


def test_collision_filled_hole_does_not_erase_visual_hole(tmp_path):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    ring = trimesh.util.concatenate(
        [
            rectangle(-0.5, -0.5, 0.5, -0.1),
            rectangle(-0.5, 0.1, 0.5, 0.5),
            rectangle(-0.5, -0.1, -0.1, 0.1),
            rectangle(0.1, -0.1, 0.5, 0.1),
        ]
    )
    registry, store, version = registered(tmp_path, ring, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    assert len(surface.polygons[0]) == 2  # exterior and actual preserved hole
    crossing = evaluate_support_footprint(
        surface,
        source_geometry_parts=parts(rectangle(-0.2, -0.2, 0.2, 0.2)),
        source_pose=pose(),
        target_pose=pose(),
    )
    assert crossing["status"] == "failed"
    fitting = evaluate_support_footprint(
        surface,
        source_geometry_parts=parts(rectangle(-0.04, -0.04, 0.04, 0.04)),
        source_pose=pose(0.3, 0.3),
        target_pose=pose(),
    )
    assert fitting["status"] == "passed"
    assert fitting["margin_m"] == pytest.approx(0.16)


def test_face_reindexing_keeps_measured_domain(tmp_path):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    original = rectangle(-0.5, -0.5, 0.5, 0.5)
    permutation = np.array([2, 0, 3, 1])
    inverse = np.argsort(permutation)
    remapped = trimesh.Trimesh(
        vertices=original.vertices[permutation],
        faces=inverse[original.faces[::-1]][:, [1, 2, 0]],
        process=False,
    )
    registry, store, version = registered(tmp_path, original, remapped)
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    assert surface.plane_z_m == pytest.approx(0.1)
    assert len(surface.polygons) == 1 and len(surface.polygons[0]) == 1


def test_unresolved_slope_crossing_plane_is_not_repaired(tmp_path):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    slope = trimesh.Trimesh(
        vertices=[[-0.1, -0.1, 0], [0.1, -0.1, 0.2], [0, 0.1, 0.2]],
        faces=[[0, 1, 2]],
        process=False,
    )
    mesh = trimesh.util.concatenate([rectangle(-0.5, -0.5, 0.5, 0.5), slope])
    registry, store, version = registered(tmp_path, mesh)
    with pytest.raises(ValueError, match="unsupported_crossing_surface_plane"):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("faces", None),
        ("faces", 3),
        ("faces", [1]),
        ("faces", [[0, "1", 2]]),
        ("parts", None),
        ("parts", [1]),
        ("part", None),
        ("part", 5),
        ("local_vertices_m", None),
        ("local_vertices_m", [[False, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        ("local_vertices_m", [["0", 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
    ],
)
def test_raw_mesh_types_fail_with_stable_public_error(tmp_path, field, bad):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    geometry = parts(rectangle(-0.1, -0.1, 0.1, 0.1))
    if field == "parts":
        geometry = bad
    elif field == "part":
        geometry["visual"] = [bad]
    else:
        geometry["visual"][0][field] = bad
    with pytest.raises(ValueError, match="invalid_support_mesh"):
        evaluate_support_footprint(
            surface, source_geometry_parts=geometry, source_pose=pose(), target_pose=pose()
        )


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"position_m": ["0", 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
        {"position_m": [False, 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
        {"position_m": [0.0, 0.0, 0.0], "orientation_wxyz": [True, 0.0, 0.0, 0.0]},
    ],
)
def test_raw_pose_types_fail_without_coercion(tmp_path, bad):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    with pytest.raises(ValueError, match="invalid_support_pose"):
        evaluate_support_footprint(
            surface,
            source_geometry_parts=parts(rectangle(-0.1, -0.1, 0.1, 0.1)),
            source_pose=bad,
            target_pose=pose(),
        )


@pytest.mark.parametrize("field", ["scale", "xyz", "rpy"])
def test_urdf_trailing_transform_tokens_are_not_silently_ignored(tmp_path, field):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    registry, store, version = registered(
        tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5), transform=(field, "1 1 1 trailing")
    )
    with pytest.raises(ValueError, match="invalid_support_mesh: invalid URDF transform"):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


@pytest.mark.parametrize("field", ["version_sha256", "member_bindings"])
def test_surface_identity_requires_existing_sha256_contract(tmp_path, field):
    from self_improving.harness.x2env.measured_support import (
        MeasuredSupportSurface,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    raw = surface.model_dump(mode="json")
    raw[field] = "not-a-hash" if field == "version_sha256" else [["visual.obj", "not-a-hash"]]
    with pytest.raises(ValueError, match="string_pattern_mismatch"):
        MeasuredSupportSurface.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("scale", "2 2 2"), ("xyz", "1 0 1"), ("rpy", "0 0 1")])
def test_complete_urdf_transforms_are_applied(tmp_path, field, value):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    registry, store, version = registered(
        tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5), transform=(field, value)
    )
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    assert surface.plane_z_m == pytest.approx(
        0.2 if field == "scale" else 1.1 if field == "xyz" else 0.1
    )


@pytest.mark.parametrize("value", ["0 1 1", "nan 1 1", "1x 1 1"])
def test_invalid_urdf_scale_is_not_repaired(tmp_path, value):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    registry, store, version = registered(
        tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5), transform=("scale", value)
    )
    with pytest.raises(ValueError, match="invalid_support_mesh: invalid URDF transform"):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


@pytest.mark.parametrize(
    "body,error",
    [
        ('<link name="a"/><link name="b"/>', "unsupported_articulation"),
        (
            '<link name="a"><visual><geometry><box size="1 1 1"/></geometry></visual></link>',
            "mesh required",
        ),
        (
            '<link name="a"><visual><geometry><mesh filename="asset.urdf"/>'
            "</geometry></visual></link>",
            "support_asset_identity_mismatch",
        ),
        ('<link name="a"/>', "support_surface_not_found"),
    ],
)
def test_inspected_urdf_unsupported_shapes_are_explicit(tmp_path, body, error):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    registry, store, version = registered(
        tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5), urdf=f'<robot name="test">{body}</robot>'
    )
    with pytest.raises(ValueError, match=error):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


@pytest.mark.parametrize("reflect", [False, True])
def test_glb_node_transform_is_consumed_not_ignored(tmp_path, reflect):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    scene = trimesh.Scene()
    matrix = np.eye(4)
    matrix[0, 0] = -1 if reflect else 2
    matrix[2, 3] = 0.7
    scene.add_geometry(rectangle(-0.5, -0.5, 0.5, 0.5), transform=matrix)
    registry, store, version = registered(tmp_path, scene, file_type="glb")
    if reflect:
        with pytest.raises(ValueError, match="unsupported node transform"):
            measure_support_surfaces(version.version_sha256, registry=registry, store=store)
    else:
        surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[
            0
        ]
        assert surface.plane_z_m == pytest.approx(0.8)
        assert max(x for x, y in surface.polygons[0][0]) == pytest.approx(1.0)


def test_multiple_urdf_origins_create_disjoint_measured_domains(tmp_path):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    body = "".join(
        f'<{kind}><origin xyz="{x} 0 0"/><geometry>'
        f'<mesh filename="{kind}.obj" scale="2 1 1"/></geometry></{kind}>'
        for kind in ("visual", "collision")
        for x in (-1, 1)
    )
    registry, store, version = registered(
        tmp_path,
        rectangle(-0.1, -0.1, 0.1, 0.1),
        urdf=f'<robot name="fixture"><link name="body">{body}</link></robot>',
    )
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    assert len(surface.polygons) == 2
    assert sorted(round(min(x for x, y in p[0]), 3) for p in surface.polygons) == [-1.2, 0.8]
    assert {entry[3] for entry in surface.face_sources} == {0, 1}


@pytest.mark.parametrize(
    "case,error",
    [
        ("disjoint", "support_surface_not_found"),
        ("mixed_intersection", "invalid intersection"),
        ("ambiguous", "ambiguous_support_surface"),
        ("nonplanar", "support_surface_not_found"),
    ],
)
def test_surface_selection_does_not_repair_or_guess(tmp_path, case, error):
    from self_improving.harness.x2env.measured_support import measure_support_surfaces

    visual = rectangle(-0.5, -0.5, 0.5, 0.5)
    collision = rectangle(1, -0.5, 2, 0.5)
    if case == "mixed_intersection":
        visual = trimesh.util.concatenate([visual, rectangle(2, -0.5, 3, 0.5)])
        collision = trimesh.util.concatenate(
            [rectangle(-0.4, -0.4, 0.4, 0.4), rectangle(3, -0.4, 4, 0.4)]
        )
    if case == "ambiguous":
        collision = trimesh.util.concatenate(
            [
                rectangle(-0.5, -0.5, 0.5, 0.5, 0.1 - 8e-6),
                rectangle(-0.5, -0.5, 0.5, 0.5, 0.1 + 8e-6),
            ]
        )
    if case == "nonplanar":
        visual = trimesh.Trimesh(
            vertices=[[0, 0, 0], [100, 0, 2e-5], [0, 100, 0]], faces=[[0, 1, 2]], process=False
        )
        collision = visual
    registry, store, version = registered(tmp_path, visual, collision)
    with pytest.raises(ValueError, match=error):
        measure_support_surfaces(version.version_sha256, registry=registry, store=store)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        (),
        ((),),
        ((((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),),),
        ((((0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (0.0, 0.0)),),),
        "overlap",
    ],
)
def test_invalid_surface_domains_are_not_repaired(tmp_path, bad):
    from self_improving.harness.x2env.measured_support import (
        MeasuredSupportSurface,
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    if bad is None:
        surface = None
    else:
        values = surface.model_dump()
        values["polygons"] = surface.polygons * 2 if bad == "overlap" else bad
        surface = MeasuredSupportSurface.model_validate(values)
    with pytest.raises(ValueError, match="invalid_support_surface"):
        evaluate_support_footprint(
            surface,
            source_geometry_parts=parts(rectangle(-0.1, -0.1, 0.1, 0.1)),
            source_pose=pose(),
            target_pose=pose(),
        )


@pytest.mark.parametrize(
    "case,error",
    [
        ("zeroquaternion", "invalid_support_pose"),
        ("missingpart", "missing footprint"),
        ("vertical", "empty footprint"),
    ],
)
def test_empty_projection_and_missing_pose_evidence_do_not_pass(tmp_path, case, error):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    geometry = parts(rectangle(-0.1, -0.1, 0.1, 0.1))
    source = pose()
    if case == "zeroquaternion":
        source["orientation_wxyz"] = [0.0, 0.0, 0.0, 0.0]
    if case == "missingpart":
        geometry.pop("collision")
    if case == "vertical":
        geometry = parts(
            trimesh.Trimesh(
                vertices=[[0, 0, 0], [0, 1, 0], [0, 0, 1]], faces=[[0, 1, 2]], process=False
            )
        )
    with pytest.raises(ValueError, match=error):
        evaluate_support_footprint(
            surface, source_geometry_parts=geometry, source_pose=source, target_pose=pose()
        )


def test_finite_pose_transform_overflow_is_rejected_before_polygon_operations(tmp_path):
    from self_improving.harness.x2env.measured_support import (
        evaluate_support_footprint,
        measure_support_surfaces,
    )

    registry, store, version = registered(tmp_path, rectangle(-0.5, -0.5, 0.5, 0.5))
    surface = measure_support_surfaces(version.version_sha256, registry=registry, store=store)[0]
    with pytest.raises(ValueError, match="invalid_support_mesh: nonfinite transformed footprint"):
        evaluate_support_footprint(
            surface,
            source_geometry_parts=parts(rectangle(-0.1, -0.1, 0.1, 0.1)),
            source_pose=pose(1e308),
            target_pose=pose(-1e308),
        )
