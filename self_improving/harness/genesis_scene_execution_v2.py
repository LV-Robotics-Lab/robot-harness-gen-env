"""Versioned scene execution with actual capture events and addressable preview closure."""

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .genesis_runtime_manifest import read_runtime_manifest
from .genesis_scene_replay_batched import read_scene_replay_batched
from .schemas.common import ArtifactRef
from .schemas.genesis_scene_execution_v2 import (
    SCENE_EXECUTION_RECEIPT_V2_ID,
    GenesisSceneCaptureEventV2,
    GenesisSceneExecutionReceiptV2,
    GenesisSceneExecutionRequestV2,
    GenesisSceneLaunchResultV2,
    GenesisSceneProcessObservationV2,
)
from .schemas.genesis_scene_replay import GenesisSceneReplayV1, GenesisSceneReportV1


def _canonical(value):
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _read(store, ref, model):
    if (
        ref.uri != f"artifact://sha256/{ref.sha256}"
        or ref.media_type != "application/json"
        or ref.schema_version != model.model_config["json_schema_extra"]["$id"]
    ):
        raise ValueError("unexpected scene evidence schema or locator")
    payload = store.resolve(ref).path.read_bytes()
    value = model.model_validate_json(payload)
    if payload != _canonical(value):
        raise ValueError("noncanonical scene evidence")
    return value


def _put(store, value, name):
    return _publish(store, _canonical(value), name, value.schema_version)


def _publish(store, payload, name, schema):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_bytes(payload)
        return store.put_file(path, name=name, media_type="application/json", schema_version=schema)


@dataclass(frozen=True)
class VerifiedGenesisSceneClosureV2:
    request: GenesisSceneExecutionRequestV2
    launch_result: GenesisSceneLaunchResultV2
    report: GenesisSceneReportV1
    execution_authority: bool = field(default=False, init=False)


_LIVE_TOKEN = object()


@dataclass(frozen=True, init=False)
class LiveGenesisSceneExecutionV2:
    receipt_ref: object
    closure: VerifiedGenesisSceneClosureV2

    def __init__(self, receipt_ref, closure, *, token=None):
        if token is not _LIVE_TOKEN:
            raise TypeError("live capability can only originate from the fixed producer")
        object.__setattr__(self, "receipt_ref", receipt_ref)
        object.__setattr__(self, "closure", closure)


@dataclass(frozen=True)
class GenesisSceneExecutionVerifierV2:
    artifact_store: object
    compiler: object

    def validate_request(self, request_ref):
        request = _read(self.artifact_store, request_ref, GenesisSceneExecutionRequestV2)
        from .image2env_genesis_compile import Image2EnvGenesisCompiler

        if type(self.compiler) is not Image2EnvGenesisCompiler:
            raise TypeError("scene execution requires the trusted native compiler")
        compiled, _ = self.compiler.read(request.compile_receipt)
        if compiled.scene != request.scene or compiled.qualification != request.asset_qualification:
            raise ValueError("scene request differs from trusted native compile receipt")
        manifest = read_runtime_manifest(self.artifact_store, request.runtime_manifest)
        return request, manifest

    def verify_launch(self, request_ref, launch_ref):
        request, manifest = self.validate_request(request_ref)
        launch = _read(self.artifact_store, launch_ref, GenesisSceneLaunchResultV2)
        if launch.request != request_ref:
            raise ValueError("launch belongs to another scene request")
        replay = _read(self.artifact_store, launch.replay, GenesisSceneReplayV1)
        if replay.scene != request.scene or replay.runtime_manifest != request.runtime_manifest:
            raise ValueError("scene replay differs from requested scene/runtime")
        declared = {(member.root, member.path): member for member in manifest.members}
        for profile, observation_ref in (
            ("baseline", launch.baseline_observation),
            ("half_dt", launch.half_dt_observation),
        ):
            observation = _read(
                self.artifact_store, observation_ref, GenesisSceneProcessObservationV2
            )
            if observation.request != request_ref or observation.profile != profile:
                raise ValueError("observation belongs to another request/profile")
            if observation.process_identity != manifest.process_identity:
                raise ValueError("observed process differs from declared runtime")
            for group in (observation.modules, observation.native_files):
                keys = tuple((member.root, member.path) for member in group)
                if keys != tuple(sorted(set(keys))):
                    raise ValueError("observed members must be sorted and unique")
                if any(
                    declared.get(key) != member for key, member in zip(keys, group, strict=True)
                ):
                    raise ValueError("observed member differs from runtime manifest")
            child = ("backend", "self_improving/harness/_genesis_scene_child.py")
            if child not in {(member.root, member.path) for member in observation.modules}:
                raise ValueError("fixed scene child was not observed")
            for stream in (observation.stdout, observation.stderr):
                if stream.media_type != "text/plain" or stream.schema_version is not None:
                    raise ValueError("invalid scene process stream")
                self.artifact_store.resolve(stream)
            raw_bytes = self.artifact_store.resolve(observation.raw_observation).path.read_bytes()
            raw = json.loads(raw_bytes)
            if raw_bytes != _canonical(raw):
                raise ValueError("noncanonical raw scene observation")
            _verify_raw(observation, raw)
            _verify_captures(self.artifact_store, observation, raw, getattr(replay, profile))
        _verify_media_index(self.artifact_store, launch, replay)
        report = read_scene_replay_batched(self.artifact_store, launch.replay)
        return VerifiedGenesisSceneClosureV2(request, launch, report)

    def read_receipt(self, receipt_ref):
        receipt = _read(self.artifact_store, receipt_ref, GenesisSceneExecutionReceiptV2)
        closure = self.verify_launch(receipt.request, receipt.launch_result)
        report = _read(self.artifact_store, receipt.report, GenesisSceneReportV1)
        if (
            receipt.replay != closure.launch_result.replay
            or report != closure.report
            or any(
                getattr(receipt, key) != getattr(closure.launch_result, key)
                for key in ("baseline_preview", "half_dt_preview", "retained_index")
            )
        ):
            raise ValueError("scene receipt differs from recomputed replay/report")
        return closure


