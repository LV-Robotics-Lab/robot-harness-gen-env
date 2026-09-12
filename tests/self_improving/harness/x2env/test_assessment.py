"""Frozen physical assertions: analytical fixtures are not simulation evidence."""

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from self_improving.harness.x2env.assessment import assess_scene, evaluate_physics


def analytic():
    import copy

    scene = {
        "schema_version": "x2env.runtime_scene.v1",
        "scene_ir_sha256": "a" * 64,
        "seed": 0,
        "entities": [
            {
                "id": "table",
                "kind": "structural_box",
                "category": "table",
                "position_m": [0.0, 0.0, 0.35],
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "size_m": [1.0, 1.0, 0.1],
                "friction": 0.6,
            },
            {
                "id": "item",
                "kind": "rigid",
                "category": "cube",
                "position_m": [0.0, 0.0, 0.4],
                "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                "urdf_path": "asset.urdf",
                "physics_path": "physics.json",
                "version_sha256": "b" * 64,
            },
        ],
        "relations": [{"source": "item", "target": "table", "relation": "on"}],
        "members": [],
    }
    vertices = [[-0.05, -0.05, 0.0], [0.05, -0.05, 0.0], [-0.05, 0.05, 0.0], [0.05, 0.05, 0.1]]
    loaded = {
        "item": {
            "mass_kg": 0.1,
            "authored_mass_kg": 0.1,
            "friction": [0.5],
            "supplied_friction": 0.5,
            "dofs": 6,
            "fixed": False,
            "collision_shapes": 1,
            "position_m": [0.0, 0.0, 0.4],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "geometry_basis": "actual_genesis_vertices_inverse_world_pose",
            "local_visual_vertices_m": vertices,
            "local_collision_vertices_m": vertices,
        },
        "table": {
            "fixed": True,
            "collision_shapes": 1,
            "friction": [0.6],
            "position_m": [0.0, 0.0, 0.35],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
        },
    }

    def rows(dt, steps):
        output = []
        for step in range(steps + 1):
            objects = {
                name: {
                    "position": pos,
                    "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
                    "velocity": [0.0, 0.0, 0.0],
                    "angular_velocity": [0.0, 0.0, 0.0],
                }
                for name, pos in [
                    ("item", [0.0, 0.0, 0.4]),
                    ("table", [0.0, 0.0, 0.35]),
                    ("ground", [0.0, 0.0, 0.0]),
                ]
            }
            contact = {
                "a": "item",
                "b": "table",
                "position": [0.0, 0.0, 0.4],
                "normal": [0.0, 0.0, 1.0],
                "penetration": 0.0,
                "force_a": [0.0, 0.0, 0.981] if step else None,
                "force_b": [0.0, 0.0, -0.981] if step else None,
            }
            output.append(
                {
                    "step": step,
                    "time_s": step * dt,
                    "objects": objects,
                    "contacts": [contact],
                    "contact_phase": "solved_step" if step else "initial_detection",
                    "net_contact_forces": {"item": [0.0, 0.0, 0.981] if step else None},
                }
            )
        return output

    return (
        scene,
        rows(0.004, 1000),
        rows(0.002, 2000),
        {"baseline": loaded, "half_dt": copy.deepcopy(loaded)},
    )


def test_single_short_smoke_is_not_physical_qualification(tmp_path):
    result = assess_scene(
        {},
        package_root=tmp_path,
        scene_ir_bytes=b"{}",
        input_sha256="a" * 64,
        profiles={"load_step_smoke": {"root": str(tmp_path), "files": []}},
    )
    assert result["physical_status"] == "not_run"
    assert result["error_code"] == "incomplete_dual_profile"
    assert result["visual_status"] == "not_run"
    assert result["robot_policy_evaluated"] is False


def test_complete_analytic_support_obeys_frozen_metrics_without_execution_authority():
    report = evaluate_physics(*analytic())
    assert report["physical_status"] == "passed"
    assert report["authority"] == "trace_consistency_only"
    assert report["simulator_execution_proven"] is False
    assert any(c["name"] == "support_geometry" and c["observed"] == 0.45 for c in report["checks"])


