"""Frozen programmatic physics, separately from managed Codex visual advisory.

Gujie eb0b710 criterion semantics and Harness c0236bd dual-dt mathematics;
the approved physics-assertions-v1.json is the numerical authority. No controller.
"""

import ast
import hashlib
import json
import math
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import model_validator
from scipy.spatial.transform import Rotation

from .genesis_child import audit_geometry
from .genesis_runtime import RuntimeMember, RuntimeModel, RuntimeScene, _member, _verify

ASSERTIONS_SHA256 = "39d83385bffc3c06a4e8ef2433f1ec58a043168a3979320e7b1b924fd28561ff"


class ProfileEvidence(RuntimeModel):
    root: str
    files: tuple[RuntimeMember, ...]

    @model_validator(mode="after")
    def absolute_root(self):
        if not Path(self.root).is_absolute():
            raise ValueError("profile root must be absolute")
        return self


def _assertions():
    raw = (
        Path(__file__).parents[2] / "golden_e2e_progress/physics-assertions-v1.json"
    ).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ASSERTIONS_SHA256:
        raise ValueError("frozen physical assertions changed")
    return json.loads(raw)


def _vector(value, size=3):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != size
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
    ):
        raise ValueError("invalid finite numeric trace vector")
    result = np.asarray(value)
    if size == 4 and abs(float(result @ result) - 1) > 1e-5:
        raise ValueError("nonunit quaternion")
    return result


def _rotation(q):
    q = _vector(q, 4)
    return Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()


def _angle(a, b):
    cosine = abs(float(np.dot(a, b))) / (np.linalg.norm(a) * np.linalg.norm(b))
    return math.degrees(2 * math.acos(min(1.0, cosine)))


