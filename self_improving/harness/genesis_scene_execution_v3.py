"""Revised host lineage over the unchanged, explicitly selected v2 scene runner.

Raw evidence readers grant only consistency authority. The exact producer alone
can grant a live capability, after independently verifying the revised compiler.
No legacy execution request or receipt is fabricated for the fixed child.
"""

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .genesis_runtime_manifest import read_runtime_manifest
from .genesis_scene_execution_v2 import (
    _canonical,
    _publish,
    _put,
    _read,
    _verify_captures,
    _verify_media_index,
    _verify_raw,
)
from .genesis_scene_launcher import launch_genesis_scene_replay, retain_scene_outputs_v2
from .genesis_scene_replay_batched import read_scene_replay_batched
from .schemas.genesis_scene_execution_v3 import (
    SCENE_EXECUTION_RECEIPT_V3_ID,
    SCENE_LAUNCH_RESULT_V3_ID,
    SCENE_PROCESS_OBSERVATION_V3_ID,
    GenesisSceneExecutionReceiptV3,
    GenesisSceneExecutionRequestV3,
    GenesisSceneLaunchResultV3,
    GenesisSceneProcessObservationV3,
)
from .schemas.genesis_scene_replay import GenesisSceneReplayV1, GenesisSceneReportV1


@dataclass(frozen=True)
class VerifiedGenesisSceneEvidenceV3:
    """Byte, process and mathematical consistency; no compiler or execution authority."""

    request: GenesisSceneExecutionRequestV3
    launch_result: GenesisSceneLaunchResultV3
    report: GenesisSceneReportV1
    execution_authority: bool = field(default=False, init=False)
    registered_compile_authority: bool = field(default=False, init=False)


def read_scene_run_evidence_v3(store, request_ref, launch_ref):
    """Recompute saved scene evidence; the exact compiler is a separate producer gate."""
    request = _read(store, request_ref, GenesisSceneExecutionRequestV3)
    manifest = read_runtime_manifest(store, request.runtime_manifest)
    launch = _read(store, launch_ref, GenesisSceneLaunchResultV3)
    replay = _read(store, launch.replay, GenesisSceneReplayV1)
    if (
        launch.request != request_ref
        or replay.scene != request.scene
        or replay.runtime_manifest != request.runtime_manifest
    ):
        raise ValueError("revised launch differs from requested scene/runtime")
    declared = {(member.root, member.path): member for member in manifest.members}
    for profile in ("baseline", "half_dt"):
        observation = _read(
            store, getattr(launch, profile + "_observation"), GenesisSceneProcessObservationV3
        )
        if (
            observation.request != request_ref
            or observation.profile != profile
            or observation.process_identity != manifest.process_identity
        ):
            raise ValueError("revised observation request/profile/runtime differs")
        for group in (observation.modules, observation.native_files):
            keys = tuple((member.root, member.path) for member in group)
            if keys != tuple(sorted(set(keys))) or any(
                declared.get(key) != member for key, member in zip(keys, group, strict=True)
            ):
                raise ValueError("revised observed members differ from the exact manifest")
        if ("backend", "self_improving/harness/_genesis_scene_child.py") not in {
            (member.root, member.path) for member in observation.modules
        }:
            raise ValueError("fixed scene child was not observed")
        for stream in (observation.stdout, observation.stderr):
            if stream.media_type != "text/plain" or stream.schema_version is not None:
                raise ValueError("invalid revised scene process stream")
            store.resolve(stream)
        raw_bytes = store.resolve(observation.raw_observation).path.read_bytes()
        raw = json.loads(raw_bytes)
        if raw_bytes != _canonical(raw):
            raise ValueError("noncanonical raw scene observation")
        # These immutable checks consume observations, not legacy typed requests.
        _verify_raw(observation, raw)
        _verify_captures(store, observation, raw, getattr(replay, profile))
    _verify_media_index(store, launch, replay)
    report = read_scene_replay_batched(store, launch.replay)
    return VerifiedGenesisSceneEvidenceV3(request, launch, report)


