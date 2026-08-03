from pathlib import Path
import pytest
from agenticsim.openxsim.env_gen import import_env_gen

FIX = (
    Path(__file__).parent / "fixtures" / "env_gen" / "can_on_plate.resolved_scene.json"
)


def test_rigid_scene_maps_to_valid_ir():
    pkg = import_env_gen(FIX)
    pkg.validate()
    assert pkg.package_id.startswith("place_a_can")
    assert len(pkg.env.objects) == 2
    ids = {o.instance_id for o in pkg.env.objects}
    assert ids == {"can_1", "plate_1"}
    can = next(o for o in pkg.env.objects if o.instance_id == "can_1")
    assert can.pose.position[0] == pytest.approx(-0.125948517)
    assert can.static is False
    assert can.asset_id.startswith("asset_071_can")
    asset = next(a for a in pkg.assets if a.asset_id == can.asset_id)
    rep = asset.representations[0]
    assert rep.backend == "sapien" and rep.format in {
        "glb",
        "obj",
        "dae",
        "stl",
        "urdf",
    }
    assert pkg.task.instruction
    assert any(c.get("type") == "unbound" for c in pkg.task.success)


def test_fidelity_unknown_physics_and_provenance_and_relations():
    pkg = import_env_gen(FIX)
    asset = pkg.assets[0]
    # 未知物理显式标记，不编造
    assert asset.physical["mass_kg"] == {"status": "unknown"}
    assert asset.physical["inertia"] == {"status": "unknown"}
    assert asset.physical["dimensions_m"] is not None
    # 血缘入 IR
    assert asset.source["kind"] == "env_gen"
    assert asset.source["asset_provenance"] == "robotwin_catalog"
    # relations 作为数据带入 env.metadata（本任务不合成 task）
    rels = pkg.env.metadata["relations"]
    assert {r["relation"] for r in rels} == {"on_table", "on_top_of"}
    # env-gen 溯源哈希带入
    assert pkg.env.metadata["source_scene_spec_sha256"]
    assert pkg.env.metadata["compiler_version"].startswith("scene_gen")


def test_rigid_asset_has_empty_articulation():
    pkg = import_env_gen(FIX)
    assert all(a.articulation == {} for a in pkg.assets)  # can/plate 无关节


def test_missing_asset_file_raises():
    from agenticsim.openxsim.importers import EnvironmentImportError

    bad = FIX.with_name("missing_asset.resolved_scene.json")
    with pytest.raises(EnvironmentImportError):
        import_env_gen(bad)


def test_non_env_gen_input_raises(tmp_path):
    from agenticsim.openxsim.importers import EnvironmentImportError

    p = tmp_path / "other.json"
    p.write_text('{"compiler_version": "something_else", "objects": []}')
    with pytest.raises(EnvironmentImportError):
        import_env_gen(p)


def test_malformed_json_raises(tmp_path):
    from agenticsim.openxsim.importers import EnvironmentImportError

    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(EnvironmentImportError):
        import_env_gen(p)