def evaluate_physics(scene, baseline_rows, half_dt_rows, loaded_by_profile):
    """Pure analytical seam. Even passed traces do not prove simulator execution."""
    report = {
        "physical_status": "not_run",
        "authority": "trace_consistency_only",
        "simulator_execution_proven": False,
        "checks": [],
        "assertions_sha256": ASSERTIONS_SHA256,
    }
    try:
        scene = RuntimeScene.model_validate(scene)
        cfg = _assertions()
        entities = {e.id: e for e in scene.entities}
        dynamic = {e.id for e in scene.entities if e.kind == "rigid"}
        targets = {
            name: [r.target for r in scene.relations if r.source == name and r.relation == "on"]
            for name in dynamic
        }
        if not dynamic or any(len(v) != 1 for v in targets.values()):
            return {**report, "error_code": "unsupported_physical_profile"}
        ends = {}
        for profile, rows in [("baseline", baseline_rows), ("half_dt", half_dt_rows)]:
            dt = cfg["replay"][profile]["dt_s"]
            steps = cfg["replay"][profile]["steps"]
            if len(rows) != steps + 1:
                return {**report, "error_code": "incomplete_dual_profile"}
            loaded = loaded_by_profile[profile]
            if set(loaded) != set(entities):
                raise ValueError("loaded entity set differs")
            for name, e in entities.items():
                item = loaded[name]
                if (
                    np.linalg.norm(_vector(item["position_m"]) - e.position_m) > 1e-6
                    or _angle(_vector(item["orientation_wxyz"], 4), e.orientation_wxyz) > 1e-4
                    or item["collision_shapes"] < 1
                    or item["fixed"] != (e.kind == "structural_box")
                ):
                    raise ValueError("actual loaded state differs")
                if e.kind == "rigid" and (
                    item["dofs"] != 6
                    or item["mass_kg"] <= 0
                    or not math.isclose(item["mass_kg"], item["authored_mass_kg"], rel_tol=1e-4)
                    or not np.allclose(item["friction"], item["supplied_friction"], atol=1e-6)
                ):
                    raise ValueError("actual loaded physics differs")
                if e.kind == "structural_box" and not np.allclose(
                    item["friction"], e.friction, atol=1e-6
                ):
                    raise ValueError("loaded support friction differs")
            for i, row in enumerate(rows):
                if (
                    type(row["step"]) is not int
                    or row["step"] != i
                    or type(row["time_s"]) not in (int, float)
                    or not math.isfinite(row["time_s"])
                    or abs(row["time_s"] - i * dt) > 1e-9
                ):
                    raise ValueError("nonsequential trajectory")
                if row["contact_phase"] != ("solved_step" if i else "initial_detection"):
                    raise ValueError("invalid contact phase")
                if set(row["objects"]) != set(entities) | {"ground"}:
                    raise ValueError("incomplete object trace")
                for name, state in row["objects"].items():
                    p = _vector(state["position"])
                    q = _vector(state["orientation_wxyz"], 4)
                    velocity = _vector(state["velocity"])
                    angular = _vector(state["angular_velocity"])
                    if name not in dynamic or i == 0:
                        e = entities.get(name)
                        expected_p = e.position_m if e else (0.0, 0.0, 0.0)
                        expected_q = e.orientation_wxyz if e else (1.0, 0.0, 0.0, 0.0)
                        if (
                            np.linalg.norm(p - expected_p) > (1e-6 if name in dynamic else 1e-7)
                            or _angle(q, expected_q) > (1e-4 if name in dynamic else 1e-5)
                            or np.linalg.norm(velocity) > 1e-7
                            or np.linalg.norm(angular) > 1e-7
                        ):
                            raise ValueError("initial state/fixed root differs")
                totals = {n: np.zeros(3) for n in dynamic}
                for c in row["contacts"]:
                    if (
                        c["a"] == c["b"]
                        or c["a"] not in row["objects"]
                        or c["b"] not in row["objects"]
                    ):
                        raise ValueError("invalid contact pair")
                    _vector(c["position"])
                    if abs(np.linalg.norm(_vector(c["normal"])) - 1) > 1e-4:
                        raise ValueError("invalid contact normal")
                    if (
                        type(c["penetration"]) not in (int, float)
                        or not math.isfinite(c["penetration"])
                        or c["penetration"] < 0
                    ):
                        raise ValueError("invalid penetration")
                    if not i:
                        if c["force_a"] is not None or c["force_b"] is not None:
                            raise ValueError("initial force unavailable")
                    else:
                        a = _vector(c["force_a"])
                        b = _vector(c["force_b"])
                        if not np.allclose(a, -b, atol=1e-7, rtol=1e-6):
                            raise ValueError("contact forces are not opposite")
                        if c["a"] in totals:
                            totals[c["a"]] += a
                        if c["b"] in totals:
                            totals[c["b"]] += b
                if set(row["net_contact_forces"]) != dynamic:
                    raise ValueError("missing net force evidence")
                for name, total in totals.items():
                    actual = row["net_contact_forces"][name]
                    if (not i and actual is not None) or (
                        i and not np.allclose(total, _vector(actual), atol=1e-5, rtol=1e-4)
                    ):
                        raise ValueError("contact sum differs from measured net force")
            ends[profile] = {}
            for name in sorted(dynamic):
                item = loaded[name]
                if item["geometry_basis"] != "actual_genesis_vertices_inverse_world_pose":
                    raise ValueError("missing actual loaded geometry")
                if any(
                    len(item[g]) < 4
                    for g in ["local_visual_vertices_m", "local_collision_vertices_m"]
                ):
                    raise ValueError("incomplete actual footprint")
                vertices = np.array(
                    [
                        _vector(v)
                        for group in ["local_visual_vertices_m", "local_collision_vertices_m"]
                        for v in item[group]
                    ]
                )
                if len(vertices) < 4:
                    raise ValueError("incomplete actual footprint")
                target = entities[targets[name][0]]
                target_rotation = _rotation(target.orientation_wxyz)
                if not np.allclose(target_rotation[:, 2], [0, 0, 1], atol=1e-6):
                    return {**report, "error_code": "unsupported_tilted_support_profile"}
                radius = max(np.linalg.norm(v) for v in vertices)
                # Convex extreme vertices preserve every linear min/max footprint exactly.
                if len(vertices) > 64:
                    from scipy.spatial import ConvexHull

                    vertices = vertices[ConvexHull(vertices).vertices]
                window = rows[-round(cfg["stability"]["window_s"] / dt) :]
                states = [r["objects"][name] for r in window]
                positions = np.array([s["position"] for s in states])
                center = positions.mean(0)
                effective = [
                    max(
                        np.linalg.norm(s["velocity"]),
                        radius * np.linalg.norm(s["angular_velocity"]),
                    )
                    for s in states
                ]
                longest = current = 0
                for speed in effective:
                    current = current + 1 if speed >= 1.5 * 9.81 * dt else 0
                    longest = max(longest, current)
                touching = supported = 0
                for row in window:
                    contacts = [c for c in row["contacts"] if name in (c["a"], c["b"])]
                    touching += bool(contacts)
                    upward = sum(
                        c["force_a" if c["a"] == name else "force_b"][2]
                        for c in contacts
                        if target.id in (c["a"], c["b"])
                    )
                    supported += upward > cfg["support"]["effective_upward_force_N_exclusive_min"]
                margin = minimum_z = math.inf
                for row in rows:
                    state = row["objects"][name]
                    world = vertices @ _rotation(state["orientation_wxyz"]).T + state["position"]
                    local = (world - target.position_m) @ target_rotation
                    margin = min(
                        margin,
                        float(np.min(np.asarray(target.size_m)[:2] / 2 - np.abs(local[:, :2]))),
                    )
                    minimum_z = min(minimum_z, float(world[:, 2].min()))
                stability = cfg["stability"]
                duration = stability["window_s"]
                measures = {
                    "translation_m": (
                        float(np.linalg.norm(positions - positions[0], axis=1).max()),
                        stability["translation_m_max"],
                        "le",
                    ),
                    "rotation_deg": (
                        max(
                            _angle(s["orientation_wxyz"], states[0]["orientation_wxyz"])
                            for s in states
                        ),
                        stability["rotation_deg_max"],
                        "le",
                    ),
                    "drift_rate_mps": (
                        float(np.linalg.norm(positions[-1] - positions[0]) / duration),
                        stability["drift_m_per_s_max"],
                        "le",
                    ),
                    "rotation_rate_dps": (
                        _angle(states[-1]["orientation_wxyz"], states[0]["orientation_wxyz"])
                        / duration,
                        stability["rotation_rate_deg_per_s_max"],
                        "le",
                    ),
                    "excursion_m": (
                        float(np.linalg.norm(positions - center, axis=1).max()),
                        stability["excursion_m_max"],
                        "le",
                    ),
                    "speed_run_steps": (
                        longest,
                        stability["sustained_effective_speed"]["consecutive_rows_exclusive_max"],
                        "lt",
                    ),
                    "contact_dropout_max": (
                        1 - touching / len(window),
                        cfg["support"]["tail_contact_dropout_max"],
                        "le",
                    ),
                    "support_fraction": (
                        supported / touching if touching else 0.0,
                        cfg["support"]["effective_upward_fraction_min"],
                        "ge",
                    ),
                    "support_geometry": (
                        margin,
                        cfg["support"]["target_local_complete_footprint_margin_m_min"],
                        "margin",
                    ),
                    "below_ground": (
                        minimum_z,
                        cfg["penetration"]["foreground_lowest_vertex_m_min"],
                        "ge",
                    ),
                    "penetration_m": (
                        max((c["penetration"] for r in rows for c in r["contacts"]), default=0.0),
                        cfg["penetration"]["all_trajectory_m_max"],
                        "le",
                    ),
                    "unexpected_ground_contact": (
                        sum(
                            {c["a"], c["b"]} == {name, "ground"}
                            for r in rows
                            for c in r["contacts"]
                        ),
                        0,
                        "le",
                    ),
                }
                for key, (observed, limit, comparison) in measures.items():
                    passed = (
                        observed <= limit
                        if comparison == "le"
                        else observed < limit
                        if comparison == "lt"
                        else observed + 1e-8 >= limit
                        if comparison == "margin"
                        else observed >= limit
                    )
                    report["checks"].append(
                        {
                            "profile": profile,
                            "entity": name,
                            "name": key,
                            "observed": observed,
                            "limit": limit,
                            "passed": bool(passed),
                        }
                    )
                ends[profile][name] = states[-1]
        for name in sorted(dynamic):
            a = ends["baseline"][name]
            b = ends["half_dt"][name]
            for key, value, limit in [
                (
                    "position_delta_m",
                    float(np.linalg.norm(np.array(a["position"]) - b["position"])),
                    cfg["cross_profile"]["per_foreground_final_position_m_max"],
                ),
                (
                    "rotation_delta_deg",
                    _angle(a["orientation_wxyz"], b["orientation_wxyz"]),
                    cfg["cross_profile"]["per_foreground_final_rotation_deg_max"],
                ),
            ]:
                report["checks"].append(
                    {
                        "profile": "agreement",
                        "entity": name,
                        "name": key,
                        "observed": value,
                        "limit": limit,
                        "passed": value <= limit,
                    }
                )
        report["physical_status"] = (
            "passed" if all(c["passed"] for c in report["checks"]) else "failed"
        )
    except (ValueError, KeyError, TypeError) as exc:
        report.update(
            physical_status="failed", error_code="invalid_physical_evidence", reason=str(exc)
        )
    return report


