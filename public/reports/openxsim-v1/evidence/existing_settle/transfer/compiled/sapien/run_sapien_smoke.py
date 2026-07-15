#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
import sapien

parser = argparse.ArgumentParser()
parser.add_argument("--spec", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
contract = spec.get("task_contract", {})
contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
action_interface = str(contract.get("action", {}).get("interface", ""))
success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
supported_success = {"state_trace_available", "object_below", "settled"}

if hasattr(sapien, "Engine"):
    engine = sapien.Engine()
    scene = engine.create_scene()
else:
    scene = sapien.Scene()
scene.set_timestep(0.01)
if hasattr(scene, "set_gravity"):
    scene.set_gravity(spec["gravity_mps2"])
scene.add_ground(0.0)
actors = {}
for item in spec["objects"]:
    if item["kind"] == "missing":
        raise RuntimeError(item["blocker"])
    if item["kind"] == "urdf":
        loader = scene.create_urdf_loader()
        loader.fix_root_link = bool(item["static"])
        actor = loader.load(item["path"])
    else:
        builder = scene.create_actor_builder()
        if item["kind"] == "box":
            half = [item["half_size_m"][i] * item["scale"][i] for i in range(3)]
            builder.add_box_collision(half_size=half)
            builder.add_box_visual(half_size=half, material=item["color_rgb"])
        else:
            builder.add_visual_from_file(item["path"], scale=item["scale"])
            builder.add_multiple_convex_collisions_from_file(item["path"], scale=item["scale"])
        actor = builder.build_static(name=item["name"]) if item["static"] else builder.build(name=item["name"])
    actor.set_pose(sapien.Pose(item["position"], item["orientation_wxyz"]))
    actors[item["name"]] = actor
initial = {name: actor.get_pose().p.tolist() for name, actor in actors.items()}

def snapshot(step):
    contacts = []
    if hasattr(scene, "get_contacts"):
        try:
            for contact in scene.get_contacts():
                names = sorted(filter(None, [getattr(getattr(contact, "bodies", [None, None])[0], "name", None), getattr(getattr(contact, "bodies", [None, None])[1], "name", None)]))
                if names:
                    contacts.append(":".join(names))
        except Exception:
            contacts = []
    return {"step": step, "objects": {name: [float(value) for value in actor.get_pose().p] for name, actor in actors.items()}, "contacts": sorted(set(contacts))}

trajectory = [snapshot(0)]
for step in range(1, args.steps + 1):
    scene.step()
    trajectory.append(snapshot(step))
final = {name: actor.get_pose().p.tolist() for name, actor in actors.items()}
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "sapien",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "object_positions_initial": initial,
    "object_positions_final": final,
    "action_interface": action_interface,
    "action_interface_bound": action_interface in {"none", "zero_action"},
    "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
    "task_contract_hash": contract_hash,
    "observation_keys": ["contact", "object_pose"],
    "trajectory_mode": "zero_action_physics_rollout",
    "trajectory": trajectory,
}
Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
