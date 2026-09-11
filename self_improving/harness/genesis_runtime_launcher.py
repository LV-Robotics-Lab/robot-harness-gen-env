"""Fixed Genesis process execution; caller scripts are never accepted."""

import hashlib
import json
import os
import subprocess
import sysconfig
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

from pydantic import ValidationError

from .artifacts import LocalArtifactStore
from .genesis_runtime_manifest import verify_runtime_deployment
from .schemas.common import ArtifactRef
from .schemas.genesis_runtime_execution import (
    FILESYSTEM_POLICY,
    GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID,
    GenesisRuntimeExecutionRequestV1,
    GenesisRuntimeObservationV1,
)
from .schemas.genesis_runtime_manifest import RuntimeProcessIdentityV1

GENESIS_RUNTIME_ENVIRONMENT = MappingProxyType(
    {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONUTF8": "0",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "${STDLIB}:${STDLIB}/lib-dynload",
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
        "TMPDIR": "${RUN}/tmp",
        "GS_CACHE_FILE_PATH": "${RUN}/cache/genesis",
        "XDG_CACHE_HOME": "${RUN}/cache",
        "MPLCONFIGDIR": "${RUN}/cache/matplotlib",
        "PYGLFW_LIBRARY": "${DISTRIBUTIONS}/lib/python3.12/site-packages/glfw/x11/libglfw.so",
        "LD_LIBRARY_PATH": (
            "${NATIVE}/usr/lib/x86_64-linux-gnu:"
            "${GENESIS}/genesis/ext/ParticleMesher/ParticleMesherPy"
        ),
        "LD_PRELOAD": (
            "${NATIVE}/usr/lib/x86_64-linux-gnu/libGLX.so.0.0.0:"
            "${NATIVE}/usr/lib/x86_64-linux-gnu/libOpenGL.so.0.0.0"
        ),
        "HOME": "${USER_HOME}",
        "MADRONA_ROOT_PATH": "${DISTRIBUTIONS}/lib/python3.12/site-packages/gs_madrona",
        "MADRONA_ROOT_CACHE_DIR": "${USER_HOME}/.cache/madrona",
    }
)
_GL_NAMES = ("libGLX.so.0.0.0", "libOpenGL.so.0.0.0")


def genesis_runtime_platform_identity() -> str:
    """Bind the current kernel build and machine type without including hostname."""
    kernel = os.uname()
    value = {name: getattr(kernel, name) for name in ("sysname", "machine", "release", "version")}
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return sysconfig.get_platform() + ";kernel=" + digest


class GenesisRuntimeLaunchError(ValueError):
    """The exact runtime request or its deployment cannot be executed."""

    failure_artifacts: tuple[ArtifactRef, ...] = ()