def assess_scene(
    scene, *, package_root, scene_ir_bytes, input_sha256, profiles, visual_status="not_run"
):
    """Consume immutable profile file identities; missing evidence cannot pass."""
    result = {
        "schema_version": "x2env.physical_assessment.v1",
        "status": "not_run",
        "physical_status": "not_run",
        "visual_status": visual_status,
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
        "checks": [],
        "assertions_sha256": ASSERTIONS_SHA256,
        "initial_reset_status": "not_run",
        "initial_reset_profiles": {},
        "post_step_reset_evaluated": False,
        "topology_status": "not_run",
        "topology_profiles": {},
    }
    if set(profiles) != {"baseline", "half_dt"}:
        return {**result, "error_code": "incomplete_dual_profile"}
    try:
        if visual_status not in {"passed", "failed", "not_run"}:
            raise ValueError("invalid separate visual status")
        scene = RuntimeScene.model_validate(scene)
        if (
            hashlib.sha256(scene_ir_bytes).hexdigest() != scene.scene_ir_sha256
            or json.loads(scene_ir_bytes)["input_sha256"] != input_sha256
        ):
            raise ValueError("scene/input identity differs")
        package = Path(package_root).resolve()
        _verify(scene, package)
        payload = scene.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        loaded = {}
        rows = {}
        identities = []
        result["input_sha256"] = input_sha256
        result["scene_sha256"] = digest
        for profile in ["baseline", "half_dt"]:
            evidence = ProfileEvidence.model_validate(profiles[profile])
            root = Path(evidence.root)
            members = evidence.files
            names = {m.path for m in members}
            required = {
                "job.json",
                "result.json",
                "loaded.json",
                "trace.ndjson",
                "media.json",
                "process.json",
                "genesis_child.py",
                "invocation.json",
                "preview.mp4",
                "simulation.mkv",
            }
            if not required <= names:
                return {
                    **result,
                    "error_code": "missing_profile_evidence",
                    "missing_files": sorted(required - names),
                }
            if len(names) != len(members):
                raise ValueError("duplicate profile member")
            for member in members:
                raw = _member(root, member.path).read_bytes()
                if (
                    len(raw) != member.size_bytes
                    or hashlib.sha256(raw).hexdigest() != member.sha256
                ):
                    if member.path == "reset-lifecycle.json":
                        result["initial_reset_status"] = "failed"
                        result["initial_reset_profiles"][profile] = "failed"
                        raise ValueError("reset lifecycle artifact identity differs")
                    raise ValueError("profile artifact identity differs")

            def read(name):
                return json.loads(_member(root, name).read_bytes())

            job = read("job.json")
            execution = read("result.json")
            process = read("process.json")
            spec = _assertions()["replay"][profile]
            if (
                job["scene"] != payload
                or job["scene_sha256"] != digest
                or job["profile"] != profile
                or execution["scene_sha256"] != digest
                or execution["profile"] != profile
                or execution["status"] != "passed"
                or execution["simulator_executed"] is not True
                or execution["steps"] != spec["steps"]
                or execution["dt"] != spec["dt_s"]
            ):
                raise ValueError("execution/scene/profile identity differs")
            if (
                process["pid"] != process["pgid"]
                or process["exit_code"] != 0
                or process["ended_monotonic"] < process["started_monotonic"]
            ):
                raise ValueError("invalid process lifecycle")
            identities.append((process["pid"], process["started_monotonic"]))
            if (
                read("invocation.json")["child_sha256"]
                != hashlib.sha256((root / "genesis_child.py").read_bytes()).hexdigest()
            ):
                raise ValueError("executed child identity differs")
            loaded[profile] = read("loaded.json")
            try:
                result["topology_profiles"][profile] = _verify_loaded_assets(
                    scene, package, loaded[profile]
                )
            except (ValueError, KeyError, TypeError, OSError) as exc:
                result["topology_status"] = "failed"
                result["topology_profiles"][profile] = "failed"
                raise ValueError(f"invalid loaded asset/topology evidence: {exc}") from exc
            rows[profile] = [
                json.loads(line) for line in (root / "trace.ndjson").read_bytes().splitlines()
            ]
            try:
                result["initial_reset_profiles"][profile] = _verify_initial_reset(
                    scene, root, names, execution, process, loaded[profile], rows[profile]
                )
            except (ValueError, KeyError, TypeError, OSError, SyntaxError) as exc:
                result["initial_reset_status"] = "failed"
                result["initial_reset_profiles"][profile] = "failed"
                raise ValueError(f"invalid initial reset evidence: {exc}") from exc
            _verify_media(root, read("media.json"), names, profile)
        if identities[0] == identities[1]:
            raise ValueError("profiles are not independent executions")
        computed = evaluate_physics(scene, rows["baseline"], rows["half_dt"], loaded)
        result.update(computed)
        result["execution_evidence_bound"] = True
        result["topology_status"] = (
            "passed" if set(result["topology_profiles"].values()) == {"passed"} else "not_run"
        )
        result["initial_reset_status"] = (
            "passed" if set(result["initial_reset_profiles"].values()) == {"passed"} else "not_run"
        )
        result["visual_status"] = visual_status
        result["status"] = (
            "failed"
            if result["physical_status"] == "failed" or visual_status == "failed"
            else "passed"
            if result["physical_status"] == "passed" and visual_status == "passed"
            else "not_run"
        )
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        result.update(
            physical_status="failed",
            status="failed",
            error_code="invalid_bound_evidence",
            reason=str(exc),
        )
    return result


