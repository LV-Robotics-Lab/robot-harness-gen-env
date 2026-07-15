#!/usr/bin/env python3
import argparse, hashlib, json, os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--package", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
from isaacsim import SimulationApp
app = SimulationApp({"headless": True, "hide_ui": True, "fast_shutdown": True, "active_gpu": 0, "physics_gpu": 0, "multi_gpu": False, "max_gpu_count": 1})
try:
    import numpy as np
    from isaacsim.core.api import World
    from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
    from isaacsim.core.prims import SingleXFormPrim
    from isaacsim.core.utils.stage import add_reference_to_stage

    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    contract = package["task"]
    semantic_contract = {key: contract[key] for key in ("reset", "action", "observation", "success", "termination")}
    contract_hash = hashlib.sha256(json.dumps(semantic_contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_interface = str(contract.get("action", {}).get("interface", ""))
    success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
    supported_success = {"state_trace_available", "object_below", "settled"}
    asset_map = {item["asset_id"]: item for item in package["assets"]}
    world = World(physics_dt=0.01, rendering_dt=0.01, stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    actors = {}
    dynamic_names = []
    external_static_names = []
    for item in package["env"]["objects"]:
        asset = asset_map[item["asset_id"]]
        primitive = next((rep for rep in asset["representations"] if rep["format"] == "primitive_box"), None)
        if primitive is None:
            representation = next(
                (
                    rep
                    for rep in asset["representations"]
                    if rep["format"] in {"usd", "usda", "usdc"} and Path(rep["uri"]).is_file()
                ),
                None,
            )
            if representation is None:
                raise RuntimeError(f"Isaac smoke needs primitive_box or USD for {item['instance_id']}")
            name = item["instance_id"]
            prim_path = f"/World/OpenXSim/{name}"
            add_reference_to_stage(usd_path=representation["uri"], prim_path=prim_path)
            actors[name] = SingleXFormPrim(
                prim_path=prim_path,
                name=name,
                position=np.asarray(item["pose"]["position"]),
                orientation=np.asarray(item["pose"]["orientation_wxyz"]),
                scale=np.asarray(item.get("scale", [1, 1, 1])),
            )
            external_static_names.append(name)
            continue
        half = primitive["metadata"].get("half_size_m", [0.025, 0.025, 0.025])
        scale = np.asarray([2 * half[i] * item.get("scale", [1, 1, 1])[i] for i in range(3)])
        color = np.asarray(primitive["metadata"].get("color_rgb", [0.8, 0.8, 0.8]))
        position = np.asarray(item["pose"]["position"])
        orientation = np.asarray(item["pose"]["orientation_wxyz"])
        name = item["instance_id"]
        cls = FixedCuboid if item.get("static", False) else DynamicCuboid
        kwargs = {"prim_path": f"/World/OpenXSim/{name}", "name": name, "position": position, "orientation": orientation, "scale": scale, "color": color}
        if cls is DynamicCuboid:
            kwargs["mass"] = float(asset.get("physical", {}).get("mass_kg", 0.1))
            dynamic_names.append(name)
        actors[name] = world.scene.add(cls(**kwargs))
    world.reset()
    for name in dynamic_names:
        actors[name].set_linear_velocity(np.zeros(3))
        actors[name].set_angular_velocity(np.zeros(3))

    def snapshot(step):
        return {"step": step, "objects": {name: [float(value) for value in actor.get_world_pose()[0]] for name, actor in actors.items()}, "contacts": []}

    trajectory = [snapshot(0)]
    for step in range(1, args.steps + 1):
        world.step(render=False)
        trajectory.append(snapshot(step))
    runtime_stage = Path(args.output).with_name("runtime_scene.usda")
    world.stage.GetRootLayer().Export(str(runtime_stage))
    payload = {
        "schema": "agenticsim.runtime_evidence.v1",
        "backend": "isaacsim",
        "reset_ok": True,
        "step_ok": True,
        "steps": args.steps,
        "action_interface": action_interface,
        "action_interface_bound": action_interface in {"none", "zero_action"},
        "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
        "task_contract_hash": contract_hash,
        "observation_keys": ["contact", "object_pose"],
        "trajectory_mode": "zero_action_physics_rollout",
        "trajectory": trajectory,
        "runtime_stage": str(runtime_stage),
        "external_asset_static_references": external_static_names,
    }
    Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
finally:
    app.close()
