"""Package-independent Genesis worker, adapted from Harness c0236bd and Gujie eb0b710.

Only declared runtime/package paths are readable after confinement. No controller imports.
"""

import hashlib
import json
import logging
import os
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def restrict_scene_process(roots, run):
    """Apply inherited read-only runtime and no-network rules, without host GL exceptions.

    This is the renderer v1 policy. Its syscall rules derive from the existing
    CPU asset child, but its filesystem allowlist includes only declared roots.
    """
    import ctypes
    import os

    libc = ctypes.CDLL(None, use_errno=True)

    class Rules(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64), ("scope", ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("fd", ctypes.c_int)]

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    abi = libc.syscall(444, 0, 0, 1)
    if abi < 6:
        raise RuntimeError("required scene Landlock ABI is unavailable")
    rules = Rules((1 << 16) - 1, 3, 3)
    descriptor = libc.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if descriptor < 0:
        raise RuntimeError("scene filesystem policy unavailable")
    permissions = [(path, 13) for path in roots.values()]
    permissions += [(run, (1 << 16) - 1), ("/dev/urandom", 4), ("/dev/null", 6)]
    try:
        for path, access in permissions:
            path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(access, path_fd)
                if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0):
                    raise RuntimeError("scene filesystem rule failed")
            finally:
                os.close(path_fd)
        if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, descriptor, 0):
            raise RuntimeError("scene filesystem restriction failed")
    finally:
        os.close(descriptor)
    filters = [
        Filter(0x20, 0, 0, 4),
        Filter(0x15, 1, 0, 0xC000003E),
        Filter(0x06, 0, 0, 0x80000000),
        Filter(0x20, 0, 0, 0),
        Filter(0x45, 0, 1, 0x40000000),
        Filter(0x06, 0, 0, 0x80000000),
    ]
    for number in (41, 42, 43, 49, 50, 53, 288, 425):
        filters.extend((Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, 0x50001)))
    filters.append(Filter(0x06, 0, 0, 0x7FFF0000))
    array = (Filter * len(filters))(*filters)
    program = Program(len(filters), array)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise RuntimeError("scene network restriction failed")
    return abi


def write(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True, allow_nan=False) + "\n")


def array(value):
    return value.detach().cpu().numpy()


def audit_geometry(actual_visual_world, actual_collision_world, position, quaternion, source):
    """Compare measured loaded vertices against independently parsed static URDF geometry."""
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree
    from scipy.spatial.transform import Rotation

    root = ET.parse(source)
    q = np.asarray(quaternion)
    rotation = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
    result = {}
    for kind, world in [("visual", actual_visual_world), ("collision", actual_collision_world)]:
        actual = (np.asarray(world).reshape(-1, 3) - np.asarray(position)) @ rotation
        pieces = []
        for node in root.findall("link/" + kind):
            ref = node.find("geometry/mesh")
            if ref is None:
                raise ValueError("runtime requires measured mesh geometry")
            loaded = trimesh.load(
                Path(source).parent / ref.get("filename"), force="scene", process=False
            )
            mesh = loaded.to_geometry()
            scale = np.fromstring(ref.get("scale", "1 1 1"), sep=" ")
            points = np.asarray(mesh.vertices) * scale
            origin = node.find("origin")
            if origin is not None:
                rpy = np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")
                points = points @ Rotation.from_euler("xyz", rpy).as_matrix().T
                points += np.fromstring(origin.get("xyz", "0 0 0"), sep=" ")
            pieces.append(points)
        expected = np.concatenate(pieces)
        if not len(actual) or not np.isfinite(actual).all():
            raise ValueError("invalid actual loaded geometry")
        error = float(
            max(cKDTree(expected).query(actual)[0].max(), cKDTree(actual).query(expected)[0].max())
        )
        if error > 1e-5:
            raise ValueError("loaded geometry differs from authored URDF")
        result[kind + "_error_m"] = error
        result["local_" + kind + "_vertices_m"] = actual.tolist()
    result["geometry_basis"] = "actual_genesis_vertices_inverse_world_pose"
    return result


