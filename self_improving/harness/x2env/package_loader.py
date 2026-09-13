"""Standard-library portable package verifier and shared fresh-process launcher.

Launch/environment/INT-TERM semantics derive from canonical genesis_runtime and
Harness c0236bd; no Store, workflow, model or qualification authority lives here.
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

AUDIT_ROOT = "_support_audit/harness/x2env"
AUDIT_FILES = (
    "genesis_runtime.py",
    "package_loader.py",
    "measured_support.py",
    "assets.py",
    "artifacts.py",
    "contracts.py",
    "assessment.py",
    "genesis_child.py",
)


def _write(path, body):
    path.write_text(json.dumps(body, sort_keys=True, indent=2, allow_nan=False) + "\n")


def _safe(root, name):
    relative = PurePosixPath(name)
    if (
        not name
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in name
        or ":" in name
        or relative.as_posix() != name
    ):
        raise ValueError("unsafe package relative path")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("unsafe package symlink/escape")
    return path


def verify_package(root):
    """Check explicit package-owned closure without resolving any CAS or audit locator."""
    root = Path(root).absolute()
    manifest = json.loads(_safe(root, "manifest.json").read_bytes())
    if manifest.get("schema_version") != "x2env.development_package.v1":
        raise ValueError("unsupported development package")
    seen = set()
    for item in manifest["members"]:
        path = _safe(root, item["path"])
        if item["path"] in seen:
            raise ValueError("duplicate package member")
        seen.add(item["path"])
        raw = path.read_bytes()
        if len(raw) != item["size_bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("corrupt package member")
    for key in ["scene", "entrypoint", "child"]:
        if manifest[key] not in seen:
            raise ValueError("unlisted package entrypoint")
    if manifest.get("status") != "development_review" or manifest.get("sim_ready") is not False:
        raise ValueError("development package cannot grant qualification")
    scene = json.loads(_safe(root, manifest["scene"]).read_bytes())
    if (
        scene.get("schema_version") not in {"x2env.runtime_scene.v1", "x2env.runtime_scene.v2"}
        or type(scene.get("seed")) is not int
        or not 0 <= scene["seed"] <= 2147483647
    ):
        raise ValueError("invalid runtime scene schema/seed")
    asset_members = {m["path"]: m for m in scene["members"]}
    package_members = {m["path"]: m for m in manifest["members"]}
    if len(asset_members) != len(scene["members"]):
        raise ValueError("duplicate scene member")
    for name, member in asset_members.items():
        if package_members.get(name) != member:
            raise ValueError("unlisted or changed scene asset")
    ids = set()
    if not 1 <= len(scene["entities"]) <= 8:
        raise ValueError("unsupported entity count")
    for entity in scene["entities"]:
        if entity["id"] in ids or entity["id"] == "ground":
            raise ValueError("invalid entity ID")
        ids.add(entity["id"])
        for key, count in [("position_m", 3), ("orientation_wxyz", 4)]:
            vector = entity[key]
            if len(vector) != count or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in vector
            ):
                raise ValueError("invalid resolved numeric pose")
        if abs(sum(v * v for v in entity["orientation_wxyz"]) - 1) > 1e-6:
            raise ValueError("invalid quaternion")
        if entity["kind"] == "rigid":
            for key in ["urdf_path", "physics_path"]:
                _safe(root, entity[key])
                if entity[key] not in asset_members:
                    raise ValueError("unlisted runtime asset")
            document = ET.parse(root / entity["urdf_path"])
            if document.findall("joint") or len(document.findall("link")) != 1:
                raise ValueError("unsupported articulation")
            for mesh in document.findall(".//mesh") + document.findall(".//texture"):
                resource = _safe((root / entity["urdf_path"]).parent, mesh.get("filename", ""))
                if resource.relative_to(root).as_posix() not in asset_members:
                    raise ValueError("unlisted mesh resource")
        elif entity["kind"] == "structural_box":
            size = entity["size_m"]
            if (
                entity["category"] not in {"table", "worktop", "counter"}
                or len(size) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in size)
            ):
                raise ValueError("unsupported foreground primitive")
        else:
            raise ValueError("unsupported entity kind")
    for relation in scene["relations"]:
        if (
            relation["relation"] not in {"on", "left_of", "right_of", "front_of", "behind", "near"}
            or relation["source"] not in ids
            or relation["target"] not in ids
            or relation["source"] == relation["target"]
        ):
            raise ValueError("unsupported or unbound relation")
    for name in asset_members:
        path = root / name
        if path.suffix.lower() == ".glb":
            raw = path.read_bytes()
            if len(raw) < 20 or raw[:4] != b"glTF":
                raise ValueError("invalid GLB")
            count = struct.unpack("<I", raw[12:16])[0]
            document = json.loads(raw[20 : 20 + count])
            if document.get("skins") or document.get("animations"):
                raise ValueError("unsupported articulated GLB")
            for group in ["buffers", "images"]:
                if any(
                    v.get("uri") and not v["uri"].startswith("data:")
                    for v in document.get(group, [])
                ):
                    raise ValueError("external GLB resource")
        elif path.suffix.lower() == ".obj" and any(
            line.strip().startswith(("mtllib ", "usemtl "))
            for line in path.read_text().splitlines()
        ):
            raise ValueError("unsupported OBJ material closure")
    if scene["schema_version"] == "x2env.runtime_scene.v2":
        required = {f"{AUDIT_ROOT}/{name}" for name in (*AUDIT_FILES, "__init__.py")}
        required.add("_support_audit/golden_e2e_progress/physics-assertions-v1.json")
        if not required <= seen:
            raise ValueError("missing package-owned support verifier")
        namespace = "_x2env_support_" + hashlib.sha256(str(root).encode()).hexdigest()
        spec = importlib.util.spec_from_file_location(
            namespace,
            _safe(root, f"{AUDIT_ROOT}/__init__.py"),
            submodule_search_locations=[str(_safe(root, AUDIT_ROOT))],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = module
        try:
            spec.loader.exec_module(module)
            runtime = __import__(f"{namespace}.genesis_runtime", fromlist=["parse_runtime_scene"])
            runtime._verify(runtime.parse_runtime_scene(scene), root)
        finally:
            for key in tuple(sys.modules):
                if key == namespace or key.startswith(namespace + "."):
                    del sys.modules[key]
    return manifest


def launch_child(
    scene,
    *,
    package_root,
    child_path,
    runtime_roots,
    output,
    profile="baseline",
    denied_roots=(),
    timeout_seconds=600,
):
    """Shared pure process adapter; callers own typed scene/asset/manifest validation."""
    output = Path(output).absolute()
    if output == Path("/") or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("unsafe execution output")
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
        required = {"interpreter", "stdlib", "distributions", "native", "genesis"}
        if not required <= runtime_roots.keys() or any(
            not Path(runtime_roots[k]).is_dir() for k in required
        ):
            result["error_code"] = "missing_runtime_dependency"
            raise ValueError("declared runtime roots unavailable")
        roots = {k: str(Path(runtime_roots[k]).resolve()) for k in required}
        root = Path(package_root).resolve()
        if root == Path("/") or any(Path(p) == Path("/") for p in roots.values()):
            raise ValueError("root filesystem cannot be an execution root")
        if profile not in {"baseline", "half_dt", "load_step_smoke"}:
            result["error_code"] = "unsupported_profile"
            raise ValueError("unsupported profile")
        denied = [str(Path(p).resolve()) for p in denied_roots]
        allowed = [root, output.resolve(), *(Path(p) for p in roots.values())]
        if any(
            Path(d).is_relative_to(a) or a.is_relative_to(Path(d)) for d in denied for a in allowed
        ):
            raise ValueError("runtime/package/output overlaps denied root")
        digest = hashlib.sha256(
            json.dumps(scene, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        job = {
            "scene": scene,
            "scene_sha256": digest,
            "package_root": str(root),
            "roots": roots,
            "output": str(output),
            "profile": profile,
            "denied_roots": denied,
        }
        _write(output / "job.json", job)
        shutil.copyfile(child_path, output / "genesis_child.py")
        for folder in ["home", "tmp", "cache"]:
            (output / folder).mkdir()
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
                process_stat = Path(f"/proc/{process.pid}/stat").read_text()
                lifecycle["start_ticks"] = int(process_stat.rsplit(")", 1)[1].split()[19])
                _write(output / "process.json", lifecycle)
                process.wait(timeout=timeout_seconds)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                lifecycle["stop_reason"] = (
                    "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                )
                _stop_group(process, lifecycle, output)
                raise
            except (OSError, ValueError, IndexError) as exc:
                lifecycle["stop_reason"] = "process_identity_unavailable"
                _stop_group(process, lifecycle, output)
                raise ValueError("owned runtime process identity unavailable") from exc
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
        result.update(
            status="cancelled",
            error_code="timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted",
            reason=str(exc),
            partial_products_retained=True,
        )
    except (ValueError, OSError) as exc:
        result["status"] = "failed"
        result.setdefault("error_code", "invalid_runtime_request")
        result["reason"] = str(exc)
    result["wall_seconds"] = time.monotonic() - start
    _write(output / "result.json", result)
    return result


def run_package(
    package, *, runtime_roots, output, profile="baseline", denied_roots=(), timeout_seconds=600
):
    """Run only package-owned validated scene/assets/child in the declared environment."""
    root = Path(package).absolute()
    manifest = verify_package(root)
    return launch_child(
        json.loads((root / manifest["scene"]).read_bytes()),
        package_root=root,
        child_path=root / manifest["child"],
        runtime_roots=runtime_roots,
        output=output,
        profile=profile,
        denied_roots=denied_roots,
        timeout_seconds=timeout_seconds,
    )


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a verified development Genesis package.")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile", choices=["baseline", "half_dt", "load_step_smoke"], default="baseline"
    )
    parser.add_argument("--deny-root", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    result = run_package(
        Path(__file__).resolve().parent,
        runtime_roots=json.loads(args.runtime.read_bytes()),
        output=args.output,
        profile=args.profile,
        denied_roots=args.deny_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
