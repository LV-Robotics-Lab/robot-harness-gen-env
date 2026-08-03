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