def audit(entity, source, physics):
    """Independently compare authored inertial values and actual Genesis geoms."""
    import numpy as np
    from scipy.spatial.transform import Rotation

    inertial = ET.parse(source).find("link/inertial")
    origin = inertial.find("origin")
    center = np.fromstring(origin.get("xyz", "0 0 0"), sep=" ")
    authored_mass = float(inertial.find("mass").get("value"))
    raw = inertial.find("inertia").attrib
    tensor = np.array(
        [
            [float(raw["ixx"]), float(raw["ixy"]), float(raw["ixz"])],
            [float(raw["ixy"]), float(raw["iyy"]), float(raw["iyz"])],
            [float(raw["ixz"]), float(raw["iyz"]), float(raw["izz"])],
        ]
    )
    rpy = np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")
    r = Rotation.from_euler("xyz", rpy).as_matrix()
    tensor = r @ tensor @ r.T
    link = entity.base_link
    q = np.asarray(link.desc.inertial_quat)
    rotation = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
    actual_i = rotation @ array(entity.get_links_inertia()).reshape(-1, 3, 3)[0] @ rotation.T
    mass = float(array(entity.get_mass()).reshape(-1)[0])
    friction = [float(array(g.get_friction())) for g in entity.geoms]
    record = {
        "mass_kg": mass,
        "authored_mass_kg": authored_mass,
        "friction": friction,
        "supplied_friction": physics["friction"],
        "com_link_m": np.asarray(link.desc.inertial_pos).tolist(),
        "inertia_link_kg_m2": actual_i.tolist(),
        "dofs": int(entity.n_dofs),
        "collision_shapes": len(entity.geoms),
        "fixed": bool(link.is_fixed),
    }
    if (
        entity.n_dofs != 6
        or link.is_fixed
        or not entity.geoms
        or not np.isclose(mass, authored_mass, rtol=1e-4, atol=0)
        or not np.isclose(mass, physics["mass_kg"], rtol=1e-4, atol=0)
        or not np.allclose(friction, physics["friction"], atol=1e-6)
        or not np.allclose(link.desc.inertial_pos, center, atol=1e-5)
        or np.linalg.norm(actual_i - tensor) / np.linalg.norm(tensor) > 1e-4
    ):
        raise ValueError("authored physical values changed during loading: " + json.dumps(record))
    return record


