"""Fixed fresh camera execution, separately sealed from historical scene replay."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from PIL import Image

from .genesis_runtime_manifest import read_runtime_manifest
from .genesis_scene_application_v2 import GenesisSceneApplication
from .genesis_scene_assessment import GenesisSceneAssessment
from .genesis_scene_execution_v2 import (
    _canonical,
    _publish,
    _put,
    _read,
    _verify_raw,
    read_retained_scene_index,
)
from .schemas.genesis_fresh_capture import (
    FRESH_CAPTURE_RECEIPT_ID,
    FRESH_CAPTURE_REQUEST_ID,
    GenesisFreshCaptureInputV1,
    GenesisFreshCaptureReceiptV1,
    GenesisFreshCaptureRequestV1,
    GenesisFreshCaptureResultV1,
)
from .schemas.genesis_scene_replay import (
    GenesisSceneBodyStateV1,
    GenesisSceneReplayV1,
    GenesisSceneSpecV1,
    GenesisSceneTraceRowV1,
)


class GenesisFreshCaptureError(RuntimeError):
    diagnostic_ref = None


_LIVE = object()


@dataclass(frozen=True, init=False)
class LiveGenesisFreshCapture:
    receipt_ref: object

    def __init__(self, receipt_ref, *, token=None):
        if token is not _LIVE:
            raise TypeError("fresh live capability requires actual fixed capture execution")
        object.__setattr__(self, "receipt_ref", receipt_ref)


@dataclass(frozen=True)
class VerifiedFreshCapture:
    receipt: GenesisFreshCaptureReceiptV1
    result: GenesisFreshCaptureResultV1
    execution_authority: bool = field(default=False, init=False)


@dataclass(frozen=True)
class GenesisFreshCaptureProducer:
    artifact_store: object
    replay_application: GenesisSceneApplication
    approved_manifest: object

    def __post_init__(self):
        if type(self.replay_application) is not GenesisSceneApplication:
            raise TypeError("fresh capture requires the exact trusted replay application")
        if self.replay_application.producer.artifact_store is not self.artifact_store:
            raise ValueError("fresh capture and replay must share the configured CAS")

    def source_request(self, run_id):
        """Deeply resolve a registered replay before capturing a new camera event."""
        receipt_ref, receipt, closure, _ = GenesisSceneAssessment(
            self.replay_application
        ).read_replay(run_id)
        replay = _read(self.artifact_store, receipt.replay, GenesisSceneReplayV1)
        data = self.artifact_store.resolve(replay.half_dt.rows).path.read_bytes()
        row = GenesisSceneTraceRowV1.model_validate_json(data.splitlines()[-1])
        if row.step != 2000 or row.time_s != 4.0:
            raise ValueError("fresh capture requires the exact final half-dt row")
        return GenesisFreshCaptureRequestV1(
            schema_version=FRESH_CAPTURE_REQUEST_ID,
            replay_run_id=run_id,
            replay_receipt=receipt_ref,
            scene=closure.request.scene,
            half_dt_rows=replay.half_dt.rows,
            final_state=row.objects["object"],
            source_step=2000,
            source_time_s=4.0,
            capture_runtime_manifest=self.approved_manifest,
            restoration="restored_final_pose_zero_velocity",
        )

    def verify_result(self, request_ref, result_ref):
        """Verify capture bytes and process observations, without granting live authority."""
        request = _read(self.artifact_store, request_ref, GenesisFreshCaptureRequestV1)
        if request.capture_runtime_manifest != self.approved_manifest:
            raise ValueError("fresh capture runtime differs from operator approval")
        result = _read(self.artifact_store, result_ref, GenesisFreshCaptureResultV1)
        if result.request != request_ref:
            raise ValueError("capture result belongs to another request")
        manifest = read_runtime_manifest(self.artifact_store, self.approved_manifest)
        if result.process_identity != manifest.process_identity:
            raise ValueError("capture process identity differs from approved runtime")
        declared = {(member.root, member.path): member for member in manifest.members}
        for members in (result.modules, result.native_files):
            keys = tuple((member.root, member.path) for member in members)
            if keys != tuple(sorted(set(keys))) or any(
                declared.get((member.root, member.path)) != member for member in members
            ):
                raise ValueError("capture observed members differ from declared runtime")
        if ("backend", "self_improving/harness/_genesis_scene_fresh_capture_child.py") not in {
            (member.root, member.path) for member in result.modules
        }:
            raise ValueError("fixed fresh capture child was not observed")
        for stream in (result.stdout, result.stderr):
            if stream.media_type != "text/plain" or stream.schema_version is not None:
                raise ValueError("fresh capture requires raw process text streams")
        raw_bytes = self.artifact_store.resolve(result.raw_observation).path.read_bytes()
        raw = json.loads(raw_bytes)
        if raw_bytes != _canonical(raw):
            raise ValueError("fresh process observation must be canonical")
        _verify_raw(result, raw)
        scene = _read(self.artifact_store, request.scene, GenesisSceneSpecV1)
        loaded = json.loads(self.artifact_store.resolve(result.raw_load).path.read_bytes())
        verify_restored_capture(scene, request, result, loaded, raw)
        image_path = self.artifact_store.resolve(result.image).path
        with Image.open(image_path) as image:
            image.load()
            if (
                image.format != "PNG"
                or image.mode != "RGB"
                or image.size != (result.width, result.height)
                or hashlib.sha256(image.tobytes()).hexdigest() != result.pixels_sha256
            ):
                raise ValueError("fresh capture pixels differ from actual PNG bytes")
        for ref in (result.raw_load, result.stdout, result.stderr, result.retained_index):
            self.artifact_store.resolve(ref)
        members = read_retained_scene_index(self.artifact_store, result.retained_index)
        for name, ref in (
            ("capture/fresh.png", result.image),
            ("capture/loaded.json", result.raw_load),
            ("capture/renderer-runtime.json", result.raw_observation),
        ):
            if members.get(name) != (ref.sha256, ref.bytes):
                raise ValueError("retained capture index differs from direct evidence")
        return request, result

    def produce(self, parameters, *, roots):
        request_ref = result_ref = None
        try:
            if type(parameters) is not GenesisFreshCaptureInputV1:
                raise TypeError("fresh capture requires exact v3 input")
            request = self.source_request(parameters.replay_run_id)
            request_ref = _put(self.artifact_store, request, "fresh-capture-request.json")
            from .genesis_scene_fresh_capture_launcher import execute_genesis_fresh_capture

            result_ref = execute_genesis_fresh_capture(
                store=self.artifact_store, request_ref=request_ref, roots=roots
            )
            _, result = self.verify_result(request_ref, result_ref)
            checked_at = datetime.now(timezone.utc)
            receipt = GenesisFreshCaptureReceiptV1(
                schema_version=FRESH_CAPTURE_RECEIPT_ID,
                replay_run_id=parameters.replay_run_id,
                replay_receipt=request.replay_receipt,
                request=request_ref,
                capture_result=result_ref,
                image=result.image,
                captured_at=result.captured_at,
                checked_at=checked_at,
                expires_at=result.captured_at + timedelta(seconds=parameters.ttl_seconds),
                ttl_seconds=parameters.ttl_seconds,
                authority="observed_restored_final_pose",
                process_continuation=False,
            )
            ref = _put(self.artifact_store, receipt, "fresh-capture-receipt.json")
            return LiveGenesisFreshCapture(ref, token=_LIVE)
        except Exception as error:
            failure = GenesisFreshCaptureError(str(error))
            failure.diagnostic_ref = _publish(
                self.artifact_store,
                _canonical(
                    dict(
                        error_type=type(error).__name__,
                        message=str(error),
                        request=request_ref.model_dump(mode="json") if request_ref else None,
                        capture_result=result_ref.model_dump(mode="json") if result_ref else None,
                        failure_artifacts=[
                            ref.model_dump(mode="json")
                            for ref in getattr(error, "failure_artifacts", ())
                        ],
                    )
                ),
                "fresh-capture-failure.json",
                None,
            )
            raise failure from error

    def read_receipt(self, receipt_ref):
        """Rebuild historical closure; this never creates a live execution capability."""
        receipt = _read(self.artifact_store, receipt_ref, GenesisFreshCaptureReceiptV1)
        expected = self.source_request(receipt.replay_run_id)
        request, result = self.verify_result(receipt.request, receipt.capture_result)
        if (
            request != expected
            or receipt.replay_receipt != request.replay_receipt
            or receipt.image != result.image
            or receipt.captured_at != result.captured_at
        ):
            raise ValueError("fresh observation receipt differs from replay/capture lineage")
        return VerifiedFreshCapture(receipt, result)


def verify_restored_capture(scene, request, result, loaded, raw):
    """Pure consistency checks for restored pose and the actual new camera event."""
    expected = dict(
        mode="restored_final_state",
        original_process_continues=False,
        physics_steps=0,
        restore_velocity_policy="zero",
        pose_coordinate_frame="genesis_entity_user_frame",
        requested_source_state=request.final_state.model_dump(mode="json"),
        representation=scene.representation.model_dump(mode="json"),
        table_center_m=list(scene.table_center_m),
        table_size_m=list(scene.table_size_m),
        object_dofs=6,
        table_fixed=True,
        ground_fixed=True,
    )
    if any(
        loaded.get(key) != value or type(loaded.get(key)) is not type(value)
        for key, value in expected.items()
    ):
        raise ValueError("fresh raw load differs from source/restoration contract")
    actual = GenesisSceneBodyStateV1.model_validate(loaded["actual_restored_state"])
    if (
        max(abs(a - b) for a, b in zip(actual.position, request.final_state.position, strict=True))
        > 1e-6
        or min(
            sum(
                (a - sign * b) ** 2
                for a, b in zip(
                    actual.orientation_wxyz, request.final_state.orientation_wxyz, strict=True
                )
            )
            for sign in (-1, 1)
        )
        > 1e-10
        or any(actual.velocity)
        or any(actual.angular_velocity)
    ):
        raise ValueError("actual restored state differs from final pose and zero velocity")
    if set(loaded["collision_shapes"]) != {"ground", "object", "table"} or any(
        type(count) is not int or count <= 0 for count in loaded["collision_shapes"].values()
    ):
        raise ValueError("restored scene must load all three collision bodies")
    center = scene.table_center_m
    top = center[2] + scene.table_size_m[2] / 2
    camera = dict(
        width=640,
        height=480,
        position=[center[0] + 0.6, center[1] - 0.7, top + 0.5],
        lookat=[center[0], center[1], top + 0.02],
        fov_degrees=40,
    )
    if loaded["camera"] != camera or raw["capture_event"]["camera"] != camera:
        raise ValueError("fresh camera differs from the fixed scene view")
    capture = raw["capture_event"]
    if (
        raw["schema_version"] != "harness.genesis_fresh_capture_raw.v1"
        or raw["request"] != result.request.model_dump(mode="json")
        or datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
        != result.captured_at
        or capture["pixels_sha256"] != result.pixels_sha256
        or capture["width"] != result.width
        or capture["height"] != result.height
        or capture["image_path"] != "fresh.png"
    ):
        raise ValueError("raw capture event differs from typed camera evidence")