class GenesisSceneExecutionError(ValueError):
    diagnostic_ref = None


def _verify_captures(store, observation, raw, profile):
    from .genesis_scene_replay import profile_settings

    dt, _, _ = profile_settings(observation.profile)
    if raw["schema_version"] != "harness.genesis_scene_raw_observation.v2":
        raise ValueError("capture observations require the exact raw v2 schema")
    events = raw["capture_events"]
    if len(events) != 41:
        raise ValueError("raw scene capture sequence must contain all 41 events")
    previous = None
    for capture, event, frame in zip(observation.captures, events, profile.frames, strict=True):
        image_path = Path(event["image_path"])
        if (
            image_path.is_absolute()
            or ".." in image_path.parts
            or image_path.as_posix() != event["image_path"]
            or image_path.as_posix() != "frames/" + frame.image.name
        ):
            raise ValueError("capture image path differs from the actual frame")
        raw_capture = GenesisSceneCaptureEventV2.model_validate(
            {
                **{key: value for key, value in event.items() if key != "image_path"},
                "image": frame.image,
            }
        )
        if (
            raw_capture != capture
            or capture.step != frame.step
            or abs(capture.time_s - frame.step * dt) > 1e-12
            or (previous is not None and capture.captured_at < previous)
        ):
            raise ValueError("actual capture event differs from typed frame/time lineage")
        previous = capture.captured_at
        with Image.open(store.resolve(capture.image).path) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size != (capture.width, capture.height)
                or hashlib.sha256(image.tobytes()).hexdigest() != capture.pixels_sha256
            ):
                raise ValueError("capture pixel identity differs from actual PNG")