@pytest.mark.parametrize(
    "attack",
    [
        "static",
        "no_contact",
        "fake_force",
        "penetration",
        "wrong_ground",
        "missing_step",
        "overflow_footprint",
        "moving_root",
    ],
)
def test_false_positive_attacks_never_pass(attack):
    scene, a, b, loaded = analytic()
    if attack == "static":
        loaded["baseline"]["item"]["fixed"] = True
    elif attack == "no_contact":
        for row in a:
            row["contacts"] = []
            row["net_contact_forces"]["item"] = [0.0, 0.0, 0.0] if row["step"] else None
    elif attack == "fake_force":
        a[-1]["net_contact_forces"]["item"] = [0.0, 0.0, 0.0]
    elif attack == "penetration":
        a[300]["contacts"][0]["penetration"] = 0.002
    elif attack == "wrong_ground":
        for row in a:
            row["contacts"][0]["b"] = "ground"
    elif attack == "missing_step":
        a[300]["step"] = 299
    elif attack == "overflow_footprint":
        for row in a[1:]:
            row["objects"]["item"]["position"][0] = 0.46
    elif attack == "moving_root":
        a[-1]["objects"]["table"]["position"][2] = 0.36
    assert evaluate_physics(scene, a, b, loaded)["physical_status"] == "failed"


def test_missing_full_second_profile_is_not_run():
    scene, a, b, loaded = analytic()
    assert evaluate_physics(scene, a, b[:-1], loaded)["physical_status"] == "not_run"


def bound_fixture(tmp_path):
    """Synthetic artifact producer: never a real process/simulation qualification."""
    import numpy as np
    import trimesh
    from PIL import Image

    from self_improving.harness.x2env.genesis_runtime import RuntimeScene
    from self_improving.harness.x2env.normalization import normalize_mesh

    scene, a, b, loaded = analytic()
    package = tmp_path / "package"
    source = tmp_path / "unit.glb"
    source.write_bytes(trimesh.creation.box().export(file_type="glb"))
    normalization = normalize_mesh(
        source, package, dimensions_m=(0.1, 0.1, 0.1), up_axis="Z", mass_kg=0.1, friction=0.5
    )
    scene["members"] = normalization["files"]
    physics = json.loads((package / "physics.json").read_bytes())
    for data in loaded.values():
        data["item"].update(
            com_link_m=physics["center_of_mass_m"], inertia_link_kg_m2=physics["inertia_kg_m2"]
        )
        vertices = [[x, y, z] for x in [-0.05, 0.05] for y in [-0.05, 0.05] for z in [0.0, 0.1]]
        data["item"].update(local_visual_vertices_m=vertices, local_collision_vertices_m=vertices)
    ir = json.dumps({"input_sha256": "c" * 64}).encode()
    scene["scene_ir_sha256"] = hashlib.sha256(ir).hexdigest()
    scene = RuntimeScene.model_validate(scene).model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(scene, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    def write(path, body):
        path.write_text(json.dumps(body))

    profiles = {}
    for index, (name, rows, stride) in enumerate([("baseline", a, 25), ("half_dt", b, 50)]):
        root = tmp_path / name
        root.mkdir()
        (root / "frames").mkdir()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        images = []
        for i in range(41):
            target = root / "frames" / f"{i:04}.png"
            Image.fromarray(frame).save(target)
            images.append(
                {
                    "step": i * stride,
                    "time_s": i * 0.1,
                    "path": target.relative_to(root).as_posix(),
                    "captured_at": (
                        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
                    ).isoformat(),
                    "png_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "rgb_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                }
            )
        for filename, codec, pixfmt in [
            ("simulation.mkv", "ffv1", "bgr0"),
            ("preview.mp4", "libx264", "yuv420p"),
        ]:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-s",
                    "32x32",
                    "-r",
                    "10",
                    "-i",
                    "-",
                    "-c:v",
                    codec,
                    "-pix_fmt",
                    pixfmt,
                    str(root / filename),
                ],
                input=frame.tobytes() * 41,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        write(
            root / "media.json",
            {
                "frames": images,
                "total_frames": 41,
                "unique_frames": 1,
                "fps": 10,
                "width": 32,
                "height": 32,
            },
        )
        write(root / "job.json", {"scene": scene, "scene_sha256": digest, "profile": name})
        write(
            root / "result.json",
            {
                "scene_sha256": digest,
                "profile": name,
                "status": "passed",
                "simulator_executed": True,
                "steps": len(rows) - 1,
                "dt": 0.004 if not index else 0.002,
            },
        )
        write(
            root / "process.json",
            {
                "pid": 100 + index,
                "pgid": 100 + index,
                "exit_code": 0,
                "started_monotonic": 1.0,
                "ended_monotonic": 2.0,
            },
        )
        (root / "genesis_child.py").write_text("# synthetic fixture identity, not executed\n")
        write(
            root / "invocation.json",
            {"child_sha256": hashlib.sha256((root / "genesis_child.py").read_bytes()).hexdigest()},
        )
        write(root / "loaded.json", loaded[name])
        (root / "trace.ndjson").write_text("\n".join(json.dumps(row) for row in rows))
        files = [
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "size_bytes": p.stat().st_size,
            }
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ]
        profiles[name] = {"root": str(root), "files": files}
    return dict(
        scene=scene,
        package_root=package,
        scene_ir_bytes=ir,
        input_sha256="c" * 64,
        profiles=profiles,
    )


