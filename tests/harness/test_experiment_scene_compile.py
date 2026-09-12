import json

import pytest

from self_improving.harness.artifacts import LocalArtifactStore
from self_improving.harness.experiment_assets import AssetRequest, acquire
from self_improving.harness.experiment_scene_compile import compile_scene
from self_improving.harness.gujie_scene_adapter import read_gujie_tabletop_scene


def test_static_compile_and_layout_revision_bind_real_geometry(tmp_path):
    asset = acquire(AssetRequest(source="generated", category="block"), tmp_path / "assets")
    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "input.json"
    source.write_text('{"text":"a block on the table"}')
    ref = store.put_file(
        source, name="input.json", media_type="application/json", schema_version=None
    )
    intent = {
        "category": "block",
        "color": None,
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": ref.sha256,
        "unknowns": [],
    }
    first = compile_scene(store, ref, intent, asset)
    scene = read_gujie_tabletop_scene(store, first.scene_ref)
    assert abs(scene.position_m[2] - 0.401) < 1e-8
    second = compile_scene(store, ref, {**intent, "xy_m": [0.1, 0.0], "yaw_deg": 25.0}, asset)
    assert first.scene_ref.sha256 != second.scene_ref.sha256
    assert first.representation_ref == second.representation_ref
    representation = json.loads(store.resolve(first.representation_ref).path.read_bytes())
    assert representation["root_logical_path"] == "asset/urdf/asset.urdf"
    assert "asset/physics.json" in {m["logical_path"] for m in representation["members"]}
    scaled = compile_scene(store, ref, {**intent, "dimensions_m": [0.11, 0.11, 0.08]}, asset)
    scaled_scene = read_gujie_tabletop_scene(store, scaled.scene_ref)
    assert abs(max(v[2] for v in scaled_scene.local_vertices_m) - 0.08) < 1e-7
    with pytest.raises(ValueError, match="anisotropic"):
        compile_scene(store, ref, {**intent, "dimensions_m": [0.1, 0.1, 0.1]}, asset)
    with pytest.raises(ValueError, match="footprint"):
        compile_scene(store, ref, {**intent, "xy_m": [0.59, 0.0]}, asset)


def test_intent_unknown_fields_cannot_carry_hidden_controls(tmp_path):
    asset = acquire(AssetRequest(source="generated", category="block"), tmp_path / "assets")
    store = LocalArtifactStore(tmp_path / "cas")
    source = tmp_path / "input.txt"
    source.write_text("a block")
    ref = store.put_file(source, name="input.txt", media_type="text/plain", schema_version=None)
    intent = {
        "category": "block",
        "color": None,
        "source": "generated",
        "selection": "exact",
        "dimensions_m": None,
        "xy_m": [0.0, 0.0],
        "yaw_deg": 0.0,
        "support": "table",
        "object_count": 1,
        "input_sha256": ref.sha256,
        "unknowns": [],
        "is_static": True,
    }
    with pytest.raises(ValueError, match="intent fields"):
        compile_scene(store, ref, intent, asset)
