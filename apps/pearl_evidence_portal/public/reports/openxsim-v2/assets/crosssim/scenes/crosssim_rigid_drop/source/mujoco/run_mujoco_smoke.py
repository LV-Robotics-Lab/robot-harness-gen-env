#!/usr/bin/env python3
import argparse, hashlib, json, xml.etree.ElementTree as ET
from pathlib import Path
import mujoco

parser = argparse.ArgumentParser()
parser.add_argument("--model", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--steps", type=int, default=20)
args = parser.parse_args()
root = ET.parse(args.model).getroot()
custom = root.find("./custom/text[@name='agenticsim_task_contract']")
contract = json.loads(custom.get("data")) if custom is not None else {}
objects_custom = root.find("./custom/text[@name='agenticsim_object_ids']")
object_names = json.loads(objects_custom.get("data")) if objects_custom is not None else []
sensors_custom = root.find("./custom/text[@name='agenticsim_sensors']")
sensors = json.loads(sensors_custom.get("data")) if sensors_custom is not None else []
contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
action_interface = str(contract.get("action", {}).get("interface", ""))
success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
supported_success = {"state_trace_available", "object_below", "settled"}
model = mujoco.MjModel.from_xml_path(args.model)
data = mujoco.MjData(model)
mujoco.mj_resetData(model, data)
mujoco.mj_forward(model, data)
initial = data.qpos.tolist()
body_ids = []
for name in object_names:
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if body_id >= 0:
        body_ids.append(body_id)
joint_ids = [
    joint_id
    for joint_id in range(model.njnt)
    if model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE
    and mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
]

def snapshot(step):
    objects = {}
    for body_id in body_ids:
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        objects[name] = [float(value) for value in data.xpos[body_id]]
    contacts = []
    for index in range(data.ncon):
        contact = data.contact[index]
        body1 = model.geom_bodyid[contact.geom1]
        body2 = model.geom_bodyid[contact.geom2]
        names = sorted(filter(None, ["ground" if body1 == 0 else mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body1), "ground" if body2 == 0 else mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body2)]))
        if names:
            contacts.append(":".join(names))
    return {"step": step, "objects": objects, "contacts": sorted(set(contacts))}

def joint_snapshot(step):
    return {
        "step": step,
        "joints": {
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id): float(data.qpos[model.jnt_qposadr[joint_id]])
            for joint_id in joint_ids
        },
    }

initial_snapshot = snapshot(0)
initial_snapshot["contacts"] = []
trajectory = [initial_snapshot]
joint_trajectory = [joint_snapshot(0)]
camera_evidence = []
for sensor in sensors:
    if str(sensor.get("type", "")) not in {"camera", "rgb", "rgb_camera"}:
        continue
    camera_name = str(sensor.get("id") or "camera")
    width = int(sensor.get("width", 640))
    height = int(sensor.get("height", 480))
    camera_path = Path(args.output).with_name(f"{camera_name}_rgb.png")
    try:
        from PIL import Image
        with mujoco.Renderer(model, height=height, width=width) as renderer:
            renderer.update_scene(data, camera=camera_name)
            Image.fromarray(renderer.render()).save(camera_path)
        camera_evidence.append({"id": camera_name, "status": "pass", "width": width, "height": height, "fov_y_deg": float(sensor.get("fov_y_deg", sensor.get("fovy", 60.0))), "rgb_path": str(camera_path)})
    except Exception as error:
        camera_evidence.append({"id": camera_name, "status": "fail", "error": repr(error)})
for step in range(1, args.steps + 1):
    mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)
    trajectory.append(snapshot(step))
    joint_trajectory.append(joint_snapshot(step))
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "mujoco",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "timestep_s": 0.01,
    "qpos_initial": initial,
    "qpos_final": data.qpos.tolist(),
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