def read_retained_scene_index(store, index_ref):
    """Read exact retained bytes in canonical POSIX logical-string order."""
    data = store.resolve(index_ref).path.read_bytes()
    index = json.loads(data)
    if (
        data != _canonical(index)
        or set(index) != {"files", "execution_authority", "qualified"}
        or index["execution_authority"] is not False
        or index["qualified"] is not False
    ):
        raise ValueError("retained scene index must be canonical non-authoritative evidence")
    refs = tuple(ArtifactRef.model_validate(item) for item in index["files"])
    names = tuple(ref.name for ref in refs)
    if names != tuple(sorted(set(names))):
        raise ValueError("retained scene members must be sorted and unique")
    members = {}
    for ref in refs:
        path = Path(ref.name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != ref.name:
            raise ValueError("retained scene member path must be canonical and relative")
        store.resolve(ref)
        members[ref.name] = (ref.sha256, ref.bytes)
    return members


def _verify_media_index(store, launch, replay):
    members = read_retained_scene_index(store, launch.retained_index)
    for profile in ("baseline", "half_dt"):
        evidence = getattr(replay, profile)
        preview = getattr(launch, profile + "_preview")
        expected = {
            profile + "/preview.mp4": preview,
            profile + "/simulation.mkv": evidence.video,
            profile + "/trace.ndjson": evidence.rows,
            profile + "/loaded.json": evidence.loaded_scene,
            **{profile + "/frames/" + frame.image.name: frame.image for frame in evidence.frames},
        }
        if any(members.get(name) != (ref.sha256, ref.bytes) for name, ref in expected.items()):
            raise ValueError(
                "retained index does not bind the complete profile media/trace closure"
            )
        path = store.resolve(preview).path
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        frames = json.loads(probe.stdout)["frames"]
        if len(frames) != 41 or any(
            abs(float(frame["best_effort_timestamp_time"]) - index / 10) > 1e-9
            or frame["width"] != 640
            or frame["height"] != 480
            for index, frame in enumerate(frames)
        ):
            raise ValueError("browser preview does not retain the actual 41-frame timing/geometry")


def _verify_raw(observation, raw):
    roots = {role: Path(path) for role, path in raw["deployment_roots"].items()}
    if set(roots) != {"backend", "distributions", "genesis", "interpreter", "native", "stdlib"}:
        raise ValueError("raw deployment must identify exactly six roots")
    if any(
        not path.is_absolute() or str(path) != raw["deployment_roots"][role] or ".." in path.parts
        for role, path in roots.items()
    ):
        raise ValueError("raw deployment locators must be canonical absolute paths")
    for field_name in ("modules", "native_files"):
        mapped = {}
        for item in raw[field_name]:
            path = Path(item["path"])
            matching = [
                (role, path.relative_to(root).as_posix())
                for role, root in roots.items()
                if path.is_relative_to(root)
            ]
            if len(matching) != 1 or str(path) != item["path"] or ".." in path.parts:
                raise ValueError("raw member does not have one canonical deployment mapping")
            if matching[0] in mapped:
                raise ValueError("duplicate raw observed member")
            mapped[matching[0]] = item["sha256"]
        expected = {
            (member.root, member.path): member.artifact.sha256
            for member in getattr(observation, field_name)
        }
        if mapped != expected:
            raise ValueError("raw and typed observed members differ")
    identity = observation.process_identity.model_dump(mode="json")
    environment = dict(identity.pop("environment"))
    if raw["process_identity"] != identity:
        raise ValueError("raw and typed process identities differ")
    run = Path(raw["environment"]["TMPDIR"]).parent
    substitutions = {"${" + role.upper() + "}": str(path) for role, path in roots.items()}
    substitutions.update({"${RUN}": str(run), "${USER_HOME}": str(run / "home")})
    for token, value in substitutions.items():
        environment = {key: item.replace(token, value) for key, item in environment.items()}
    startup = dict(environment)
    startup["LD_LIBRARY_PATH"] = str(roots["native"] / "usr/lib/x86_64-linux-gnu")
    expected_delta = {
        "LD_LIBRARY_PATH": dict(observation.process_identity.environment)["LD_LIBRARY_PATH"]
    }
    if (
        raw["environment"] != environment
        or raw["startup_environment"] != startup
        or observation.environment_delta != expected_delta
    ):
        raise ValueError("raw scene process environment transition differs")
    for field_name in ("landlock_abi", "network_denied", "runtime_write_denied", "torch_threads"):
        expected = getattr(observation, field_name)
        if field_name == "torch_threads":
            expected = list(expected)
        if raw[field_name] != expected or type(raw[field_name]) is not type(expected):
            raise ValueError("raw confinement or thread observation differs")


@dataclass(frozen=True)
class GenesisSceneExecutionProducerV2:
    artifact_store: object
    compiler: object
    approved_manifest: object

    def produce(self, request_ref, *, roots):
        verifier = GenesisSceneExecutionVerifierV2(self.artifact_store, self.compiler)
        launch_ref = None
        try:
            request, _ = verifier.validate_request(request_ref)
            if request.runtime_manifest != self.approved_manifest:
                raise ValueError("scene runtime is not the operator-approved manifest")
            from .genesis_scene_launcher import execute_genesis_scene_request_v2

            launch_ref = execute_genesis_scene_request_v2(
                store=self.artifact_store, request_ref=request_ref, roots=roots
            )
            closure = verifier.verify_launch(request_ref, launch_ref)
            if closure.report.status != "passed":
                raise ValueError("scene physical validation did not pass")
            report_ref = _put(self.artifact_store, closure.report, "scene-report.json")
            receipt = GenesisSceneExecutionReceiptV2(
                schema_version=SCENE_EXECUTION_RECEIPT_V2_ID,
                authority="observed_execution_only",
                request=request_ref,
                launch_result=launch_ref,
                replay=closure.launch_result.replay,
                report=report_ref,
                baseline_preview=closure.launch_result.baseline_preview,
                half_dt_preview=closure.launch_result.half_dt_preview,
                retained_index=closure.launch_result.retained_index,
            )
            receipt_ref = _put(self.artifact_store, receipt, "scene-execution-receipt.json")
            return LiveGenesisSceneExecutionV2(receipt_ref, closure, token=_LIVE_TOKEN)
        except Exception as error:
            diagnostic = getattr(error, "diagnostic_ref", None)
            payload = dict(
                error_type=type(error).__name__,
                message=str(error),
                request=request_ref.model_dump(mode="json"),
                launch_result=launch_ref.model_dump(mode="json") if launch_ref else None,
                cause_diagnostic=diagnostic.model_dump(mode="json") if diagnostic else None,
                failure_artifacts=[
                    ref.model_dump(mode="json") for ref in getattr(error, "failure_artifacts", ())
                ],
            )
            failure = GenesisSceneExecutionError(str(error))
            failure.diagnostic_ref = _publish(
                self.artifact_store, _canonical(payload), "scene-execution-failure.json", None
            )
            raise failure from error
