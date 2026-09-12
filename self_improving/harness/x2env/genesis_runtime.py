"""Canonical load/step/render adapter. No workflow, asset acquisition or qualification.

Derived from Harness c0236bd genesis_scene_cpu_backend / portable_genesis_package,
and Gujie eb0b710 standard_urdf morph/audit semantics. Genesis owns simulation.
RuntimeScene contains resolved world poses: this adapter never repairs placement.
"""

import hashlib
import json
import math
import os
import signal
import shutil
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Number = Annotated[float, Field(strict=True)]
Vec3 = tuple[Number, Number, Number]


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RuntimeMember(RuntimeModel):
    path: str
    sha256: Sha
    size_bytes: int = Field(ge=0)


class RuntimeEntity(RuntimeModel):
    id: str = Field(pattern=r"^[A-Za-z][\w-]*$")
    kind: Literal["rigid", "structural_box"]
    category: str
    position_m: Vec3
    orientation_wxyz: tuple[Number, Number, Number, Number]
    urdf_path: str | None = None
    physics_path: str | None = None
    version_sha256: Sha | None = None
    size_m: Vec3 | None = None
    friction: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def complete(self):
        if abs(sum(v * v for v in self.orientation_wxyz) - 1) > 1e-6:
            raise ValueError("orientation must be a unit wxyz quaternion")
        if self.kind == "rigid":
            if not all((self.urdf_path, self.physics_path, self.version_sha256)):
                raise ValueError("rigid needs explicit URDF, physics and version identity")
            if self.size_m is not None or self.friction is not None:
                raise ValueError("rigid geometry/physics must come from asset members")
        elif (
            self.category not in {"table", "worktop", "counter"}
            or self.size_m is None
            or min(self.size_m) <= 0
            or self.friction is None
            or self.urdf_path is not None
            or self.physics_path is not None
            or self.version_sha256 is not None
        ):
            raise ValueError("structural boxes only support table/worktop/counter")
        return self


class RuntimeRelation(RuntimeModel):
    source: str
    target: str
    relation: Literal["on", "left_of", "right_of", "front_of", "behind", "near"]


class RuntimeScene(RuntimeModel):
    schema_version: Literal["x2env.runtime_scene.v1"] = "x2env.runtime_scene.v1"
    seed: int = Field(strict=True, ge=0, le=2147483647)
    scene_ir_sha256: Sha
    entities: tuple[RuntimeEntity, ...] = Field(min_length=1, max_length=8)
    relations: tuple[RuntimeRelation, ...] = ()
    members: tuple[RuntimeMember, ...] = ()

    @model_validator(mode="after")
    def graph(self):
        ids = {e.id for e in self.entities}
        if len(ids) != len(self.entities) or "ground" in ids:
            raise ValueError("duplicate or reserved entity ID")
        if len({m.path for m in self.members}) != len(self.members):
            raise ValueError("duplicate member")
        for r in self.relations:
            if r.source not in ids or r.target not in ids or r.source == r.target:
                raise ValueError("invalid relation")
            if r.relation == "on":
                by_id = {e.id: e for e in self.entities}
                if by_id[r.source].kind != "rigid" or by_id[r.target].kind != "structural_box":
                    raise ValueError("runtime profile supports rigid on structural support only")
        return self


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _member(root, name):
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
        raise ValueError("unsafe member path")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(
        root
    ):
        raise ValueError("unsafe member path: symlink/escape")
    return path