def test_bound_analytic_files_do_not_gain_live_execution_authority(tmp_path):
    kwargs = bound_fixture(tmp_path)
    result = assess_scene(**kwargs)
    assert result["physical_status"] == "passed", result
    assert result["status"] == "not_run"  # no fresh Codex visual advisory here
    assert result["simulator_execution_proven"] is False
    assert result["execution_evidence_bound"] is True
    # File corruption and a copied identity must not inherit that result.
    from pathlib import Path

    trace = Path(kwargs["profiles"]["half_dt"]["root"]) / "trace.ndjson"
    trace.write_bytes(trace.read_bytes() + b"\n{}")
    result = assess_scene(**kwargs, visual_status="passed")
    assert result["physical_status"] == "failed"
    assert "artifact identity differs" in result["reason"]


@pytest.mark.parametrize(
    "attack",
    [
        "wrong_input",
        "reused_process",
        "false_geometry",
        "false_mass",
        "missing_frame",
        "nan_inertia",
    ],
)
def test_newly_hashed_but_semantically_false_evidence_is_rejected(tmp_path, attack):
    from pathlib import Path

    kwargs = bound_fixture(tmp_path)
    baseline = Path(kwargs["profiles"]["baseline"]["root"])
    half = Path(kwargs["profiles"]["half_dt"]["root"])

    def update(profile, name, body):
        root = Path(kwargs["profiles"][profile]["root"])
        path = root / name
        path.write_text(json.dumps(body))
        for member in kwargs["profiles"][profile]["files"]:
            if member["path"] == name:
                member.update(
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    size_bytes=path.stat().st_size,
                )

    if attack == "wrong_input":
        kwargs["input_sha256"] = "d" * 64
    elif attack == "reused_process":
        update("half_dt", "process.json", json.loads((baseline / "process.json").read_bytes()))
    elif attack in ["false_geometry", "false_mass", "nan_inertia"]:
        loaded = json.loads((half / "loaded.json").read_bytes())
        if attack == "false_geometry":
            loaded["item"]["local_collision_vertices_m"][0][0] += 0.01
        elif attack == "nan_inertia":
            loaded["item"]["inertia_link_kg_m2"][0][0] = float("nan")
        else:
            loaded["item"]["mass_kg"] = 1.0
        update("half_dt", "loaded.json", loaded)
    else:
        kwargs["profiles"]["half_dt"]["files"] = [
            m for m in kwargs["profiles"]["half_dt"]["files"] if m["path"] != "frames/0040.png"
        ]
    assert assess_scene(**kwargs, visual_status="passed")["physical_status"] == "failed"