def publish_scene_run_evidence_v3(store, *, request_ref, replay_ref, output_dir):
    """Retain saved runner output with new host lineage, without issuing live authority."""
    request = _read(store, request_ref, GenesisSceneExecutionRequestV3)
    manifest = read_runtime_manifest(store, request.runtime_manifest)
    replay = _read(store, replay_ref, GenesisSceneReplayV1)
    if replay.scene != request.scene or replay.runtime_manifest != request.runtime_manifest:
        raise ValueError("saved replay differs from revised request")
    declared = {(member.root, member.path): member for member in manifest.members}
    result = {}
    for profile in ("baseline", "half_dt"):
        raw_path = output_dir / profile / "renderer-runtime.json"
        raw_bytes = raw_path.read_bytes()
        raw = json.loads(raw_bytes)
        if raw_bytes != _canonical(raw):
            raise ValueError("noncanonical saved raw scene observation")
        roots = {role: Path(path) for role, path in raw["deployment_roots"].items()}
        mapped = {}
        for field_name in ("modules", "native_files"):
            members = {}
            for item in raw[field_name]:
                path = Path(item["path"])
                matches = [
                    (role, path.relative_to(root).as_posix())
                    for role, root in roots.items()
                    if path.is_relative_to(root)
                ]
                if len(matches) != 1 or matches[0] in members:
                    raise ValueError("saved observed member needs one unique deployment mapping")
                member = declared.get(matches[0])
                if member is None or member.artifact.sha256 != item["sha256"]:
                    raise ValueError("saved observed member differs from runtime manifest")
                members[matches[0]] = member
            mapped[field_name] = tuple(members[key] for key in sorted(members))
        captures = []
        for event, frame in zip(
            raw["capture_events"], getattr(replay, profile).frames, strict=True
        ):
            if event["image_path"] != "frames/" + frame.image.name:
                raise ValueError("saved capture path differs from retained frame")
            captures.append(
                {**{k: v for k, v in event.items() if k != "image_path"}, "image": frame.image}
            )
        streams = {
            name: store.put_file(
                output_dir / (profile + "-" + name + ".txt"),
                name=profile + "-" + name + ".txt",
                media_type="text/plain",
                schema_version=None,
            )
            for name in ("stdout", "stderr")
        }
        observation = GenesisSceneProcessObservationV3(
            schema_version=SCENE_PROCESS_OBSERVATION_V3_ID,
            request=request_ref,
            profile=profile,
            process_identity=manifest.process_identity,
            environment_delta={
                "LD_LIBRARY_PATH": dict(manifest.process_identity.environment)["LD_LIBRARY_PATH"]
            },
            **mapped,
            external_native_files=(),
            torch_threads=tuple(raw["torch_threads"]),
            landlock_abi=raw["landlock_abi"],
            network_denied=raw["network_denied"],
            runtime_write_denied=raw["runtime_write_denied"],
            kernel_devices=("/dev/null", "/dev/urandom"),
            network_policy="socket_syscalls_denied",
            filesystem_policy="runtime_roots_readonly_kernel_devices_run_output_write_inherited_children",
            raw_observation=store.put_file(
                raw_path,
                name=profile + "-raw-observation.json",
                media_type="application/json",
                schema_version=None,
            ),
            captures=captures,
            **streams,
        )
        result[profile + "_observation"] = _put(
            store, observation, profile + "-observation-v3.json"
        )
        result[profile + "_preview"] = store.put_file(
            output_dir / profile / "preview.mp4",
            name=profile + "-preview.mp4",
            media_type="video/mp4",
            schema_version=None,
        )
    result["retained_index"] = retain_scene_outputs_v2(store, output_dir)
    launch = GenesisSceneLaunchResultV3(
        schema_version=SCENE_LAUNCH_RESULT_V3_ID,
        request=request_ref,
        replay=replay_ref,
        **result,
    )
    return _put(store, launch, "scene-launch-result-v3.json")