def execute(job):
    import genesis as gs
    import imageio.v2 as imageio
    import numpy as np
    import torch
    from genesis.ext.pyrender.platforms.osmesa import OSMesaPlatform
    from PIL import Image

    if gs.__version__ != "1.3.3" or getattr(gs, "_initialized", False):
        raise ValueError("fresh fixed Genesis 1.3.3 required")
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    original = gs.get_device
    gs.get_device = lambda backend, device_idx=None: (
        torch.device("cpu"),
        job["cpu_name"],
        job["memory_bytes"] / 1024**3,
        backend,
    )
    original_fbo = OSMesaPlatform.supports_framebuffers
    OSMesaPlatform.supports_framebuffers = checked_framebuffer
    out = Path(job["output"])
    package = Path(job["package_root"])
    writers = []
    try:
        gs.init(
            backend=gs.cpu, seed=job["scene"]["seed"], precision="32", logging_level=logging.WARNING
        )
        gs.get_device = original
        dt, steps, stride = {
            "baseline": (0.004, 1000, 25),
            "half_dt": (0.002, 2000, 50),
            "load_step_smoke": (0.004, 25, 5),
        }[job["profile"]]
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
        entities = {"ground": scene.add_entity(gs.morphs.Plane(), name="ground")}
        descriptions = {}
        physics_by_id = {}
        for row in job["scene"]["entities"]:
            name = row["id"]
            descriptions[name] = row
            pose = {"pos": tuple(row["position_m"]), "quat": tuple(row["orientation_wxyz"])}
            if row["kind"] == "structural_box":
                morph = gs.morphs.Box(size=tuple(row["size_m"]), fixed=True, **pose)
                friction = row["friction"]
            else:
                source = package / row["urdf_path"]
                physics = json.loads((package / row["physics_path"]).read_bytes())
                physics_by_id[name] = physics
                morph = gs.morphs.URDF(
                    file=str(source),
                    fixed=False,
                    collision=True,
                    scale=1.0,
                    convexify=False,
                    decimate=False,
                    watertighten=None,
                    recompute_inertia=False,
                    align=False,
                    merge_fixed_links=False,
                    **pose,
                )
                friction = physics["friction"]
            entities[name] = scene.add_entity(
                morph, name=name, vis_mode="visual", material=gs.materials.Rigid(friction=friction)
            )
        points = np.array([e["position_m"] for e in descriptions.values()])
        center = points.mean(0)
        distance = max(0.8, float(np.ptp(points, axis=0).max()) * 2)
        camera = scene.add_camera(
            res=(640, 480),
            GUI=False,
            pos=tuple(center + np.array([0.6, -0.7, 0.5]) * distance),
            lookat=tuple(center),
            fov=40,
        )
        scene.build()
        loaded = {}
        for name, row in descriptions.items():
            entity = entities[name]
            if row["kind"] == "rigid":
                loaded[name] = audit(entity, package / row["urdf_path"], physics_by_id[name])
                visual = np.concatenate(
                    [
                        array(g.get_vverts()).reshape(-1, 3)
                        for link in entity.links
                        for g in link.vgeoms
                    ]
                )
                loaded[name].update(
                    audit_geometry(
                        visual,
                        array(entity.get_verts()),
                        array(entity.get_pos()).reshape(3),
                        array(entity.get_quat()).reshape(4),
                        package / row["urdf_path"],
                    )
                )
            else:
                loaded[name] = {
                    "fixed": bool(entity.base_link.is_fixed),
                    "collision_shapes": len(entity.geoms),
                    "friction": [float(array(g.get_friction())) for g in entity.geoms],
                }
                if (
                    not loaded[name]["fixed"]
                    or not entity.geoms
                    or not np.allclose(loaded[name]["friction"], row["friction"], atol=1e-6)
                ):
                    raise ValueError("structural support load mismatch")
            loaded[name].update(
                position_m=array(entity.get_pos()).reshape(-1).tolist(),
                orientation_wxyz=array(entity.get_quat()).reshape(-1).tolist(),
            )
            if not np.allclose(loaded[name]["position_m"], row["position_m"], atol=1e-6):
                raise ValueError("runtime changed initial world position")
        write(out / "loaded.json", loaded)
        owners = {g.idx: name for name, entity in entities.items() for g in entity.geoms}
        frames = out / "frames"
        frames.mkdir()
        lossless = imageio.get_writer(
            out / "simulation.mkv", format="FFMPEG", fps=10, codec="ffv1", pixelformat="bgr0"
        )
        writers.append(lossless)
        preview = imageio.get_writer(
            out / "preview.mp4", format="FFMPEG", fps=10, codec="libx264", pixelformat="yuv420p"
        )
        writers.append(preview)
        captures = []
        scene.rigid_solver.detect_collision()
        with (out / "trace.ndjson").open("x") as stream:
            for step in range(steps + 1):
                if step:
                    scene.step()
                raw = scene.rigid_solver.collider.get_contacts(to_torch=False)
                contacts = []
                for i, valid in enumerate(raw.get("valid_mask", [True] * len(raw["geom_a"]))):
                    if not valid:
                        continue
                    force = np.asarray(raw["force"][i]).reshape(3)
                    contacts.append(
                        {
                            "a": owners[int(raw["geom_a"][i])],
                            "b": owners[int(raw["geom_b"][i])],
                            "position": np.asarray(raw["position"][i]).reshape(3).tolist(),
                            "normal": np.asarray(raw["normal"][i]).reshape(3).tolist(),
                            "penetration": float(raw["penetration"][i]),
                            "force_a": (-force).tolist() if step else None,
                            "force_b": force.tolist() if step else None,
                        }
                    )
                objects = {
                    name: {
                        key: array(method()).reshape(-1).tolist()
                        for key, method in [
                            ("position", entity.get_pos),
                            ("orientation_wxyz", entity.get_quat),
                            ("velocity", entity.get_vel),
                            ("angular_velocity", entity.get_ang),
                        ]
                    }
                    for name, entity in entities.items()
                }
                row = {
                    "step": step,
                    "time_s": step * dt,
                    "objects": objects,
                    "contacts": contacts,
                    "contact_phase": "solved_step" if step else "initial_detection",
                }
                row["net_contact_forces"] = {}
                for name in physics_by_id:
                    net = array(entities[name].get_links_net_contact_force()).reshape(-1, 3).sum(0)
                    total = np.zeros(3)
                    if step:
                        for contact in contacts:
                            if contact["a"] == name:
                                total += contact["force_a"]
                            if contact["b"] == name:
                                total += contact["force_b"]
                        if not np.allclose(total, net, atol=1e-5, rtol=1e-4):
                            raise ValueError("contact forces differ from actual entity net force")
                    row["net_contact_forces"][name] = net.tolist() if step else None
                stream.write(json.dumps(row, allow_nan=False) + "\n")
                stream.flush()
                if step % stride == 0:
                    rgb = np.asarray(camera.render(rgb=True, force_render=True)[0], dtype=np.uint8)
                    captured = datetime.now(timezone.utc).isoformat()
                    image = frames / f"step-{step:04d}.png"
                    Image.fromarray(rgb).save(image)
                    lossless.append_data(rgb)
                    preview.append_data(rgb)
                    captures.append(
                        {
                            "step": step,
                            "time_s": step * dt,
                            "captured_at": captured,
                            "path": image.relative_to(out).as_posix(),
                            "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                            "png_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        }
                    )
        for writer in writers:
            writer.close()
        writers.clear()
        with imageio.get_reader(out / "simulation.mkv") as reader:
            hashes = [hashlib.sha256(f.tobytes()).hexdigest() for f in reader]
        if hashes != [f["rgb_sha256"] for f in captures]:
            raise ValueError("sequential lossless video differs from camera frames")
        media = {
            "total_frames": len(captures),
            "unique_frames": len(set(hashes)),
            "width": 640,
            "height": 480,
            "fps": 10,
            "capture_stride": stride,
            "simulation_duration_s": dt * steps,
            "frames": captures,
            "playback_matches_simulation_rate": job["profile"] != "load_step_smoke",
        }
        write(out / "media.json", media)
        return {
            "status": "passed",
            "simulator_executed": True,
            "steps": steps,
            "dt": dt,
            "profile": job["profile"],
            "scene_sha256": job["scene_sha256"],
            "physical_profile": "not_run",
            "media": media,
            "loaded": loaded,
            "robot_policy_evaluated": False,
            "data_collection_evaluated": False,
        }
    finally:
        for writer in writers:
            writer.close()
        gs.get_device = original
        if getattr(gs, "_initialized", False):
            gs.destroy()
        OSMesaPlatform.supports_framebuffers = original_fbo


