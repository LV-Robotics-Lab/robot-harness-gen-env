"""Finite-table Genesis replay, with sequential diagnostic media from the same steps.

Load, contact observation and paired-dt workflow semantics derive from Gujie's
validate_imported_scene.py at eb0b710581fd7794bc01b447b0f77cb871c8a711.
Genesis World owns the simulator; this Harness implementation owns its output contract.
"""

import hashlib
import json
import logging
import os
import traceback
from pathlib import Path


def run_genesis_scene_cpu(
    scene_spec,
    urdf_path: Path,
    output_dir: Path,
    *,
    profile: str,
    runtime_observer=None,
    cpu_facts=None,
):
    """Execute exactly one fresh four-second profile; never repair input poses."""
    if profile not in ("baseline", "half_dt"):
        raise ValueError("unknown scene replay profile")
    if not urdf_path.is_absolute() or not urdf_path.is_file():
        raise ValueError("scene URDF must be a materialized absolute file")
    if os.environ.get("PYOPENGL_PLATFORM") != "osmesa":
        raise ValueError("scene runner requires the explicit OSMesa software renderer")
    output_dir.mkdir(parents=True, exist_ok=False)
    dt, steps, stride = (0.004, 1000, 25) if profile == "baseline" else (0.002, 2000, 50)
    result = {"status": "error", "profile": profile, "dt": dt, "steps": steps, "rows": 0}
    gs = None
    writers = []
    platform_class = None
    original_framebuffers = None
    try:
        os.environ.update(GS_HEADLESS="1", PYGLET_HEADLESS="1")
        import genesis as gs
        import imageio.v2 as imageio
        import numpy as np
        import torch
        from genesis.ext.pyrender.platforms.osmesa import OSMesaPlatform
        from PIL import Image

        platform_class = OSMesaPlatform
        original_framebuffers = OSMesaPlatform.supports_framebuffers
        OSMesaPlatform.supports_framebuffers = _checked_osmesa_framebuffers

        if gs.__version__ != "1.3.3" or getattr(gs, "_initialized", False):
            raise ValueError("scene requires a fresh fixed Genesis 1.3.3 process")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        from contextlib import nullcontext

        from ._genesis_cpu_child import bind_cpu_device_metadata

        context = (
            nullcontext()
            if cpu_facts is None
            else bind_cpu_device_metadata(gs, cpu_device=torch.device("cpu"), facts=cpu_facts)
        )
        with context:
            gs.init(backend=gs.cpu, seed=0, precision="32", logging_level=logging.WARNING)
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
        owners = {geom.idx: name for name, entity in entities.items() for geom in entity.geoms}
        loaded = {
            name: {
                "links": len(entity.links),
                "dofs": entity.n_dofs,
                "fixed": bool(entity.base_link.is_fixed),
                "collision_shapes": len(entity.geoms),
                "mass_kg": float(entity.get_mass().item()),
                "world_vertices_m": entity.get_verts()
                .detach()
                .cpu()
                .numpy()
                .reshape(-1, 3)
                .tolist(),
            }
            for name, entity in entities.items()
        }
        loaded.update(
            object_dofs=entities["object"].n_dofs,
            representation=scene_spec.get("representation"),
            table_center_m=scene_spec["table_center_m"],
            table_size_m=scene_spec["table_size_m"],
            profile=profile,
            table_fixed=bool(entities["table"].base_link.is_fixed),
            ground_fixed=bool(entities["ground"].base_link.is_fixed),
            object_collision_geoms=len(entities["object"].geoms),
            table_collision_geoms=len(entities["table"].geoms),
            ground_collision_geoms=len(entities["ground"].geoms),
        )
        _write(output_dir / "loaded.json", loaded)
        if entities["object"].n_dofs != 6 or not entities["table"].base_link.is_fixed:
            raise ValueError("scene dynamic/fixed load differs")
        frames = output_dir / "frames"
        frames.mkdir()
        lossless = imageio.get_writer(
            output_dir / "simulation.mkv", format="FFMPEG", fps=10, codec="ffv1", pixelformat="bgr0"
        )
        writers.append(lossless)
        preview = imageio.get_writer(
            output_dir / "preview.mp4",
            format="FFMPEG",
            fps=10,
            codec="libx264",
            pixelformat="yuv420p",
        )
        writers.append(preview)
        sampled = []
        scene.rigid_solver.detect_collision()
        with (output_dir / "trace.ndjson").open("x") as stream:
            for step in range(steps + 1):
                if step:
                    scene.step()
                raw = scene.rigid_solver.collider.get_contacts(to_torch=False)
                contacts = []
                for index, valid in enumerate(raw.get("valid_mask", [True] * len(raw["geom_a"]))):
                    if not valid:
                        continue
                    force = np.asarray(raw["force"][index]).reshape(3)
                    contacts.append(
                        {
                            "a": owners[int(raw["geom_a"][index])],
                            "b": owners[int(raw["geom_b"][index])],
                            "position": np.asarray(raw["position"][index]).reshape(3).tolist(),
                            "normal": np.asarray(raw["normal"][index]).reshape(3).tolist(),
                            "penetration": float(raw["penetration"][index]),
                            "force_a": (-force).tolist() if step else None,
                            "force_b": force.tolist() if step else None,
                        }
                    )
                objects = {}
                for name, entity in entities.items():
                    objects[name] = {
                        key: method().detach().cpu().numpy().reshape(-1).tolist()
                        for key, method in (
                            ("position", entity.get_pos),
                            ("orientation_wxyz", entity.get_quat),
                            ("velocity", entity.get_vel),
                            ("angular_velocity", entity.get_ang),
                        )
                    }
                if step:
                    total = np.zeros(3)
                    for contact in contacts:
                        if contact["a"] == "object":
                            total += contact["force_a"]
                        if contact["b"] == "object":
                            total += contact["force_b"]
                    measured = (
                        entities["object"]
                        .get_links_net_contact_force()
                        .detach()
                        .cpu()
                        .numpy()
                        .reshape(-1, 3)
                        .sum(0)
                    )
                    if not np.allclose(total, measured, atol=1e-5, rtol=1e-4):
                        raise ValueError("scene contact forces differ from actual object force")
                row = {
                    "schema_version": "harness.genesis_scene_trace_row.v1",
                    "step": step,
                    "time_s": step * dt,
                    "contact_phase": "solved_step" if step else "initial_detection",
                    "objects": objects,
                    "contacts": contacts,
                }
                stream.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                )
                stream.flush()
                result["rows"] += 1
                if step % stride == 0:
                    rgb = np.asarray(camera.render(rgb=True, force_render=True)[0], dtype=np.uint8)
                    if step == 0:
                        from OpenGL.GL import GL_RENDERER, GL_VENDOR, GL_VERSION, glGetString

                        renderer = {
                            name: glGetString(value).decode()
                            for name, value in (
                                ("renderer", GL_RENDERER),
                                ("vendor", GL_VENDOR),
                                ("version", GL_VERSION),
                            )
                        }
                        if (
                            "llvmpipe" not in renderer["renderer"].lower()
                            or renderer["vendor"] != "Mesa"
                        ):
                            raise ValueError("actual renderer is not the declared Mesa llvmpipe")
                        result["renderer"] = renderer
                    frame_path = frames / f"step-{step:04d}.png"
                    Image.fromarray(rgb).save(frame_path)
                    lossless.append_data(rgb)
                    preview.append_data(rgb)
                    sampled.append(
                        {
                            "step": step,
                            "time_s": step * dt,
                            "path": frame_path.relative_to(output_dir).as_posix(),
                            "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                            "png_sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                        }
                    )
        for writer in writers:
            writer.close()
        writers.clear()
        decoded = []
        with imageio.get_reader(output_dir / "simulation.mkv") as reader:
            for frame in reader:
                decoded.append(hashlib.sha256(frame.tobytes()).hexdigest())
        if decoded != [frame["rgb_sha256"] for frame in sampled]:
            raise ValueError("lossless video does not reproduce the ordered real RGB frames")
        with imageio.get_reader(output_dir / "preview.mp4") as reader:
            preview_hashes = [hashlib.sha256(frame.tobytes()).hexdigest() for frame in reader]
        if len(preview_hashes) != len(sampled):
            raise ValueError("preview frame count differs")
        media = {
            "total_frames": len(sampled),
            "unique_frames": len(set(decoded)),
            "lossless_pixels_equal": True,
            "fps": 10,
            "width": 640,
            "height": 480,
            "frames": sampled,
            "preview_decoded_sha256": preview_hashes,
        }
        _write(output_dir / "media.json", media)
        maps = sorted(
            {
                line.split()[-1]
                for line in (
                    "" if runtime_observer else Path("/proc/self/maps").read_text()
                ).splitlines()
                if len(line.split()) >= 6
                and line.split()[-1].startswith("/")
                and ".so" in line.split()[-1]
            }
        )
        _write(
            output_dir / "renderer-runtime.json",
            runtime_observer()
            if runtime_observer
            else {
                "renderer": result["renderer"],
                "native_files": [
                    {"path": path, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                    for path in maps
                ],
                "open_devices": sorted(
                    {
                        str(path.resolve())
                        for path in Path("/proc/self/fd").iterdir()
                        if path.exists() and str(path.resolve()).startswith("/dev/")
                    }
                ),
                "execution_authority": False,
            },
        )
        result.update(
            status="complete", media=media, simulator_executed=True, qualification_issued=False
        )
    except Exception as error:
        result.update(
            error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc()
        )
        raise
    finally:
        for writer in writers:
            writer.close()
        _write(output_dir / "result.json", result)
        if gs is not None and getattr(gs, "_initialized", False):
            gs.destroy()
        if platform_class is not None:
            platform_class.supports_framebuffers = original_framebuffers
    return result


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _checked_osmesa_framebuffers(platform):
    """Use FBOs only after Mesa creates and completes a real color framebuffer.

    The fixed Genesis OSMesa adapter reports False for all Mesa versions. Its fallback
    render branch currently asserts on a None return. Modern Mesa supports FBOs; check
    that actual capability instead of changing upstream files or assuming support.
    """
    from OpenGL import GL

    platform.make_current()
    if GL.glGetString(GL.GL_VERSION) is None:
        raise ValueError("OSMesa context is not current")
    previous_framebuffer = int(GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING))
    previous_renderbuffer = int(GL.glGetIntegerv(GL.GL_RENDERBUFFER_BINDING))
    framebuffer = GL.glGenFramebuffers(1)
    renderbuffer = GL.glGenRenderbuffers(1)
    try:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, framebuffer)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, renderbuffer)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, 4, 4)
        GL.glFramebufferRenderbuffer(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, renderbuffer
        )
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise ValueError("OSMesa actual framebuffer is incomplete")
        return True
    finally:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, previous_framebuffer)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, previous_renderbuffer)
        GL.glDeleteFramebuffers(1, [framebuffer])
        GL.glDeleteRenderbuffers(1, [renderbuffer])
