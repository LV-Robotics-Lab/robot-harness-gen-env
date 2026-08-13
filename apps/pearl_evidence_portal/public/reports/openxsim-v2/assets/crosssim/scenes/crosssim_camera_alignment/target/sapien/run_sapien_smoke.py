#!/usr/bin/env python3
import argparse, hashlib, json, math
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
    renderer = sapien.SapienRenderer()
    engine.set_renderer(renderer)
    scene = engine.create_scene()
else:
    scene = sapien.Scene()
scene.set_timestep(0.01)
if hasattr(scene, "set_gravity"):
    scene.set_gravity(spec["gravity_mps2"])
scene.add_ground(0.0)
if hasattr(scene, "set_ambient_light"):
    scene.set_ambient_light([0.35, 0.35, 0.35])
if hasattr(scene, "add_directional_light"):
    scene.add_directional_light([0.2, 0.3, -1.0], [1.2, 1.2, 1.2], shadow=False)
actors = {}
joint_names_by_actor = {}

def set_actor_pose(actor, pose):
    if hasattr(actor, "set_root_pose"):
        actor.set_root_pose(pose)
    else:
        actor.set_pose(pose)

def get_actor_pose(actor):
    if hasattr(actor, "get_root_pose"):
        return actor.get_root_pose()
    return actor.get_pose()

for item in spec["objects"]:
    if item["kind"] == "missing":
        raise RuntimeError(item["blocker"])
    if item["kind"] == "urdf":
        loader = scene.create_urdf_loader()
        loader.fix_root_link = bool(item["static"])
        actor = loader.load(item["path"])
        joint_names_by_actor[item["name"]] = [
            str(joint.get("id"))
            for joint in (item.get("articulation") or {}).get("joints", [])
            if str(joint.get("type")) != "fixed"
        ]
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
    set_actor_pose(actor, sapien.Pose(item["position"], item["orientation_wxyz"]))
    actors[item["name"]] = actor
initial = {name: get_actor_pose(actor).p.tolist() for name, actor in actors.items()}

cameras = {}

def camera_pose(sensor):
    position = [float(value) for value in sensor.get("position", [0.0, -1.0, 1.0])]
    if sensor.get("look_at") is None:
        return sapien.Pose(position, sensor.get("orientation_wxyz", [1.0, 0.0, 0.0, 0.0]))
    target = [float(value) for value in sensor["look_at"]]
    up_hint = [float(value) for value in sensor.get("up", [0.0, 0.0, 1.0])]
    def normalize(vector):
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]
    def cross(first, second):
        return [first[1] * second[2] - first[2] * second[1], first[2] * second[0] - first[0] * second[2], first[0] * second[1] - first[1] * second[0]]
    forward = normalize([target[index] - position[index] for index in range(3)])
    left = normalize(cross(up_hint, forward))
    camera_up = normalize(cross(forward, left))
    matrix = [
        [forward[0], left[0], camera_up[0]],
        [forward[1], left[1], camera_up[1]],
        [forward[2], left[2], camera_up[2]],
    ]
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = [0.25 * scale, (matrix[2][1] - matrix[1][2]) / scale, (matrix[0][2] - matrix[2][0]) / scale, (matrix[1][0] - matrix[0][1]) / scale]
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
        quaternion = [(matrix[2][1] - matrix[1][2]) / scale, 0.25 * scale, (matrix[0][1] + matrix[1][0]) / scale, (matrix[0][2] + matrix[2][0]) / scale]
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
        quaternion = [(matrix[0][2] - matrix[2][0]) / scale, (matrix[0][1] + matrix[1][0]) / scale, 0.25 * scale, (matrix[1][2] + matrix[2][1]) / scale]
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
        quaternion = [(matrix[1][0] - matrix[0][1]) / scale, (matrix[0][2] + matrix[2][0]) / scale, (matrix[1][2] + matrix[2][1]) / scale, 0.25 * scale]
    return sapien.Pose(position, quaternion)

for sensor in spec.get("sensors", []):
    if str(sensor.get("type", "")) not in {"camera", "rgb", "rgb_camera"}:
        continue
    name = str(sensor.get("id") or "camera")
    camera = scene.add_camera(
        name,
        int(sensor.get("width", 640)),
        int(sensor.get("height", 480)),
        float(sensor.get("fov_y_rad", float(sensor.get("fov_y_deg", 60.0)) * 3.141592653589793 / 180.0)),
        float(sensor.get("near_m", 0.01)),
        float(sensor.get("far_m", 100.0)),
    )
    camera.set_pose(camera_pose(sensor))
    cameras[name] = (camera, sensor)

def snapshot(step):
    contacts = []
    if hasattr(scene, "get_contacts"):
        try:
            for contact in scene.get_contacts():
                raw_bodies = list(getattr(contact, "bodies", [None, None]))[:2]
                names = []
                for body in raw_bodies:
                    name = getattr(body, "name", None)
                    getter = getattr(body, "get_name", None)
                    if not name and callable(getter):
                        name = getter()
                    if not name:
                        entity = getattr(body, "entity", None)
                        name = getattr(entity, "name", None)
                    if name:
                        names.append(str(name))
                names = sorted(names)
                if names:
                    contacts.append(":".join(names))
        except Exception:
            contacts = []
    return {"step": step, "objects": {name: [float(value) for value in get_actor_pose(actor).p] for name, actor in actors.items()}, "contacts": sorted(set(contacts))}

def joint_snapshot(step):
    values = {}
    for actor_name, names in joint_names_by_actor.items():
        actor = actors[actor_name]
        qpos = actor.get_qpos() if hasattr(actor, "get_qpos") else []
        for index, name in enumerate(names):
            if index < len(qpos):
                values[name] = float(qpos[index])
    return {"step": step, "joints": values}

trajectory = [snapshot(0)]
joint_trajectory = [joint_snapshot(0)]
camera_evidence = []
if cameras:
    try:
        import numpy as np
        from PIL import Image
        scene.update_render()
        for name, (camera, sensor) in cameras.items():
            camera.take_picture()
            color = camera.get_picture("Color")[..., :3]
            path = Path(args.output).with_name(f"{name}_rgb.png")
            Image.fromarray((np.clip(color, 0.0, 1.0) * 255).astype(np.uint8)).save(path)
            camera_evidence.append({"id": name, "status": "pass", "width": int(sensor.get("width", 640)), "height": int(sensor.get("height", 480)), "fov_y_deg": float(sensor.get("fov_y_deg", 60.0)), "rgb_path": str(path)})
    except Exception as error:
        camera_evidence.append({"id": "camera_runtime", "status": "fail", "error": repr(error)})
for step in range(1, args.steps + 1):
    scene.step()
    trajectory.append(snapshot(step))
    joint_trajectory.append(joint_snapshot(step))
final = {name: get_actor_pose(actor).p.tolist() for name, actor in actors.items()}
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "sapien",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "timestep_s": 0.01,
    "object_positions_initial": initial,
    "object_positions_final": final,
    "action_interface": action_interface,
    "action_interface_bound": action_interface in {"none", "zero_action"},
    "success_evaluator_bound": bool(success_types) and success_types <= supported_success,
    "task_contract_hash": contract_hash,
    "observation_keys": ["contact", "object_pose"],
    "trajectory_mode": "zero_action_physics_rollout",
    "trajectory": trajectory,
    "joint_trajectory": joint_trajectory,
    "camera_evidence": camera_evidence,
}
Path(args.output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