@contextmanager
def _retain_child_failure(store, run):
    try:
        yield
    except Exception as cause:
        refs = []
        for name in ("stdout.txt", "stderr.txt"):
            path = run / name
            if path.exists():
                refs.append(
                    store.put_file(path, name=name, media_type="text/plain", schema_version=None)
                )
        partial = run / "partial-probe.json"
        if partial.exists():
            child_store = LocalArtifactStore(run / "cas")
            for value in json.loads(partial.read_bytes()).values():
                ref = ArtifactRef.model_validate(value)
                store.put_file(
                    child_store.resolve(ref).path,
                    name=ref.name,
                    media_type=ref.media_type,
                    schema_version=ref.schema_version,
                )
            refs.append(
                store.put_file(
                    partial, name=partial.name, media_type="application/json", schema_version=None
                )
            )
        else:
            available = []
            for path in sorted((run / "cas/sha256").glob("*/*")):
                available.append(
                    store.put_file(
                        path,
                        name="partial-cas-" + path.name,
                        media_type="application/octet-stream",
                        schema_version=None,
                    ).model_dump(mode="json")
                )
            partial = run / "partial-cas.json"
            partial.write_text(
                json.dumps(
                    {"complete_probe": False, "available_artifacts": available},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            refs.append(
                store.put_file(
                    partial, name=partial.name, media_type="application/json", schema_version=None
                )
            )
        error = GenesisRuntimeLaunchError(str(cause))
        error.failure_artifacts = tuple(refs)
        raise error from cause


def execute_genesis_runtime_probe(
    *, store: LocalArtifactStore, request_ref: ArtifactRef, roots: Mapping[str, Path]
) -> ArtifactRef:
    """Validate the fixed request before permitting any runtime execution."""
    try:
        GenesisRuntimeExecutionRequestV1.model_validate_json(
            store.resolve(request_ref).path.read_bytes()
        )
    except ValidationError as error:
        raise GenesisRuntimeLaunchError("invalid execution request") from error
    from .genesis_runtime_execution import GenesisRuntimeExecutionVerifier, runtime_backend_identity

    request, manifest, runtime = GenesisRuntimeExecutionVerifier(store).verify_request(request_ref)
    expected_environment = tuple(sorted(GENESIS_RUNTIME_ENVIRONMENT.items()))
    if manifest.process_identity.environment != expected_environment:
        raise GenesisRuntimeLaunchError("execution environment differs from fixed CPU profile")
    if manifest.process_identity.platform != genesis_runtime_platform_identity():
        raise GenesisRuntimeLaunchError("execution kernel platform identity differs")
    declared = {(member.root, member.path): member for member in manifest.members}
    for filename in (
        "genesis_runtime_launcher.py",
        "_genesis_cpu_child.py",
        "genesis_cpu_backend.py",
    ):
        member = declared.get(("backend", "self_improving/harness/" + filename))
        if (
            member is None
            or hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
            != member.artifact.sha256
        ):
            raise GenesisRuntimeLaunchError(
                "execution implementation differs from installed Harness"
            )
    verify_runtime_deployment(
        store, request.manifest, roots=roots, process_identity=manifest.process_identity
    )
    external = tuple(declared[("native", "usr/lib/x86_64-linux-gnu/" + name)] for name in _GL_NAMES)
    for member in external:
        path = Path("/") / member.path
        if (
            path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != member.artifact.sha256
        ):
            raise GenesisRuntimeLaunchError("external native identity differs")
    with tempfile.TemporaryDirectory(prefix="genesis-fixed-execution-") as temporary:
        run = Path(temporary)
        (run / "tmp").mkdir()
        (run / "cache").mkdir()
        child_store = LocalArtifactStore(run / "cas")
        representation = json.loads(store.resolve(request.representation).path.read_bytes())
        for value in (
            request.representation,
            request.runtime_lock,
            *(ArtifactRef.model_validate(item["artifact"]) for item in representation["members"]),
        ):
            source = store.resolve(value).path
            child_store.put_file(
                source,
                name=value.name,
                media_type=value.media_type,
                schema_version=value.schema_version,
            )
        invocation = {
            "roots": {key: str(value) for key, value in roots.items()},
            "representation": request.representation.model_dump(mode="json"),
            "runtime_lock": request.runtime_lock.model_dump(mode="json"),
            "implementation_sha256": runtime_backend_identity(manifest),
            "user_home": os.path.expanduser("~"),
        }
        invocation_path = run / "invocation.json"
        environment = dict(GENESIS_RUNTIME_ENVIRONMENT)
        environment["LD_LIBRARY_PATH"] = "${NATIVE}/usr/lib/x86_64-linux-gnu"
        replacements = {"${" + name.upper() + "}": str(path) for name, path in roots.items()}
        replacements["${RUN}"] = str(run)
        replacements["${USER_HOME}"] = os.path.expanduser("~")
        for token, value in replacements.items():
            environment = {key: item.replace(token, value) for key, item in environment.items()}
        invocation["startup_environment"] = environment
        invocation["expected_process_identity"] = manifest.process_identity.model_dump(mode="json")
        invocation_path.write_text(json.dumps(invocation, sort_keys=True, separators=(",", ":")))
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
            str(roots["backend"] / "self_improving/harness/_genesis_cpu_child.py"),
            str(invocation_path),
        ]
        with _retain_child_failure(store, run):
            with (
                (run / "stdout.txt").open("wb") as stdout,
                (run / "stderr.txt").open("wb") as stderr,
            ):
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    timeout=300,
                    close_fds=True,
                )
            streams = []
            for name in ("stdout", "stderr"):
                path = run / (name + ".txt")
                streams.append(
                    store.put_file(
                        path, name=path.name, media_type="text/plain", schema_version=None
                    )
                )
            if completed.returncode != 0:
                error = GenesisRuntimeLaunchError("fixed Genesis child failed")
                error.failure_artifacts = tuple(streams)
                raise error
            result = json.loads((run / "result.json").read_bytes())
            process = RuntimeProcessIdentityV1.model_validate(result["process_identity"])
            if process != manifest.process_identity or result["torch_threads"] != [1, 1]:
                raise GenesisRuntimeLaunchError("observed process identity differs")
            verify_runtime_deployment(
                store, request.manifest, roots=roots, process_identity=process
            )
            for member in external:
                if (
                    hashlib.sha256((Path("/") / member.path).read_bytes()).hexdigest()
                    != member.artifact.sha256
                ):
                    raise GenesisRuntimeLaunchError("external native identity drifted")
            observed = {}
            for field in ("modules", "native_files"):
                members = {}
                for value in result[field]:
                    path = Path(value["path"])
                    candidates = [
                        (role, path.relative_to(root).as_posix())
                        for role, root in roots.items()
                        if path.is_relative_to(root)
                    ]
                    if not candidates and str(path) in tuple(
                        "/" + member.path for member in external
                    ):
                        candidates = [("native", path.relative_to("/").as_posix())]
                    if (
                        len(candidates) != 1
                        or candidates[0] not in declared
                        or declared[candidates[0]].artifact.sha256 != value["sha256"]
                    ):
                        raise GenesisRuntimeLaunchError("observed member is not declared")
                    members[candidates[0]] = declared[candidates[0]]
                observed[field] = tuple(members[key] for key in sorted(members))
            refs = {
                key: ArtifactRef.model_validate(value)
                for key, value in result["probe_refs"].items()
            }
            for ref in refs.values():
                store.put_file(
                    child_store.resolve(ref).path,
                    name=ref.name,
                    media_type=ref.media_type,
                    schema_version=ref.schema_version,
                )
            observation = GenesisRuntimeObservationV1(
                schema_version=GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID,
                request=request_ref,
                process_identity=process,
                torch_threads=(1, 1),
                modules=observed["modules"],
                native_files=observed["native_files"],
                external_native_files=external,
                landlock_abi=result["landlock_abi"],
                filesystem_policy=FILESYSTEM_POLICY,
                kernel_devices=("/dev/null", "/dev/urandom"),
                network_policy="socket_syscalls_denied",
                kernel_facts=result["kernel_facts"],
                generated_namespaces=result["generated_namespaces"],
                frozen_modules=result["frozen_modules"],
                builtin_modules=result["builtin_modules"],
                other_non_file_modules=result["other_non_file_modules"],
                stdout=streams[0],
                stderr=streams[1],
                **refs,
            )
            path = run / "observation.json"
            path.write_text(
                json.dumps(
                    observation.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            )
            ref = store.put_file(
                path,
                name=path.name,
                media_type="application/json",
                schema_version=GENESIS_RUNTIME_OBSERVATION_SCHEMA_ID,
            )
            GenesisRuntimeExecutionVerifier(store).verify_observation(request_ref, ref)
            return ref
