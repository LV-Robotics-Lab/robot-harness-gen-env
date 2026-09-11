"""Declared-runtime dual-profile scene runs, with no asset or execution qualification.

Renderer-enabled process confinement is a separate qualification gate. This producer
retains real outputs while its only consumer authority remains trace consistency.
"""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from .genesis_runtime_launcher import GENESIS_RUNTIME_ENVIRONMENT
from .genesis_runtime_manifest import verify_runtime_deployment
from .schemas.common import ArtifactRef
from .schemas.genesis_scene_replay import GenesisSceneReplayV1
from .schemas.x2env_compile_evidence import AssetRepresentationV1

GENESIS_SCENE_ENVIRONMENT = {
    **GENESIS_RUNTIME_ENVIRONMENT,
    "PYOPENGL_PLATFORM": "osmesa",
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "MESA_SHADER_CACHE_DISABLE": "true",
    "__EGL_VENDOR_LIBRARY_FILENAMES": "${NATIVE}/usr/share/glvnd/egl_vendor.d/50_mesa.json",
    "IMAGEIO_FFMPEG_EXE": (
        "${DISTRIBUTIONS}/lib/python3.12/site-packages/imageio_ffmpeg/binaries/"
        "ffmpeg-linux-x86_64-v7.0.2"
    ),
}


class GenesisSceneLaunchError(ValueError):
    """A failed scene launch with retained diagnostic closure."""

    failure_artifacts = ()


def execute_genesis_scene_request(*, store, request_ref, roots):
    """Execute fixed typed bytes; the owning producer verifies qualification authority."""
    from .schemas.genesis_scene_execution import (
        GenesisSceneExecutionRequestV1,
    )

    if (
        request_ref.schema_version != "harness.genesis_scene_execution_request.v1"
        or request_ref.media_type != "application/json"
    ):
        raise GenesisSceneLaunchError("scene request must have its exact typed CAS reference")
    request = GenesisSceneExecutionRequestV1.model_validate_json(
        store.resolve(request_ref).path.read_bytes()
    )
    roots = {key: Path(value) for key, value in roots.items()}
    run = Path(tempfile.mkdtemp(prefix="golden-scene-execution-")) / "outputs"
    replay = launch_genesis_scene_replay(
        store, request.scene, request.runtime_manifest, roots=roots, output_dir=run
    )
    try:
        return _publish_scene_launch_result(store, request_ref, request, roots, run, replay)
    except Exception as cause:
        error = GenesisSceneLaunchError(str(cause))
        error.failure_artifacts = (
            store.put_file(
                run / "retained-output.json",
                name="retained-output.json",
                media_type="application/json",
                schema_version=None,
            ),
        )
        raise error from cause