def _verify(scene, root):
    names = {m.path for m in scene.members}
    for m in scene.members:
        content = _member(root, m.path).read_bytes()
        if len(content) != m.size_bytes or hashlib.sha256(content).hexdigest() != m.sha256:
            raise ValueError("corrupt runtime member")
        if Path(m.path).suffix.lower() == ".glb":
            if len(content) < 20 or content[:4] != b"glTF":
                raise ValueError("invalid GLB member")
            size = struct.unpack("<I", content[12:16])[0]
            doc = json.loads(content[20 : 20 + size])
            for group in ("buffers", "images"):
                if any(
                    item.get("uri") and not item["uri"].startswith("data:")
                    for item in doc.get(group, [])
                ):
                    raise ValueError("unsupported external GLB resource")
            if doc.get("skins") or doc.get("animations"):
                raise ValueError("unsupported articulated GLB")
        elif Path(m.path).suffix.lower() == ".obj":
            if any(
                line.strip().startswith(("mtllib ", "usemtl "))
                for line in content.decode().splitlines()
            ):
                raise ValueError("unsupported OBJ material closure; normalized GLB visual required")
    for e in scene.entities:
        if e.kind != "rigid":
            continue
        if e.urdf_path not in names or e.physics_path not in names:
            raise ValueError("unbound URDF/physics member")
        urdf = _member(root, e.urdf_path)
        document = ET.parse(urdf)
        if document.findall("joint") or len(document.findall("link")) != 1:
            raise ValueError("unsupported articulation/multilink asset")
        for mesh in document.findall(".//mesh"):
            ref = mesh.get("filename", "")
            path = _member(urdf.parent, ref)
            if path.relative_to(root).as_posix() not in names:
                raise ValueError("unbound mesh resource")
        physics = json.loads(_member(root, e.physics_path).read_bytes())
        for key in ("mass_kg", "friction"):
            value = physics[key]
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid supplied physics")
        if physics["mass_kg"] == 0:
            raise ValueError("dynamic mass must be positive")


