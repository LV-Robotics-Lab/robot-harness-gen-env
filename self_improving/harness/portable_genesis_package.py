"""Portable experimental scene packages; no qualification or publication authority."""

import hashlib
import json
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _path(root, relative):
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or "\\" in relative:
        raise ValueError("unsafe package path")
    path = root / relative
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink package path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("package path escapes root")
    return path


def load_package(root):
    """Verify local member bytes without consulting a source workspace or CAS."""
    root = Path(root)
    manifest = json.loads(_path(root, "environment.json").read_bytes())
    if manifest.get("schema_version") != "harness.portable_genesis_package.v1":
        raise ValueError("unsupported portable package")
    seen = set()
    for member in manifest["members"]:
        path = _path(root, member["path"])
        if member["path"] in seen:
            raise ValueError("duplicate package member")
        seen.add(member["path"])
        data = path.read_bytes()
        if len(data) != member["bytes"] or hashlib.sha256(data).hexdigest() != member["sha256"]:
            raise ValueError("corrupt package member")
    for field in ("scene", "urdf", "entrypoint"):
        if field in manifest and manifest[field] not in seen:
            raise ValueError("unlisted package entrypoint: " + field)
    for relative in seen:
        path = root / relative
        if path.suffix == ".urdf":
            references = [
                node.attrib["filename"]
                for node in ET.fromstring(path.read_bytes()).iter()
                if "filename" in node.attrib
            ]
        elif path.suffix in {".obj", ".mtl"}:
            references = [
                line.split(maxsplit=1)[1].strip()
                for line in path.read_text().splitlines()
                if line.strip().startswith(("mtllib ", "map_Kd ", "map_Ka ", "map_Bump "))
            ]
        else:
            references = []
        for reference in references:
            if PurePosixPath(reference).is_absolute() or ":" in reference or "\\" in reference:
                raise ValueError("absolute or remote asset resource")
            resolved = (path.parent / reference).resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ValueError("asset resource escapes package")
            if resolved.relative_to(root.resolve()).as_posix() not in seen:
                raise ValueError("asset resource is not a package member")
    return manifest


