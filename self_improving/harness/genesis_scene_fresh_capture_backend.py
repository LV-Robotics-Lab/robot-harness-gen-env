"""Restore a verified final pose in a new Genesis process and take a real photograph.

The finite-table loading and checked software renderer derive from the Harness
scene CPU backend; this path does not advance or continue the original replay.
"""

import hashlib
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .genesis_scene_cpu_backend import _checked_osmesa_framebuffers, _write


def capture_restored_scene(
    scene_spec,
    final_state,
    urdf_path: Path,
    output_dir: Path,
    *,
    runtime_observer=None,
    cpu_facts=None,
):
    """Restore the trace's entity user-frame pose, zero velocities and render without steps."""
    if not urdf_path.is_absolute() or not urdf_path.is_file():
        raise ValueError("fresh capture requires a materialized absolute URDF")
    if os.environ.get("PYOPENGL_PLATFORM") != "osmesa":
        raise ValueError("fresh capture requires OSMesa software rendering")
    output_dir.mkdir(parents=True, exist_ok=False)
    gs = None
    platform_class = None
    original = None
    result = {
        "status": "error",
        "physics_steps": 0,
        "mode": "restored_final_state",
        "original_process_continues": False,
    }
    try:
        from contextlib import nullcontext

        import genesis as gs
        import numpy as np
        import torch
        from genesis.ext.pyrender.platforms.osmesa import OSMesaPlatform
        from PIL import Image

        from ._genesis_cpu_child import bind_cpu_device_metadata

        platform_class = OSMesaPlatform
        original = platform_class.supports_framebuffers
        platform_class.supports_framebuffers = _checked_osmesa_framebuffers
        if gs.__version__ != "1.3.3" or getattr(gs, "_initialized", False):
            raise ValueError("fresh capture requires a fresh fixed Genesis 1.3.3 process")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        context = (
            nullcontext()
            if cpu_facts is None
            else bind_cpu_device_metadata(gs, cpu_device=torch.device("cpu"), facts=cpu_facts)
        )
        with context:
            gs.init(backend=gs.cpu, seed=0, precision="32", logging_level=logging.WARNING)
        dt = 0.002
        scene = gs.Scene(
            show_viewer=False,
            renderer=gs.renderers.Rasterizer(),
            sim_options=gs.options.SimOptions(dt=dt, substeps=1, gravity=(0, 0, -9.81)),
            rigid_options=gs.options.RigidOptions(
                enable_collision=True,
                constraint_solver=gs.constraint_solver.Newton,
                iterations=50,
                ls_iterations=50,
                tolerance=1e-8,
                constraint_timeconst=0.01,
                use_hibernation=False,
            ),
        )
        physics_path = urdf_path.parent.parent / "physics.json"
        physics = json.loads(physics_path.read_text())
        entities = {
            "ground": scene.add_entity(
                gs.morphs.Plane(pos=(0, 0, scene_spec["ground_z_m"])), name="ground"
            ),
            "table": scene.add_entity(
                gs.morphs.Box(
                    size=tuple(scene_spec["table_size_m"]),
                    pos=tuple(scene_spec["table_center_m"]),
                    fixed=True,
                ),
                material=gs.materials.Rigid(friction=scene_spec["table_friction"]),
                surface=gs.surfaces.Default(color=(0.5, 0.4, 0.3, 1)),
                name="table",
            ),
            "object": scene.add_entity(
                gs.morphs.URDF(
                    file=str(urdf_path),
                    fixed=False,
                    convexify=False,
                    recompute_inertia=False,
                    pos=tuple(scene_spec["position_m"]),
                    quat=tuple(scene_spec["orientation_wxyz"]),
                ),
                material=gs.materials.Rigid(friction=physics["friction"]),
                name="object",
                vis_mode="visual",
            ),
        }
        center = scene_spec["table_center_m"]
        top = center[2] + scene_spec["table_size_m"][2] / 2
        camera = scene.add_camera(
            res=(640, 480),
            GUI=False,
            pos=(center[0] + 0.6, center[1] - 0.7, top + 0.5),
            lookat=(center[0], center[1], top + 0.02),
            fov=40,
        )
        scene.build()

        obj = entities["object"]
        if (
            obj.n_dofs != 6
            or not entities["table"].base_link.is_fixed
            or not entities["ground"].base_link.is_fixed
        ):
            raise ValueError("fresh capture dynamic/fixed load differs")
        obj.set_pos(final_state["position"], relative=True, zero_velocity=True)
        obj.set_quat(final_state["orientation_wxyz"], relative=True, zero_velocity=True)
        obj.set_dofs_velocity(np.zeros(6))
        actual = {
            key: method().detach().cpu().numpy().reshape(-1).tolist()
            for key, method in (
                ("position", obj.get_pos),
                ("orientation_wxyz", obj.get_quat),
                ("velocity", obj.get_vel),
                ("angular_velocity", obj.get_ang),
            )
        }
        if not np.allclose(actual["position"], final_state["position"], atol=1e-6, rtol=0):
            raise ValueError("actual restored position differs")
        if (
            abs(abs(float(np.dot(actual["orientation_wxyz"], final_state["orientation_wxyz"]))) - 1)
            > 1e-6
        ):
            raise ValueError("actual restored quaternion differs")
        if any(
            abs(value) > 1e-8 for key in ("velocity", "angular_velocity") for value in actual[key]
        ):
            raise ValueError("actual restored velocities are not zero")
        camera_config = {
            "width": 640,
            "height": 480,
            "position": [center[0] + 0.6, center[1] - 0.7, top + 0.5],
            "lookat": [center[0], center[1], top + 0.02],
            "fov_degrees": 40,
        }
        loaded = {
            "mode": "restored_final_state",
            "original_process_continues": False,
            "physics_steps": 0,
            "restore_velocity_policy": "zero",
            "pose_coordinate_frame": "genesis_entity_user_frame",
            "requested_source_state": final_state,
            "actual_restored_state": actual,
            "representation": scene_spec["representation"],
            "table_center_m": scene_spec["table_center_m"],
            "table_size_m": scene_spec["table_size_m"],
            "object_dofs": obj.n_dofs,
            "table_fixed": True,
            "ground_fixed": True,
            "collision_shapes": {name: len(entity.geoms) for name, entity in entities.items()},
            "camera": camera_config,
        }
        _write(output_dir / "loaded.json", loaded)
        rgb = np.asarray(camera.render(rgb=True, force_render=True)[0], dtype=np.uint8)
        captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        from OpenGL.GL import GL_RENDERER, GL_VENDOR, GL_VERSION, glGetString

        renderer = {
            key: glGetString(value).decode()
            for key, value in (
                ("renderer", GL_RENDERER),
                ("vendor", GL_VENDOR),
                ("version", GL_VERSION),
            )
        }
        if "llvmpipe" not in renderer["renderer"].lower() or renderer["vendor"] != "Mesa":
            raise ValueError("fresh capture actual renderer is not Mesa llvmpipe")
        path = output_dir / "fresh.png"
        Image.fromarray(rgb).save(path)
        capture = {
            "captured_at": captured_at,
            "pixels_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "image_path": "fresh.png",
            "camera": camera_config,
            "renderer": renderer,
        }
        _write(output_dir / "capture.json", capture)
        if runtime_observer is not None:
            _write(output_dir / "renderer-runtime.json", runtime_observer())
        result.update(status="complete", capture=capture, qualification_issued=False)
        return result
    except Exception as error:
        result.update(
            error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc()
        )
        raise
    finally:
        _write(output_dir / "result.json", result)
        if gs is not None and getattr(gs, "_initialized", False):
            gs.destroy()
        if platform_class is not None:
            platform_class.supports_framebuffers = original