def run_scene(
    scene,
    *,
    package_root,
    runtime_roots,
    output_dir,
    profile="baseline",
    denied_roots=(),
    timeout_seconds=600,
):
    """Launch a fresh declared CPU runtime; result never grants physical qualification."""
    output = Path(output_dir).absolute()
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    result = {
        "status": "failed",
        "simulator_executed": False,
        "physical_profile": "not_run",
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
    try:
        scene = RuntimeScene.model_validate(scene)
        root = Path(package_root).resolve()
        _verify(scene, root)
        required = {"interpreter", "stdlib", "distributions", "native", "genesis"}
        if not required <= runtime_roots.keys() or any(
            not Path(runtime_roots[k]).is_dir() for k in required
        ):
            result["error_code"] = "missing_runtime_dependency"
            raise ValueError("declared runtime roots unavailable")
        if profile not in {"baseline", "half_dt", "load_step_smoke"}:
            result["error_code"] = "unsupported_profile"
            raise ValueError("unsupported profile")
        roots = {k: str(Path(runtime_roots[k]).resolve()) for k in required}
        denied = [str(Path(p).resolve()) for p in denied_roots]
        allowed = [root, output.resolve(), *(Path(p) for p in roots.values())]
        if any(
            Path(d).is_relative_to(a) or a.is_relative_to(Path(d)) for d in denied for a in allowed
        ):
            raise ValueError("runtime/package/output overlaps denied root")
        payload = scene.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        job = {
            "scene": payload,
            "scene_sha256": digest,
            "package_root": str(root),
            "roots": roots,
            "output": str(output),
            "profile": profile,
            "denied_roots": denied,
        }
        _write(output / "job.json", job)
        shutil.copyfile(Path(__file__).with_name("genesis_child.py"), output / "genesis_child.py")
        for name in ("home", "tmp", "cache"):
            (output / name).mkdir()
        env = _environment(roots, output)
        native = Path(roots["native"]) / "usr/lib/x86_64-linux-gnu"
        command = [
            str(native / "ld-linux-x86-64.so.2"),
            "--library-path",
            str(native),
            str(Path(roots["interpreter"]) / "python3.12"),
            "-X",
            "frozen_modules=off",
            "-S",
            "-B",
            str(output / "genesis_child.py"),
            str(output / "job.json"),
        ]
        _write(
            output / "invocation.json",
            {
                "command": command,
                "environment": env,
                "child_sha256": hashlib.sha256(
                    (output / "genesis_child.py").read_bytes()
                ).hexdigest(),
            },
        )
        with (
            (output / "stdout.log").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=output,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                start_new_session=True,
            )
            lifecycle = {
                "pid": process.pid,
                "pgid": process.pid,
                "attempt_dir": str(output),
                "command": command,
                "signals": [],
                "started_monotonic": time.monotonic(),
            }
            _write(output / "process.json", lifecycle)
            try:
                process.wait(timeout=timeout_seconds)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                lifecycle["stop_reason"] = (
                    "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                )
                _stop_group(process, lifecycle, output)
                raise
            finally:
                lifecycle["exit_code"] = process.poll()
                lifecycle["ended_monotonic"] = time.monotonic()
                _write(output / "process.json", lifecycle)
        result["exit_code"] = process.returncode
        if (output / "child-result.json").is_file():
            result.update(json.loads((output / "child-result.json").read_bytes()))
        if process.returncode:
            result["error_code"] = "runtime_execution_failed"
            raise ValueError("Genesis child failed; retained stderr and partial products")
        if result.get("status") != "passed" or result.get("scene_sha256") != digest:
            raise ValueError("missing or unbound child result")
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        result["status"] = "cancelled"
        result["error_code"] = (
            "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
        )
        result["reason"] = str(exc)
        result["partial_products_retained"] = True
    except (ValueError, OSError) as exc:
        result["status"] = "failed"
        result.setdefault("error_code", "invalid_runtime_request")
        result["reason"] = str(exc)
    result["wall_seconds"] = time.monotonic() - start
    _write(output / "result.json", result)
    return result


def _stop_group(process, lifecycle, output):
    """Signal only the session just created for this attempt; never fuzzy process matching."""
    for sig, grace in [(signal.SIGINT, 10.0), (signal.SIGTERM, 10.0)]:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            lifecycle["group_exited"] = True
            return
        os.killpg(process.pid, sig)
        lifecycle["signals"].append(
            {"signal": sig.name, "pgid": process.pid, "monotonic": time.monotonic()}
        )
        _write(output / "process.json", lifecycle)
        until = time.monotonic() + grace
        while time.monotonic() < until:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                lifecycle["group_exited"] = True
                return
            time.sleep(0.05)
    lifecycle["group_exited"] = False
    lifecycle["cleanup_error"] = "owned_process_group_did_not_exit_after_INT_TERM"


def _environment(roots, output):
    distributions = roots["distributions"] + "/lib/python3.12/site-packages"
    native = roots["native"] + "/usr/lib/x86_64-linux-gnu"
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONUTF8": "0",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": roots["stdlib"] + ":" + roots["stdlib"] + "/lib-dynload",
        "GS_HEADLESS": "1",
        "PYGLET_HEADLESS": "1",
        "GS_PARA_LEVEL": "0",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "QD_NUM_THREADS": "1",
        "QD_OFFLINE_CACHE": "0",
        "QD_ENABLE_PYBUF": "0",
        "TMPDIR": str(output / "tmp"),
        "GS_CACHE_FILE_PATH": str(output / "cache/genesis"),
        "XDG_CACHE_HOME": str(output / "cache"),
        "MPLCONFIGDIR": str(output / "cache/matplotlib"),
        "PYGLFW_LIBRARY": distributions + "/glfw/x11/libglfw.so",
        "LD_LIBRARY_PATH": native,
        "LD_PRELOAD": native + "/libGLX.so.0.0.0:" + native + "/libOpenGL.so.0.0.0",
        "HOME": str(output / "home"),
        "MADRONA_ROOT_PATH": distributions + "/gs_madrona",
        "MADRONA_ROOT_CACHE_DIR": str(output / "home/.cache/madrona"),
        "PYOPENGL_PLATFORM": "osmesa",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "MESA_SHADER_CACHE_DISABLE": "true",
        "__EGL_VENDOR_LIBRARY_FILENAMES": roots["native"]
        + "/usr/share/glvnd/egl_vendor.d/50_mesa.json",
        "IMAGEIO_FFMPEG_EXE": distributions + "/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2",
    }