def export_package(store, *, scene_ref, output, provenance=None, reports=(), media=()):
    """Export one finite-table scene and its full representation with a local runner."""
    from .genesis_scene_launcher import GENESIS_SCENE_ENVIRONMENT
    from .schemas.common import ArtifactRef

    output = Path(output).absolute()
    if output.exists():
        raise ValueError("package output must be new")
    output.mkdir(parents=True)

    def read(ref):
        ref = ArtifactRef.model_validate(ref) if isinstance(ref, dict) else ref
        data = store.resolve(ref).path.read_bytes()
        if len(data) != ref.bytes or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("source member differs from artifact identity")
        return data

    try:
        raw = read(scene_ref)
        scene = json.loads(raw)
        if scene.get("schema_version") != "harness.genesis_scene_spec.v1":
            raise ValueError("portable runner requires finite-table scene v1")
        (output / "scene.json").write_bytes(raw)
        representation = json.loads(read(scene["representation"]))
        for member in representation["members"]:
            target = _path(output, "assets/" + member["logical_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(member["artifact"]))
        for group, refs in (("reports", reports), ("media", media)):
            for index, ref in enumerate(refs):
                name = ref["name"] if isinstance(ref, dict) else ref.name
                target = _path(output, f"{group}/{index}-{Path(name).name}")
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(read(ref))
        _write(output / "provenance.json", provenance or {})
        _write(
            output / "runtime.json",
            {
                "python": "3.12",
                "genesis_version": "1.3.3",
                "genesis_commit": "0e74bf392781884ccad765c3f344419c86b872ca",
                "renderer": "OSMesa",
                "external_runtime_required": True,
                "roles": ["interpreter", "stdlib", "distributions", "native", "genesis"],
                "environment": GENESIS_SCENE_ENVIRONMENT,
            },
        )
        runner = output / "runner"
        runner.mkdir()
        (runner / "__init__.py").write_text("")
        for name in (
            "genesis_scene_cpu_backend.py",
            "_genesis_cpu_child.py",
            "_genesis_scene_child.py",
        ):
            shutil.copyfile(Path(__file__).with_name(name), runner / name)
        shutil.copyfile(Path(__file__), output / "portable_genesis_package.py")
        shutil.copyfile(Path(__file__).with_name("_portable_genesis_child.py"), output / "run.py")
        members = []
        for path in sorted(output.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                members.append(
                    {
                        "path": path.relative_to(output).as_posix(),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        manifest = {
            "schema_version": "harness.portable_genesis_package.v1",
            "members": members,
            "scene": "scene.json",
            "urdf": "assets/" + representation["root_logical_path"],
            "entrypoint": "run.py",
            "qualification_status": "development_unqualified",
            "profile": "single_rigid_finite_table_baseline",
            "robot_policy_evaluated": False,
            "data_collection_evaluated": False,
            "checks": {
                "member_integrity": "passed",
                "relocated_execution": "not_run",
                "physical_profile": "not_run",
            },
        }
        _write(output / "environment.json", manifest)
        load_package(output)
        return manifest
    except Exception as error:
        _write(output / "export-failure.json", {"status": "failed", "reason": str(error)})
        raise


def run_package(root, *, runtime_roots, output, denied_roots=(), timeout=600, profile="baseline"):
    """Run copied package in a confined child; retain failures and real output."""
    root, output = Path(root).resolve(), Path(output).absolute()
    manifest = load_package(root)
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    result = {
        "status": "failed",
        "simulator_executed": False,
        "robot_policy_evaluated": False,
        "data_collection_evaluated": False,
    }
    try:
        if profile not in ("baseline", "half_dt"):
            result["error_code"] = "unsupported_replay_profile"
            raise ValueError("only fixed baseline and half_dt profiles are supported")
        required = {"interpreter", "stdlib", "distributions", "native", "genesis"}
        if not required <= runtime_roots.keys() or any(
            not Path(runtime_roots[key]).is_dir() for key in required
        ):
            result["error_code"] = "missing_runtime_dependency"
            raise ValueError("declared runtime roots unavailable")
        roots = {key: str(Path(runtime_roots[key]).resolve()) for key in required}
        denied = [str(Path(p).resolve()) for p in denied_roots]
        allowed = [root, output, *(Path(p) for p in roots.values())]
        if any(
            Path(d).is_relative_to(a) or a.is_relative_to(Path(d)) for d in denied for a in allowed
        ):
            raise ValueError("runtime/package/output overlaps denied roots")
        job = {
            "root": str(root),
            "output": str(output),
            "roots": roots,
            "profile": profile,
            "denied_roots": denied,
            "manifest": manifest,
        }
        _write(output / "job.json", job)
        native = Path(roots["native"]) / "usr/lib/x86_64-linux-gnu"
        env = json.loads((root / "runtime.json").read_bytes())["environment"]
        replacements = {"${" + key.upper() + "}": value for key, value in roots.items()}
        replacements.update({"${RUN}": str(output), "${USER_HOME}": str(output / "home")})
        for token, replacement in replacements.items():
            env = {key: value.replace(token, replacement) for key, value in env.items()}
        env["LD_LIBRARY_PATH"] = str(native)
        for folder in ("home", "tmp", "cache"):
            (output / folder).mkdir()
        command = [
            str(native / "ld-linux-x86-64.so.2"),
            "--library-path",
            str(native),
            str(Path(roots["interpreter"]) / "python3.12"),
            "-X",
            "frozen_modules=off",
            "-S",
            "-B",
            str(root / "run.py"),
            "--child",
            str(output / "job.json"),
        ]
        with (
            (output / "stdout.log").open("wb") as stdout,
            (output / "stderr.log").open("wb") as stderr,
        ):
            done = subprocess.run(
                command,
                env=env,
                cwd=output,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                timeout=timeout,
            )
        result["exit_code"] = done.returncode
        if done.returncode:
            raise ValueError("portable Genesis child failed")
        result.update(json.loads((output / "child-result.json").read_bytes()))
    except Exception as error:
        result.setdefault("error_code", "package_execution_failed")
        result["reason"] = str(error)
    result["wall_seconds"] = time.monotonic() - start
    _write(output / "result.json", result)
    return result