def _verify_initial_reset(scene, root, names, execution, process, loaded, rows):
    """Historical file audit only; never claims a live or post-step reset experiment.

    Inspect the invocation-bound worker bytes, not this installation's worker. A new
    worker's explicit declaration prevents stripping both claim and lifecycle file.
    """
    source = ast.parse((root / "genesis_child.py").read_bytes())
    declarations = [
        node.value
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "INITIAL_RESET_REQUIRED"
            for target in node.targets
        )
    ]
    if declarations and (
        len(declarations) != 1
        or not isinstance(declarations[0], ast.Constant)
        or declarations[0].value is not True
    ):
        raise ValueError("invalid reset requirement declaration")
    child = (
        json.loads((root / "child-result.json").read_bytes())
        if "child-result.json" in names
        else {}
    )
    if not isinstance(child, dict):
        raise ValueError("invalid reset child result")
    claim = execution.get("initial_reset")
    if (
        not declarations
        and claim is None
        and "initial_reset" not in child
        and ("reset-lifecycle.json" not in names)
    ):
        return "not_run"
    if (
        not isinstance(claim, dict)
        or child.get("initial_reset") != claim
        or claim.get("path") != "reset-lifecycle.json"
        or claim.get("status") != "passed"
        or claim.get("post_step_reset_evaluated") is not False
        or "reset-lifecycle.json" not in names
    ):
        raise ValueError("missing or inconsistent reset claim")
    raw = (root / "reset-lifecycle.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != claim.get("sha256"):
        raise ValueError("reset lifecycle hash differs")
    proof = json.loads(raw)
    if (
        proof["schema_version"] != "x2env.initial_scene_reset.v1"
        or proof["status"] != "passed"
        or proof["reset_invoked"] is not True
        or proof["post_step_reset_evaluated"] is not False
        or type(proof["warmup_steps"]) is not int
        or proof["warmup_steps"] != 0
    ):
        raise ValueError("invalid reset lifecycle schema or scope")
    events = proof["events"]
    if not isinstance(events, list) or len(events) != 2:
        raise ValueError("invalid reset lifecycle actions")
    last = process["started_monotonic"]
    end = process["ended_monotonic"]
    for event, action in zip(events, ("build", "reset"), strict=True):
        start, finish = event["started_monotonic"], event["ended_monotonic"]
        if (
            event["action"] != action
            or event["status"] != "completed"
            or any(
                type(v) not in (int, float) or not math.isfinite(v)
                for v in (last, start, finish, end)
            )
            or not 0 <= last <= start <= finish <= end
        ):
            raise ValueError("invalid reset lifecycle order or timing")
        last = finish
    expected = {entity.id for entity in scene.entities} | {"ground"}
    if set(proof["objects"]) != expected or set(loaded) != expected - {"ground"}:
        raise ValueError("reset entity set differs")
    if not rows or rows[0]["step"] != 0 or rows[0]["time_s"] != 0:
        raise ValueError("reset lacks zero-step trace")
    if set(rows[0]["objects"]) != expected:
        raise ValueError("reset zero-step entity set differs")
    for name, state in proof["objects"].items():
        for field in ("velocity", "angular_velocity"):
            value = _vector(state[field])
            if np.linalg.norm(value) > 1e-7:
                raise ValueError("reset did not establish zero velocity")
            if not np.array_equal(value, _vector(rows[0]["objects"][name][field])):
                raise ValueError("reset velocity differs from zero-step trace")
            if name != "ground" and not np.array_equal(
                value, _vector(loaded[name]["initial_" + field])
            ):
                raise ValueError("reset velocity differs from loaded evidence")
    return "passed"


def _verify_loaded_assets(scene, package, loaded):
    topology = []
    for entity in scene.entities:
        if entity.kind != "rigid":
            continue
        actual = loaded[entity.id]
        physics = json.loads((package / entity.physics_path).read_bytes())
        if (
            not math.isclose(actual["mass_kg"], physics["mass_kg"], rel_tol=1e-4)
            or actual["supplied_friction"] != physics["friction"]
            or not actual["friction"]
            or not np.allclose(actual["friction"], physics["friction"], atol=1e-6)
        ):
            raise ValueError("loaded values differ from immutable physics member")
        inertial = ET.parse(package / entity.urdf_path).find("link/inertial")
        center = np.fromstring(inertial.find("origin").get("xyz", "0 0 0"), sep=" ")
        raw = inertial.find("inertia").attrib
        matrix = np.array(
            [
                [float(raw["ixx"]), float(raw["ixy"]), float(raw["ixz"])],
                [float(raw["ixy"]), float(raw["iyy"]), float(raw["iyz"])],
                [float(raw["ixz"]), float(raw["iyz"]), float(raw["izz"])],
            ]
        )
        r = Rotation.from_euler(
            "xyz", np.fromstring(inertial.find("origin").get("rpy", "0 0 0"), sep=" ")
        ).as_matrix()
        matrix = r @ matrix @ r.T
        if (
            not np.isfinite(matrix).all()
            or not np.isfinite(actual["inertia_link_kg_m2"]).all()
            or np.linalg.norm(matrix) <= 0
            or not np.allclose(actual["com_link_m"], center, atol=1e-5)
            or np.linalg.norm(np.asarray(actual["inertia_link_kg_m2"]) - matrix)
            / np.linalg.norm(matrix)
            > 1e-4
            or not math.isclose(
                actual["mass_kg"], float(inertial.find("mass").get("value")), rel_tol=1e-4
            )
        ):
            raise ValueError("actual inertia/COM/mass differs from immutable URDF")
        parts = actual.get("geometry_parts")
        if parts is None and actual.get("topology_status", "not_run") != "not_run":
            raise ValueError("missing loaded topology evidence")
        audit = audit_geometry(
            actual["local_visual_vertices_m"],
            actual["local_collision_vertices_m"],
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            package / entity.urdf_path,
            geometry_parts=parts,
        )
        topology.append(audit["topology_status"])
    return "passed" if topology and set(topology) == {"passed"} else "not_run"


def _verify_media(root, media, names, profile):
    from PIL import Image

    stride = 25 if profile == "baseline" else 50
    if media["total_frames"] != 41 or len(media["frames"]) != 41 or media["fps"] != 10:
        raise ValueError("incomplete sequential media")
    hashes = []
    last_time = None
    for index, frame in enumerate(media["frames"]):
        if (
            frame["path"] not in names
            or frame["step"] != index * stride
            or abs(frame["time_s"] - index * 0.1) > 1e-9
        ):
            raise ValueError("unbound or nonsequential capture")
        captured = datetime.fromisoformat(frame["captured_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None or (last_time is not None and captured < last_time):
            raise ValueError("invalid camera completion timestamp")
        last_time = captured
        path = _member(root, frame["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != frame["png_sha256"]:
            raise ValueError("camera PNG differs")
        with Image.open(path) as image:
            if image.mode != "RGB" or image.size != (media["width"], media["height"]):
                raise ValueError("camera format differs")
            digest = hashlib.sha256(image.tobytes()).hexdigest()
        if digest != frame["rgb_sha256"]:
            raise ValueError("camera pixels differ")
        hashes.append(digest)
    frame_bytes = media["width"] * media["height"] * 3

    def decode(name):
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(root / name),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=60,
        )
        times = [
            float(row["best_effort_timestamp_time"]) for row in json.loads(probe.stdout)["frames"]
        ]
        if len(times) != 41 or any(
            not math.isfinite(t) or abs(t - i * 0.1) > 1e-6 for i, t in enumerate(times)
        ):
            raise ValueError("video frame timing differs from capture sequence")
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-threads",
                "1",
                "-i",
                str(root / name),
                "-frames:v",
                "42",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=60,
        )
        if len(process.stdout) != 41 * frame_bytes:
            raise ValueError("video frame size/count differs")
        return [
            hashlib.sha256(process.stdout[i : i + frame_bytes]).hexdigest()
            for i in range(0, len(process.stdout), frame_bytes)
        ]

    decoded = decode("simulation.mkv")
    if decoded != hashes or len(set(hashes)) != media["unique_frames"]:
        raise ValueError("lossless movie differs from ordered camera frames")
    decode("preview.mp4")