@dataclass(frozen=True)
class GenesisSceneExecutionVerifierV3:
    artifact_store: object
    compiler: object

    def validate_request(self, request_ref):
        request = _read(self.artifact_store, request_ref, GenesisSceneExecutionRequestV3)
        from .revised_image_compiler import RevisedImage2EnvGenesisCompiler

        if type(self.compiler) is not RevisedImage2EnvGenesisCompiler:
            raise TypeError("revised execution requires the exact revised compiler")
        if self.compiler.artifact_store is not self.artifact_store:
            raise ValueError("revised execution and compiler require the same CAS store instance")
        compiled, _ = self.compiler.read(request.compile_receipt)
        if compiled.scene != request.scene or compiled.qualification != request.asset_qualification:
            raise ValueError("revised execution differs from its verified compile")
        return request, read_runtime_manifest(self.artifact_store, request.runtime_manifest)

    def verify_launch(self, request_ref, launch_ref):
        self.validate_request(request_ref)
        return read_scene_run_evidence_v3(self.artifact_store, request_ref, launch_ref)

    def read_receipt(self, receipt_ref):
        receipt = _read(self.artifact_store, receipt_ref, GenesisSceneExecutionReceiptV3)
        closure = self.verify_launch(receipt.request, receipt.launch_result)
        report = _read(self.artifact_store, receipt.report, GenesisSceneReportV1)
        if report != closure.report or any(
            getattr(receipt, key) != getattr(closure.launch_result, key)
            for key in ("replay", "baseline_preview", "half_dt_preview", "retained_index")
        ):
            raise ValueError("revised receipt differs from recomputed evidence")
        return closure


_LIVE_TOKEN = object()


@dataclass(frozen=True, init=False)
class LiveGenesisSceneExecutionV3:
    receipt_ref: object
    closure: VerifiedGenesisSceneEvidenceV3

    def __init__(self, receipt_ref, closure, *, token=None):
        if token is not _LIVE_TOKEN:
            raise TypeError("live capability requires the fixed revised producer")
        object.__setattr__(self, "receipt_ref", receipt_ref)
        object.__setattr__(self, "closure", closure)


class GenesisSceneExecutionErrorV3(ValueError):
    diagnostic_ref = None


@dataclass(frozen=True)
class GenesisSceneExecutionProducerV3:
    artifact_store: object
    compiler: object
    approved_manifest: object

    def produce(self, request_ref, *, roots):
        verifier = GenesisSceneExecutionVerifierV3(self.artifact_store, self.compiler)
        launch_ref = None
        run = Path(tempfile.mkdtemp(prefix="golden-revised-scene-")) / "outputs"
        try:
            request, _ = verifier.validate_request(request_ref)
            if request.runtime_manifest != self.approved_manifest:
                raise ValueError("revised runtime is not the operator-approved manifest")
            replay_ref = launch_genesis_scene_replay(
                self.artifact_store,
                request.scene,
                request.runtime_manifest,
                roots={role: Path(path) for role, path in roots.items()},
                output_dir=run,
                runtime_version=2,
            )
            launch_ref = publish_scene_run_evidence_v3(
                self.artifact_store, request_ref=request_ref, replay_ref=replay_ref, output_dir=run
            )
            closure = verifier.verify_launch(request_ref, launch_ref)
            if closure.report.status != "passed":
                raise ValueError("revised physical scene checks did not pass")
            report_ref = _put(self.artifact_store, closure.report, "revised-scene-report.json")
            receipt = GenesisSceneExecutionReceiptV3(
                schema_version=SCENE_EXECUTION_RECEIPT_V3_ID,
                authority="observed_execution_only",
                request=request_ref,
                launch_result=launch_ref,
                report=report_ref,
                **{
                    key: getattr(closure.launch_result, key)
                    for key in ("replay", "baseline_preview", "half_dt_preview", "retained_index")
                },
            )
            ref = _put(self.artifact_store, receipt, "scene-execution-receipt-v3.json")
            return LiveGenesisSceneExecutionV3(ref, closure, token=_LIVE_TOKEN)
        except Exception as cause:
            retained = retain_scene_outputs_v2(self.artifact_store, run) if run.exists() else None
            payload = dict(
                error_type=type(cause).__name__,
                message=str(cause),
                request=request_ref.model_dump(mode="json"),
                launch_result=launch_ref.model_dump(mode="json") if launch_ref else None,
                retained_index=retained.model_dump(mode="json") if retained else None,
                failure_artifacts=[
                    ref.model_dump(mode="json") for ref in getattr(cause, "failure_artifacts", ())
                ],
            )
            error = GenesisSceneExecutionErrorV3(str(cause))
            error.diagnostic_ref = _publish(
                self.artifact_store, _canonical(payload), "scene-execution-failure-v3.json", None
            )
            raise error from cause