def _publish_scene_launch_result(store, request_ref, request, roots, run, replay):
    from .genesis_runtime_manifest import read_runtime_manifest
    from .schemas.genesis_scene_execution import (
        GenesisSceneLaunchResultV1,
        GenesisSceneProcessObservationV1,
    )

    manifest = read_runtime_manifest(store, request.runtime_manifest)
    declared = {(item.root, item.path): item for item in manifest.members}
    observations = {}
    for profile in ("baseline", "half_dt"):
        raw_path = run / profile / "renderer-runtime.json"
        raw = json.loads(raw_path.read_bytes())
        mapped = {}
        for field in ("modules", "native_files"):
            members = {}
            for item in raw[field]:
                path = Path(item["path"])
                matching = [
                    (role, path.relative_to(root).as_posix())
                    for role, root in roots.items()
                    if path.is_relative_to(root)
                ]
                if len(matching) != 1:
                    raise GenesisSceneLaunchError("scene observation root mapping differs")
                member = declared[matching[0]]
                if member.artifact.sha256 != item["sha256"]:
                    raise GenesisSceneLaunchError("scene observed member changed")
                members[matching[0]] = member
            mapped[field] = tuple(members[key] for key in sorted(members))
        streams = {
            stream: store.put_file(
                run / (profile + "-" + stream + ".txt"),
                name=profile + "-" + stream + ".txt",
                media_type="text/plain",
                schema_version=None,
            )
            for stream in ("stdout", "stderr")
        }
        observation = GenesisSceneProcessObservationV1(
            schema_version="harness.genesis_scene_process_observation.v1",
            request=request_ref,
            profile=profile,
            process_identity=manifest.process_identity,
            environment_delta={"LD_LIBRARY_PATH": GENESIS_SCENE_ENVIRONMENT["LD_LIBRARY_PATH"]},
            **mapped,
            external_native_files=(),
            torch_threads=(1, 1),
            landlock_abi=raw["landlock_abi"],
            network_denied=True,
            runtime_write_denied=True,
            kernel_devices=("/dev/null", "/dev/urandom"),
            network_policy="socket_syscalls_denied",
            filesystem_policy="runtime_roots_readonly_kernel_devices_run_output_write_inherited_children",
            raw_observation=store.put_file(
                raw_path,
                name=profile + "-raw-observation.json",
                media_type="application/json",
                schema_version=None,
            ),
            **streams,
        )
        path = run / (profile + "-observation.json")
        path.write_text(
            json.dumps(observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        observations[profile + "_observation"] = store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=observation.schema_version,
        )
    result = GenesisSceneLaunchResultV1(
        schema_version="harness.genesis_scene_launch_result.v1",
        request=request_ref,
        replay=replay,
        **observations,
    )
    path = run / "launch-result.json"
    path.write_text(
        json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    return store.put_file(
        path, name=path.name, media_type="application/json", schema_version=result.schema_version
    )


def launch_genesis_scene_replay(store, scene_ref, runtime_manifest_ref, *, roots, output_dir):
    """Run two fixed fresh children and publish CAS evidence for a pure-read consumer."""
    if type(runtime_manifest_ref) is not ArtifactRef:
        raise ValueError("scene execution requires an explicit runtime manifest")
    from .genesis_runtime_manifest import read_runtime_manifest

    manifest = read_runtime_manifest(store, runtime_manifest_ref)
    if dict(manifest.process_identity.environment) != GENESIS_SCENE_ENVIRONMENT:
        raise GenesisSceneLaunchError("scene environment differs from fixed software profile")
    for filename in (
        "genesis_scene_cpu_backend.py",
        "_genesis_scene_child.py",
        "genesis_scene_launcher.py",
    ):
        candidates = [
            m
            for m in manifest.members
            if m.root == "backend" and m.path == "self_improving/harness/" + filename
        ]
        if (
            len(candidates) != 1
            or candidates[0].artifact.sha256
            != hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
        ):
            raise ValueError("scene runtime manifest does not contain the installed fixed runner")
    verify_runtime_deployment(
        store, runtime_manifest_ref, roots=roots, process_identity=manifest.process_identity
    )
    from .gujie_scene_adapter import read_gujie_tabletop_scene

    spec = read_gujie_tabletop_scene(store, scene_ref)
    representation = AssetRepresentationV1.model_validate_json(
        store.resolve(spec.representation).path.read_bytes()
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    materialized = output_dir / "asset"
    for member in representation.members:
        path = materialized / member.logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store.resolve(member.artifact).path.read_bytes())
    outputs = {}
    preview_refs = {}
    try:
        for profile in ("baseline", "half_dt"):
            profile_dir = output_dir / profile
            job = {
                "scene_spec": spec.model_dump(mode="json"),
                "urdf_path": str(materialized / representation.root_logical_path),
                "profile": profile,
                "output_dir": str(profile_dir),
                "roots": {k: str(v) for k, v in roots.items()},
            }
            job_path = output_dir / (profile + "-job.json")
            job_path.write_text(json.dumps(job, sort_keys=True, separators=(",", ":")))
            environment = dict(manifest.process_identity.environment)
            replacements = {"${" + key.upper() + "}": str(value) for key, value in roots.items()}
            replacements.update(
                {"${RUN}": str(output_dir), "${USER_HOME}": str(output_dir / "home")}
            )
            for token, value in replacements.items():
                environment = {key: item.replace(token, value) for key, item in environment.items()}
            observed_environment = dict(environment)
            environment["LD_LIBRARY_PATH"] = str(roots["native"] / "usr/lib/x86_64-linux-gnu")
            job["expected_process_identity"] = manifest.process_identity.model_dump(mode="json")
            job["startup_environment"] = environment
            job_path.write_text(json.dumps(job, sort_keys=True, separators=(",", ":")))
            for path in (output_dir / "tmp", output_dir / "home", output_dir / "cache"):
                path.mkdir(exist_ok=True)
            native = roots["native"] / "usr/lib/x86_64-linux-gnu"
            command = [
                str(native / "ld-linux-x86-64.so.2"),
                "--library-path",
                str(native),
                str(roots["interpreter"] / "python3.12"),
                "-X",
                "frozen_modules=off",
                "-S",
                "-B",
                str(roots["backend"] / "self_improving/harness/_genesis_scene_child.py"),
                str(job_path),
            ]
            with (
                (output_dir / (profile + "-stdout.txt")).open("wb") as stdout,
                (output_dir / (profile + "-stderr.txt")).open("wb") as stderr,
            ):
                completed = subprocess.run(
                    command,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=300,
                    close_fds=True,
                )
            if completed.returncode:
                raise ValueError(f"scene {profile} child failed with status {completed.returncode}")
            observation = json.loads((profile_dir / "renderer-runtime.json").read_bytes())
            if (
                observation["schema_version"] != "harness.genesis_scene_process_observation.v1"
                or observation["landlock_abi"] < 6
                or observation["network_denied"] is not True
                or observation["runtime_write_denied"] is not True
                or observation["torch_threads"] != [1, 1]
                or observation["environment"] != observed_environment
                or observation["startup_environment"] != environment
                or observation["process_identity"]
                != {
                    key: value
                    for key, value in manifest.process_identity.model_dump(mode="json").items()
                    if key != "environment"
                }
            ):
                raise GenesisSceneLaunchError("scene process observation differs")
            declared = {(item.root, item.path): item.artifact.sha256 for item in manifest.members}
            for member in observation["modules"] + observation["native_files"]:
                path = Path(member["path"])
                matches = [
                    (role, path.relative_to(root).as_posix())
                    for role, root in roots.items()
                    if path.is_relative_to(root)
                ]
                if len(matches) != 1 or declared.get(matches[0]) != member["sha256"]:
                    raise GenesisSceneLaunchError(
                        "scene observed runtime member is undeclared or changed"
                    )
            media = json.loads((profile_dir / "media.json").read_text())

            def publish(path, media_type, schema=None):
                return store.put_file(
                    path, name=path.name, media_type=media_type, schema_version=schema
                )

            outputs[profile] = {
                "profile": profile,
                "rows": publish(
                    profile_dir / "trace.ndjson",
                    "application/x-ndjson",
                    "harness.genesis_scene_trace_row.v1",
                ),
                "loaded_scene": publish(profile_dir / "loaded.json", "application/json"),
                "frames": [
                    {
                        "step": frame["step"],
                        "image": publish(profile_dir / frame["path"], "image/png"),
                    }
                    for frame in media["frames"]
                ],
                "video": publish(profile_dir / "simulation.mkv", "video/x-matroska"),
            }
            preview_refs[profile] = publish(profile_dir / "preview.mp4", "video/mp4").model_dump(
                mode="json"
            )
        verify_runtime_deployment(
            store, runtime_manifest_ref, roots=roots, process_identity=manifest.process_identity
        )
        replay = GenesisSceneReplayV1(
            schema_version="harness.genesis_scene_replay.v1",
            scene=scene_ref,
            runtime_manifest=runtime_manifest_ref,
            **outputs,
        )
        path = output_dir / "scene-replay.json"
        path.write_text(
            json.dumps(replay.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        return store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=replay.schema_version,
        )
    except Exception as cause:
        error = GenesisSceneLaunchError(str(cause))
        raise error from cause
    finally:
        files = []
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and not path.is_relative_to(materialized):
                ref = store.put_file(
                    path,
                    name=path.relative_to(output_dir).as_posix(),
                    media_type="application/octet-stream",
                    schema_version=None,
                )
                files.append(ref.model_dump(mode="json"))
        (output_dir / "retained-output.json").write_text(
            json.dumps(
                {
                    "execution_authority": False,
                    "qualified": False,
                    "files": files,
                    "preview_refs": preview_refs,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        retained = store.put_file(
            output_dir / "retained-output.json",
            name="retained-output.json",
            media_type="application/json",
            schema_version=None,
        )
        if "error" in locals():
            error.failure_artifacts = (retained,)
