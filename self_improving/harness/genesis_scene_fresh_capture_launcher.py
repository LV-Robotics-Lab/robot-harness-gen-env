"""Fixed independent fresh camera process, with complete runtime and output closure."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from .genesis_runtime_manifest import read_runtime_manifest, verify_runtime_deployment
from .genesis_scene_launcher import GENESIS_SCENE_ENVIRONMENT, retain_scene_outputs_v2
from .gujie_scene_adapter import read_gujie_tabletop_scene
from .schemas.genesis_fresh_capture import (
    FRESH_CAPTURE_REQUEST_ID,
    FRESH_CAPTURE_RESULT_ID,
    GenesisFreshCaptureRequestV1,
    GenesisFreshCaptureResultV1,
)
from .schemas.x2env_compile_evidence import AssetRepresentationV1


class GenesisFreshCaptureError(ValueError):
    """A failed camera process whose available bytes remain reachable."""

    failure_artifacts = ()


def execute_genesis_fresh_capture(*, store, request_ref, roots):
    """Execute a typed restoration request; the owning application grants authority."""
    if (
        request_ref.schema_version != FRESH_CAPTURE_REQUEST_ID
        or request_ref.media_type != "application/json"
    ):
        raise GenesisFreshCaptureError("fresh capture requires an exact typed request")
    request = GenesisFreshCaptureRequestV1.model_validate_json(
        store.resolve(request_ref).path.read_bytes()
    )
    roots = {key: Path(value) for key, value in roots.items()}
    run = Path(tempfile.mkdtemp(prefix="golden-fresh-capture-")) / "outputs"
    run.mkdir()
    try:
        manifest = read_runtime_manifest(store, request.capture_runtime_manifest)
        if dict(manifest.process_identity.environment) != GENESIS_SCENE_ENVIRONMENT:
            raise GenesisFreshCaptureError("fresh capture runtime environment differs")
        declared = {(item.root, item.path): item for item in manifest.members}
        for filename in (
            "genesis_scene_fresh_capture_backend.py",
            "_genesis_scene_fresh_capture_child.py",
            "genesis_scene_fresh_capture_launcher.py",
        ):
            member = declared.get(("backend", "self_improving/harness/" + filename))
            if (
                member is None
                or member.artifact.sha256
                != hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
            ):
                raise GenesisFreshCaptureError(
                    "fresh capture manifest differs from fixed implementation"
                )
        verify_runtime_deployment(
            store,
            request.capture_runtime_manifest,
            roots=roots,
            process_identity=manifest.process_identity,
        )
        spec = read_gujie_tabletop_scene(store, request.scene)
        rep = AssetRepresentationV1.model_validate_json(
            store.resolve(spec.representation).path.read_bytes()
        )
        asset = run / "asset"
        for member in rep.members:
            path = asset / member.logical_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(store.resolve(member.artifact).path.read_bytes())
        final_environment = dict(manifest.process_identity.environment)
        replacements = {"${" + key.upper() + "}": str(value) for key, value in roots.items()}
        replacements.update({"${RUN}": str(run), "${USER_HOME}": str(run / "home")})
        for token, value in replacements.items():
            final_environment = {
                key: item.replace(token, value) for key, item in final_environment.items()
            }
        startup = dict(final_environment)
        startup["LD_LIBRARY_PATH"] = str(roots["native"] / "usr/lib/x86_64-linux-gnu")
        job = {
            "request_ref": request_ref.model_dump(mode="json"),
            "scene_spec": spec.model_dump(mode="json"),
            "final_state": request.final_state.model_dump(mode="json"),
            "urdf_path": str(asset / rep.root_logical_path),
            "output_dir": str(run / "capture"),
            "roots": {key: str(value) for key, value in roots.items()},
            "expected_process_identity": manifest.process_identity.model_dump(mode="json"),
            "startup_environment": startup,
        }
        job_path = run / "job.json"
        job_path.write_text(json.dumps(job, sort_keys=True, separators=(",", ":")))
        for name in ("tmp", "home", "cache"):
            (run / name).mkdir()
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
            str(roots["backend"] / "self_improving/harness/_genesis_scene_fresh_capture_child.py"),
            str(job_path),
        ]
        with (run / "stdout.txt").open("wb") as stdout, (run / "stderr.txt").open("wb") as stderr:
            child = subprocess.run(
                command,
                env=startup,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                timeout=300,
            )
        if child.returncode:
            raise GenesisFreshCaptureError(
                f"fresh capture child failed with status {child.returncode}"
            )
        raw = json.loads((run / "capture/renderer-runtime.json").read_bytes())
        if (
            raw["schema_version"] != "harness.genesis_fresh_capture_raw.v1"
            or raw["request"] != request_ref.model_dump(mode="json")
            or raw["environment"] != final_environment
            or raw["startup_environment"] != startup
            or raw["process_identity"]
            != {
                k: v
                for k, v in manifest.process_identity.model_dump(mode="json").items()
                if k != "environment"
            }
            or raw["landlock_abi"] < 6
            or raw["network_denied"] is not True
            or raw["runtime_write_denied"] is not True
            or raw["torch_threads"] != [1, 1]
        ):
            raise GenesisFreshCaptureError("fresh capture process observation differs")
        mapped = {}
        for field in ("modules", "native_files"):
            members = {}
            for item in raw[field]:
                path = Path(item["path"])
                matches = [
                    (role, path.relative_to(root).as_posix())
                    for role, root in roots.items()
                    if path.is_relative_to(root)
                ]
                if (
                    len(matches) != 1
                    or matches[0] not in declared
                    or declared[matches[0]].artifact.sha256 != item["sha256"]
                ):
                    raise GenesisFreshCaptureError(
                        "fresh capture observed member is undeclared or changed"
                    )
                members[matches[0]] = declared[matches[0]]
            mapped[field] = tuple(members[key] for key in sorted(members))
        verify_runtime_deployment(
            store,
            request.capture_runtime_manifest,
            roots=roots,
            process_identity=manifest.process_identity,
        )
        capture = json.loads((run / "capture/capture.json").read_bytes())
        if raw["capture_event"] != capture:
            raise GenesisFreshCaptureError("raw capture event differs")
        from PIL import Image

        with Image.open(run / "capture/fresh.png") as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size != (640, 480)
                or hashlib.sha256(image.tobytes()).hexdigest() != capture["pixels_sha256"]
            ):
                raise GenesisFreshCaptureError("fresh capture PNG pixels differ")

        def put(relative, media="application/json"):
            path = run / relative
            return store.put_file(path, name=path.name, media_type=media, schema_version=None)

        result = GenesisFreshCaptureResultV1(
            schema_version=FRESH_CAPTURE_RESULT_ID,
            request=request_ref,
            image=put("capture/fresh.png", "image/png"),
            captured_at=capture["captured_at"],
            pixels_sha256=capture["pixels_sha256"],
            width=640,
            height=480,
            raw_load=put("capture/loaded.json"),
            raw_observation=put("capture/renderer-runtime.json"),
            process_identity=manifest.process_identity,
            environment_delta={"LD_LIBRARY_PATH": GENESIS_SCENE_ENVIRONMENT["LD_LIBRARY_PATH"]},
            **mapped,
            landlock_abi=raw["landlock_abi"],
            network_denied=True,
            runtime_write_denied=True,
            torch_threads=(1, 1),
            physics_steps=0,
            restore_velocity_policy="zero",
            process_continuation=False,
            stdout=put("stdout.txt", "text/plain"),
            stderr=put("stderr.txt", "text/plain"),
            retained_index=retain_scene_outputs_v2(store, run),
        )
        path = run / "fresh-capture-result.json"
        path.write_text(
            json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        )
        return store.put_file(
            path,
            name=path.name,
            media_type="application/json",
            schema_version=FRESH_CAPTURE_RESULT_ID,
        )
    except Exception as cause:
        error = GenesisFreshCaptureError(str(cause))
        error.failure_artifacts = (retain_scene_outputs_v2(store, run),)
        raise error from cause
