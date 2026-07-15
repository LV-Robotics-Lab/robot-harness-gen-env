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
contract_hash = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
action_interface = str(contract.get("action", {}).get("interface", ""))
success_types = {str(item.get("type", "")) for item in contract.get("success", [])}
supported_success = {"state_trace_available", "object_below", "settled"}
model = mujoco.MjModel.from_xml_path(args.model)
data = mujoco.MjData(model)
mujoco.mj_resetData(model, data)
mujoco.mj_forward(model, data)
initial = data.qpos.tolist()
body_ids = [index for index in range(1, model.nbody) if model.body_jntnum[index] > 0]

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
        names = sorted(filter(None, [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body1), mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body2)]))
        if names:
            contacts.append(":".join(names))
    return {"step": step, "objects": objects, "contacts": sorted(set(contacts))}

trajectory = [snapshot(0)]
for step in range(1, args.steps + 1):
    mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)
    trajectory.append(snapshot(step))
payload = {
    "schema": "agenticsim.runtime_evidence.v1",
    "backend": "mujoco",
    "reset_ok": True,
    "step_ok": True,
    "steps": args.steps,
    "qpos_initial": initial,
    "qpos_final": data.qpos.tolist(),
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