def main():
    job = json.loads(Path(sys.argv[1]).read_bytes())
    out = Path(job["output"])
    roots = {k: Path(v) for k, v in job["roots"].items()}
    sys.path[:] = [
        str(roots["stdlib"]),
        str(roots["stdlib"] / "lib-dynload"),
        str(roots["distributions"] / "lib/python3.12/site-packages"),
        str(roots["genesis"]),
    ]
    cpu = Path("/proc/cpuinfo").read_text()
    memory = Path("/proc/meminfo").read_text()
    job["cpu_name"] = next(
        s.partition(":")[2].strip() for s in cpu.splitlines() if s.startswith("model name")
    )
    job["memory_bytes"] = (
        int(next(s.split()[1] for s in memory.splitlines() if s.startswith("MemTotal:"))) * 1024
    )
    result = {"status": "failed", "simulator_executed": False, "scene_sha256": job["scene_sha256"]}
    start = time.monotonic()
    try:
        abi = restrict_scene_process({**roots, "package": Path(job["package_root"])}, out)
        denials = []
        for path in job["denied_roots"]:
            try:
                os.listdir(path)
            except PermissionError:
                denials.append({"path": path, "read_denied": True})
            else:
                raise RuntimeError("denied source remained readable")
        result["isolation"] = {"landlock_abi": abi, "source_read_denials": denials}
        for m in job["scene"]["members"]:
            content = (Path(job["package_root"]) / m["path"]).read_bytes()
            if (
                len(content) != m["size_bytes"]
                or hashlib.sha256(content).hexdigest() != m["sha256"]
            ):
                raise ValueError("runtime member changed before load")
        result.update(execute(job))
    except Exception as exc:
        result.update(status="failed", reason=str(exc), traceback=traceback.format_exc())
    result["wall_seconds"] = time.monotonic() - start
    write(out / "child-result.json", result)
    return 0 if result["status"] == "passed" else 1


def checked_framebuffer(platform):
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


if __name__ == "__main__":
    raise SystemExit(main())
